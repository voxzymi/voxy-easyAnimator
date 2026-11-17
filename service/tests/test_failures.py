"""The failure rules from the spec:

* general model fails      -> Make Layers reports the failure and shows no parts.
* subdivision model fails  -> the single Hair part from Make Layers is still there and the
                              manifest records the failure.
"""
import json
import os
import time

import numpy as np
import pytest

from conftest import run_job
from voxy_service.manifest import validate_manifest
from voxy_service.models.stub import StubGeneralModel, StubHairModel

FRIENDLY = {"user_message", "what_to_do", "help_topic", "code"}


def _friendly(err):
    assert FRIENDLY <= set(err)
    assert err["user_message"] and err["what_to_do"] and err["help_topic"]
    text = json.dumps(err)
    assert "Traceback" not in text and "File \"" not in text            # no raw stack traces
    for jargon in ("manifest", "ONNX", "AEGP", "Exception"):
        assert jargon.lower() not in (err["user_message"] + err["what_to_do"]).lower()


def test_general_model_failure_shows_no_parts(svc, png_path):
    svc.registry.override("general", StubGeneralModel(fail=True))
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": png_path})
    assert m["job"]["status"] == "failed"
    assert m["parts"] == [] and m["groups"] == [] and m["keypoints"] == []
    assert m["errors"][0]["code"] == "GENERAL_MODEL_FAILED"
    _friendly(m["errors"][0])
    assert not os.path.isdir(os.path.join(job.dir, "parts"))           # nothing left behind to show
    validate_manifest(m)
    assert job.status == "done"                                         # the job itself finished; the result is "failed"


def test_general_model_missing_in_release_build(tmp_path, png_path):
    from conftest import stub_cfg
    from voxy_service.server import Service
    cfg = stub_cfg()
    cfg["service"]["allow_stub_models"] = False                         # what a release build sets
    cfg["models"]["general"]["backend"] = "onnx"                        # and there is no model file
    s = Service(cfg, str(tmp_path / "d"), app_dir=str(tmp_path / "a"), token="t")
    _, m = run_job(s, "make_layers", {"type": "make_layers", "image_path": png_path})
    assert m["job"]["status"] == "failed" and m["parts"] == []
    assert m["errors"][0]["code"] == "GENERAL_MODEL_MISSING"
    _friendly(m["errors"][0])


def test_no_character_found(svc, tmp_path):
    from voxy_service.imaging import save_png
    p = str(tmp_path / "blank.png")
    save_png(p, np.full((200, 120, 4), 255, np.uint8))
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": p})
    assert m["job"]["status"] == "failed" and m["parts"] == []
    assert m["errors"][0]["code"] == "NO_CHARACTER_FOUND"


def test_unreadable_image(svc, tmp_path):
    p = tmp_path / "bad.png"
    p.write_bytes(b"not a png")
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": str(p)})
    assert m["job"]["status"] == "failed" and m["errors"][0]["code"] == "IMAGE_UNREADABLE"
    _friendly(m["errors"][0])


def test_step_timeout_is_reported(svc, png_path):
    svc.cfg["timeouts_s"]["general"] = 0.3
    svc.registry.override("general", StubGeneralModel(delay_s=5))
    t0 = time.time()
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": png_path})
    assert time.time() - t0 < 4
    assert m["job"]["status"] == "failed" and m["errors"][0]["code"] == "STEP_TIMEOUT"
    _friendly(m["errors"][0])


def test_cancellation(svc, png_path):
    svc.registry.override("general", StubGeneralModel(delay_s=10))
    job = svc.jobs.submit("make_layers", {"type": "make_layers", "image_path": png_path})
    time.sleep(0.3)
    assert svc.jobs.cancel(job.id)
    svc.jobs.wait(job.id, 5)
    assert job.status == "cancelled"
    assert job.manifest["job"]["status"] == "cancelled" and job.manifest["parts"] == []
    assert job.manifest["errors"][0]["code"] == "JOB_CANCELLED"


def test_cancel_unknown_job(svc):
    assert svc.jobs.cancel("nope") is False


