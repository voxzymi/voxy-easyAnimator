"""Job queue: one job at a time (the GPU is shared), with cancellation and history."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .cancel import CancelToken
from .errors import Cancelled, VoxyError, error_dict
from .manifest import finalize, new_manifest
from .models.registry import ModelRegistry
from .pipeline import HANDLERS, Ctx
from .upscale import Upscaler

log = logging.getLogger("voxy.jobs")


class Job:
    def __init__(self, job_id: str, kind: str, job_dir: str):
        self.id = job_id
        self.kind = kind
        self.dir = job_dir
        self.status = "queued"          # queued | running | done | cancelled
        self.step = ""
        self.cancel = CancelToken()
        self.manifest: Optional[Dict[str, Any]] = None
        self.created = time.time()
        self.event = threading.Event()

    def public(self, with_manifest: bool = True) -> Dict[str, Any]:
        d = {"job_id": self.id, "type": self.kind, "status": self.status, "step": self.step}
        if with_manifest and self.manifest is not None:
            d["manifest"] = self.manifest
        return d


class JobManager:
    def __init__(self, cfg: Dict[str, Any], registry: ModelRegistry, app_dir: str, data_dir: str,
                 upscaler: Optional[Upscaler] = None):
        self.cfg, self.registry, self.app_dir, self.data_dir = cfg, registry, app_dir, data_dir
        self.upscaler = upscaler or Upscaler(cfg["upscale"], app_dir)
        self.jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxy-job")
        self.jobs_root = os.path.join(data_dir, "jobs")
        self.tmp_root = os.path.join(data_dir, "tmp", "upscale")
        os.makedirs(self.jobs_root, exist_ok=True)
        self.cleanup_stale()

    # ---- folders
    def job_dir(self, job_id: str) -> Optional[str]:
        for root in (self.jobs_root, self.tmp_root):
            d = os.path.join(root, job_id)
            if os.path.isdir(d) and os.path.basename(d) == job_id and job_id.isalnum():
                return d
        return None

    def _new_dir(self, kind: str, job_id: str) -> str:
        root = self.tmp_root if kind == "upscale" else self.jobs_root
        d = os.path.join(root, job_id)
        os.makedirs(d, exist_ok=True)
        return d

    # ---- lifecycle
    def submit(self, kind: str, req: Dict[str, Any]) -> Job:
        if kind not in HANDLERS:
            raise VoxyError("BAD_REQUEST", f"unknown job type {kind}")
        job_id = uuid.uuid4().hex[:16]
        job = Job(job_id, kind, self._new_dir(kind, job_id))
        with self._lock:
            self.jobs[job_id] = job
        self._pool.submit(self._run, job, req)
        return job

    def _run(self, job: Job, req: Dict[str, Any]) -> None:
        if job.cancel.cancelled:
            self._finish_cancelled(job)
            return
        job.status = "running"
        ctx = Ctx(cfg=self.cfg, registry=self.registry, app_dir=self.app_dir, data_dir=self.data_dir, job_id=job.id,
                  job_dir=job.dir, cancel=job.cancel, resolve_job_dir=self.job_dir, upscaler=self.upscaler,
                  on_step=lambda s: setattr(job, "step", s))
        try:
            job.manifest = HANDLERS[job.kind](ctx, req)
        except BaseException as e:  # noqa: BLE001 - last line of defence: never lose a job
            log.error("job %s crashed:\n%s", job.id, traceback.format_exc())
            m = new_manifest(job.id, job.kind, output_dir=job.dir)
            m["errors"].append(error_dict("INTERNAL_ERROR", f"{type(e).__name__}: {e}", stage=job.step))
            job.manifest = finalize(m, "failed")
        job.status = "cancelled" if job.manifest["job"]["status"] == "cancelled" else "done"
        job.step = ""
        job.event.set()
        self.prune()

    def _finish_cancelled(self, job: Job) -> None:
        m = new_manifest(job.id, job.kind, output_dir=job.dir)
        m["errors"].append(Cancelled().to_dict())
        job.manifest = finalize(m, "cancelled")
        job.status = "cancelled"
        job.event.set()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self.jobs.get(job_id)

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Optional[Job]:
        job = self.get(job_id)
        if job is not None:
            job.event.wait(timeout)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel.cancel()
        return True

    def busy(self) -> bool:
        with self._lock:
            return any(j.status in ("queued", "running") for j in self.jobs.values())

    # ---- temporary files
    def discard(self, job_id: str) -> bool:
        """Delete a job's folder (used for Discard on an upscale result)."""
        d = self.job_dir(job_id)
        if not d:
            return False
        shutil.rmtree(d, ignore_errors=True)
        with self._lock:
            self.jobs.pop(job_id, None)
        return True

    def cleanup_tmp(self) -> int:
        """Delete every temporary upscale result (called when a project closes and at start-up)."""
        n = 0
        if os.path.isdir(self.tmp_root):
            for name in os.listdir(self.tmp_root):
                shutil.rmtree(os.path.join(self.tmp_root, name), ignore_errors=True)
                n += 1
        with self._lock:
            for jid in [j for j, job in self.jobs.items() if job.kind == "upscale" and job.status in ("done", "cancelled")]:
                self.jobs.pop(jid, None)
        return n

    def cleanup_stale(self) -> None:
        self.cleanup_tmp()
        keep = int(self.cfg["service"]["max_job_history"])
        dirs = sorted((os.path.join(self.jobs_root, d) for d in os.listdir(self.jobs_root)), key=os.path.getmtime)
        for d in dirs[:-keep] if len(dirs) > keep else []:
            shutil.rmtree(d, ignore_errors=True)

    def prune(self) -> None:
        keep = int(self.cfg["service"]["max_job_history"])
        with self._lock:
            done = [j for j in self.jobs.values() if j.status in ("done", "cancelled") and j.kind != "upscale"]
            done.sort(key=lambda j: j.created)
            for j in done[:-keep] if len(done) > keep else []:
                shutil.rmtree(j.dir, ignore_errors=True)
                self.jobs.pop(j.id, None)
