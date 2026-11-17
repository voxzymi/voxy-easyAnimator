"""Manifest: the only format shared between the AE host and the ML service.

``validate_manifest`` runs the JSON Schema and then the rules a schema cannot
express (group/class mapping, one Hair part, ordering, skipped parts, files).
The service validates every response before sending it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import jsonschema

from . import schema as S

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "assets", "manifest.schema.json")


class ManifestError(ValueError):
    """The manifest does not follow the documented format."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
        self.problems = problems


@lru_cache(maxsize=1)
def _validator() -> jsonschema.Draft202012Validator:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return jsonschema.Draft202012Validator(json.load(f))


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_manifest(job_id: str, job_type: str, *, parent_job_id: Optional[str] = None,
                 output_dir: Optional[str] = None, source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "schema_version": S.MANIFEST_SCHEMA_VERSION,
        "job": {"id": job_id, "type": job_type, "status": "ok", "created_at": now_iso(), "finished_at": None,
                "parent_job_id": parent_job_id, "output_dir": output_dir},
        "source": source,
        "models": {},
        "parts": [],
        "groups": [],
        "skipped": [],
        "keypoints": [],
        "hair": {"subdivision": {"status": "not_run", "count": 0}},
        "background": None,
        "upscale": None,
        "errors": [],
        "warnings": [],
        "timings_ms": {},
    }


def rebuild_groups(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Groups in the fixed group order, listing part ids in the order parts appear."""
    groups = []
    for g in S.GROUP_ORDER:
        ids = [p["id"] for p in parts if p["group"] == g]
        if ids:
            groups.append({"name": g, "parts": ids})
    return groups


def sort_parts(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Canonical manifest order: group order, then class order, then id for subdivisions."""
    def key(p: Dict[str, Any]):
        cls = p.get("class") or p["id"]
        try:
            ck = S.manifest_order_key(cls)
        except (KeyError, ValueError):
            ck = (S.GROUP_ORDER.index(p["group"]), 999)
        return (ck[0], ck[1], p["id"])
    return sorted(parts, key=key)


def finalize(manifest: Dict[str, Any], status: Optional[str] = None) -> Dict[str, Any]:
    manifest["parts"] = sort_parts(manifest["parts"])
    manifest["groups"] = rebuild_groups(manifest["parts"])
    manifest["job"]["finished_at"] = now_iso()
    if status:
        manifest["job"]["status"] = status
    return manifest


def validate_manifest(m: Dict[str, Any], *, base_dir: Optional[str] = None) -> None:
    """Raise ManifestError listing every problem found. ``base_dir`` enables file checks."""
    problems: List[str] = []
    for err in sorted(_validator().iter_errors(m), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        problems.append(f"{loc}: {err.message}")
    if problems:
        raise ManifestError(problems)

    job = m["job"]
    parts = m["parts"]
    ids = [p["id"] for p in parts]
    if len(set(ids)) != len(ids):
        problems.append("part ids are not unique")

    for p in parts:
        cls = p.get("class")
        if p["kind"] in ("visible", "hidden") and cls is not None:
            if cls not in S.GROUP_OF_CLASS:
                problems.append(f"part {p['id']}: unknown class {cls}")
            elif S.GROUP_OF_CLASS[cls] != p["group"]:
                problems.append(f"part {p['id']}: group {p['group']} does not match class {cls} ({S.GROUP_OF_CLASS[cls]})")
        if p["kind"] == "hidden" and p["group"] != "Hidden":
            problems.append(f"part {p['id']}: hidden parts must be in the Hidden group")
        if p["kind"] == "subdivision":
            if p["group"] != "Hair":
                problems.append(f"part {p['id']}: subdivisions must be in the Hair group")
            if not p.get("parent"):
                problems.append(f"part {p['id']}: subdivision needs a parent")
        x, y, w, h = p["bbox"]
        if w < 1 or h < 1:
            problems.append(f"part {p['id']}: empty bbox")
        if p["pixel_count"] < 1:
            problems.append(f"part {p['id']}: parts with no pixels must be listed in 'skipped'")
        if not p["outline"]:
            problems.append(f"part {p['id']}: no outline")

    # Group list must match parts, in manifest order, in group order, with no empty groups.
    expected = rebuild_groups(parts)
    if m["groups"] != expected:
        problems.append("groups do not match the parts list (order or membership)")
    group_names = [g["name"] for g in m["groups"]]
    if group_names != [g for g in S.GROUP_ORDER if g in group_names]:
        problems.append("groups are not in the documented group order")

    # Hair rules.
    hair_parts = [p for p in parts if p["group"] == "Hair"]
    subs = [p for p in hair_parts if p["kind"] == "subdivision"]
    singles = [p for p in hair_parts if p["kind"] != "subdivision"]
    if job["type"] in ("make_layers", "combine_hair"):
        if subs:
            problems.append(f"{job['type']} must not contain hair subdivisions")
        if len(singles) > 1:
            problems.append("the Hair group must contain exactly one part")
    if subs and singles:
        problems.append("a Hair group cannot hold both subdivisions and the single Hair part")
    sub_status = m["hair"]["subdivision"]["status"]
    if sub_status == "ok" and not subs:
        problems.append("hair.subdivision.status is ok but there are no subdivisions")
    if sub_status == "failed" and subs:
        problems.append("hair.subdivision.status is failed but subdivisions are present")
    if subs:
        pivots = {tuple(p["pivot"]) for p in subs}
        if len(pivots) != 1:
            problems.append("hair subdivisions must share one root pivot")

    skipped_ids = {s["id"] for s in m["skipped"]}
    if skipped_ids & set(ids):
        problems.append("a part is listed both in parts and in skipped")

    if job["status"] == "failed" and job["type"] == "make_layers" and parts:
        problems.append("a failed make_layers job must show no parts")
    if job["status"] == "failed" and not m["errors"]:
        problems.append("a failed job must carry at least one error")

    if base_dir is not None:
        for p in parts:
            for key in ("image", "mask"):
                if not os.path.isfile(os.path.join(base_dir, p[key])):
                    problems.append(f"part {p['id']}: missing file {p[key]}")
    if problems:
        raise ManifestError(problems)