# ------------------------------------------------------------------ hair subdivision
@pytest.fixture()
def made(svc, png_path):
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": png_path})
    return job, m


def test_subdivision_model_failure_keeps_single_hair_part(svc, made):
    mjob, m = made
    original_hair = [p for p in m["parts"] if p["id"] == "hair"][0]
    svc.registry.override("hair", StubHairModel(fail=True))
    job, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": mjob.id,
                                              "options": {"count": 6, "smoothing": 0.3}})
    assert sm["job"]["status"] == "partial"
    assert [p["id"] for p in sm["parts"]] == ["hair"]                  # the single Hair part is still there
    assert sm["parts"][0]["kind"] == "visible" and sm["parts"][0]["pixel_count"] == original_hair["pixel_count"]
    sub = sm["hair"]["subdivision"]
    assert sub["status"] == "failed" and sub["error"]["code"] == "HAIR_MODEL_FAILED"   # the manifest records it
    assert sm["errors"] and sm["errors"][0]["code"] == "HAIR_MODEL_FAILED"
    _friendly(sm["errors"][0])
    validate_manifest(sm, base_dir=job.dir)                             # and its files really exist
    # Make Layers' own result is untouched
    again = json.load(open(os.path.join(mjob.dir, "manifest.json")))
    assert again["parts"] == m["parts"] and again["hair"]["subdivision"]["status"] == "not_run"


def test_subdivision_model_missing_in_release_build(svc, made):
    mjob, m = made
    svc.registry.allow_stub = False
    svc.registry._cache.pop("hair", None)
    _, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": mjob.id})
    assert sm["job"]["status"] == "partial" and [p["id"] for p in sm["parts"]] == ["hair"]
    assert sm["errors"][0]["code"] == "HAIR_MODEL_MISSING"


def test_hair_subdivision_succeeds(svc, made):
    mjob, m = made
    job, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": mjob.id,
                                              "options": {"count": 5, "smoothing": 0.2}})
    assert sm["job"]["status"] == "ok"
    subs = sm["parts"]
    assert 2 <= len(subs) <= 5
    assert all(p["group"] == "Hair" and p["kind"] == "subdivision" and p["parent"] == "hair" for p in subs)
    assert {tuple(p["pivot"]) for p in subs} == {tuple(sm["hair"]["subdivision"]["pivot"])}   # shared root pivot
    assert sm["hair"]["subdivision"]["status"] == "ok" and sm["hair"]["subdivision"]["count"] == len(subs)
    assert all(p["produced_by"]["model"] == "hair" for p in subs)
    original = [p for p in m["parts"] if p["id"] == "hair"][0]
    assert sum(p["pixel_count"] for p in subs) == original["pixel_count"]           # a partition of the Hair part
    validate_manifest(sm, base_dir=job.dir)


def test_combine_merges_subdivisions_back_into_one_hair_part(svc, made):
    mjob, m = made
    sjob, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": mjob.id})
    cjob, cm = run_job(svc, "combine_hair", {"type": "combine_hair", "subdivision_job_id": sjob.id})
    assert cm["job"]["status"] == "ok"
    assert [p["id"] for p in cm["parts"]] == ["hair"] and cm["parts"][0]["name"] == "Hair"
    original = [p for p in m["parts"] if p["id"] == "hair"][0]
    h = cm["parts"][0]
    assert h["pixel_count"] == original["pixel_count"] and h["bbox"] == original["bbox"]
    assert h["pivot"] == original["pivot"] and h["produced_by"] == original["produced_by"]
    validate_manifest(cm, base_dir=cjob.dir)


def test_subdivide_needs_an_existing_parent(svc):
    _, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": "doesnotexist"})
    assert sm["job"]["status"] == "failed" and sm["errors"][0]["code"] == "PARENT_JOB_MISSING"
    assert sm["parts"] == []


def test_subdivision_count_is_clamped(svc, made):
    mjob, _ = made
    _, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": mjob.id, "options": {"count": 500}})
    assert sm["job"]["status"] == "ok" and len(sm["parts"]) <= svc.cfg["hair"]["max_count"]
