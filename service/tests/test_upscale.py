import hashlib
import os
import sys
import threading
import time

import numpy as np
import pytest
from PIL import Image

from conftest import run_job, stub_cfg
from voxy_service import upscale as U
from voxy_service.cancel import CancelToken
from voxy_service.errors import Cancelled, VoxyError
from voxy_service.server import Service

FAKE = os.path.join(os.path.dirname(__file__), "fake_upscayl.py")


@pytest.fixture()
def models_dir(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    for ext in (".param", ".bin"):
        (d / ("realesr-animevideov3-x2" + ext)).write_bytes(b"x")
    return str(d)


@pytest.fixture()
def ucfg(models_dir):
    cfg = stub_cfg()["upscale"]
    cfg.update(executable=FAKE, models_dir=models_dir)
    return cfg


@pytest.fixture()
def frame(tmp_path):
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 255, (60, 40, 4), dtype=np.uint8)
    p = str(tmp_path / "layer_frame.png")
    Image.fromarray(arr, "RGBA").save(p)
    return p, arr


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_flags_come_from_the_tools_own_help_text():
    flags, text = U.probe_flags(FAKE)
    assert U.REQUIRED_FLAGS <= flags
    assert "-z" in text and "model-scale" in text


def test_command_line_uses_only_documented_flags(ucfg, tmp_path):
    cmd = U.build_command("upscayl-bin", "in.png", "out.png", model="m", model_scale=2, scale=2, tile=0,
                          models_dir="/models", gpu_id=None)
    assert cmd == ["upscayl-bin", "-i", "in.png", "-o", "out.png", "-z", "2", "-s", "2", "-n", "m", "-t", "0",
                   "-f", "png", "-m", "/models"]
    used = {a[1] for a in cmd if a.startswith("-") and len(a) == 2}
    flags, _ = U.probe_flags(FAKE)
    assert used <= flags


def test_upscale_2x_leaves_the_input_alone_and_writes_a_temp_result(ucfg, frame, tmp_path):
    path, arr = frame
    before = digest(path)
    up = U.Upscaler(ucfg, str(tmp_path))
    out_dir = str(tmp_path / "tmp" / "up1")
    res = up.upscale(path, out_dir, CancelToken(), 30)
    assert res.output_size == (80, 120) and res.input_size == (40, 60)
    assert os.path.dirname(res.output_path) == out_dir and os.path.getsize(res.output_path) > 0
    assert digest(path) == before                                       # the source frame is untouched
    assert sorted(os.listdir(tmp_path / "tmp")) == ["up1"]              # nothing written anywhere else


def test_out_of_memory_retries_with_smaller_tiles(ucfg, frame, tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_UPSCAYL_OOM_ABOVE", "100")
    monkeypatch.setenv("FAKE_UPSCAYL_LOG", str(log))
    res = U.Upscaler(ucfg, str(tmp_path)).upscale(frame[0], str(tmp_path / "o"), CancelToken(), 30)
    tiles = [int(l.split("-t ")[1].split()[0]) for l in log.read_text().splitlines()]
    assert tiles == [0, 256, 128, 64]                                   # auto, then halving until it fits
    assert res.tile == 64 and res.attempts == 4


def test_out_of_memory_at_every_size_is_reported(ucfg, frame, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_UPSCAYL_OOM_ABOVE", "8")
    with pytest.raises(VoxyError) as e:
        U.Upscaler(ucfg, str(tmp_path)).upscale(frame[0], str(tmp_path / "o"), CancelToken(), 30)
    assert e.value.code == "UPSCALER_OOM"


def test_other_failures_do_not_retry(ucfg, frame, tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_UPSCAYL_FAIL", "1")
    monkeypatch.setenv("FAKE_UPSCAYL_LOG", str(log))
    with pytest.raises(VoxyError) as e:
        U.Upscaler(ucfg, str(tmp_path)).upscale(frame[0], str(tmp_path / "o"), CancelToken(), 30)
    assert e.value.code == "UPSCALER_FAILED"
    assert len(log.read_text().splitlines()) == 1


def test_timeout_kills_the_tool(ucfg, frame, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_UPSCAYL_SLEEP", "5")
    t0 = time.time()
    with pytest.raises(VoxyError) as e:
        U.Upscaler(ucfg, str(tmp_path)).upscale(frame[0], str(tmp_path / "o"), CancelToken(), 0.6)
    assert e.value.code == "UPSCALER_TIMEOUT" and time.time() - t0 < 4


def test_cancel_stops_the_tool(ucfg, frame, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_UPSCAYL_SLEEP", "5")
    tok = CancelToken()
    threading.Timer(0.4, tok.cancel).start()
    t0 = time.time()
    with pytest.raises(Cancelled):
        U.Upscaler(ucfg, str(tmp_path)).upscale(frame[0], str(tmp_path / "o"), tok, 30)
    assert time.time() - t0 < 4


def test_missing_executable(frame, tmp_path):
    cfg = stub_cfg()["upscale"]
    cfg["executable"] = str(tmp_path / "nope")
    with pytest.raises(VoxyError) as e:
        U.Upscaler(cfg, str(tmp_path)).upscale(frame[0], str(tmp_path / "o"), CancelToken(), 5)
    assert e.value.code == "UPSCALER_MISSING"
    assert U.Upscaler(cfg, str(tmp_path)).check() == {"ok": False, "reason": "missing"}


def test_check_reports_model_files(ucfg, tmp_path, models_dir):
    up = U.Upscaler(ucfg, str(tmp_path))
    assert up.check()["ok"] is True
    os.remove(os.path.join(models_dir, "realesr-animevideov3-x2.bin"))
    assert up.check()["ok"] is False and up.check()["reason"] == "model"


# ------------------------------------------------------------------ tiling
@pytest.mark.parametrize("w,h,tile,ov", [(100, 80, 64, 8), (500, 30, 128, 16), (129, 129, 64, 8), (64, 64, 64, 8)])
def test_tiles_cover_every_pixel(w, h, tile, ov):
    boxes = U.plan_tiles(w, h, tile, ov)
    cover = np.zeros((h, w), int)
    for x0, y0, x1, y1 in boxes:
        assert x1 - x0 <= tile and y1 - y0 <= tile
        cover[y0:y1, x0:x1] += 1
    assert cover.min() >= 1


def test_large_pictures_are_tiled_and_stitched_exactly(ucfg, tmp_path):
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 255, (150, 170, 4), dtype=np.uint8)
    p = str(tmp_path / "big.png")
    Image.fromarray(arr, "RGBA").save(p)
    ucfg.update(max_single_pass_px=1000, outer_tile=64, outer_overlap=8)       # force the tile path
    res = U.Upscaler(ucfg, str(tmp_path)).upscale(p, str(tmp_path / "o"), CancelToken(), 60)
    got = np.array(Image.open(res.output_path))
    want = np.array(Image.fromarray(arr, "RGBA").resize((340, 300), Image.BICUBIC))
    assert got.shape == want.shape
    assert np.abs(got.astype(int) - want.astype(int)).max() <= 1               # same as one pass
    assert not os.path.exists(tmp_path / "o" / "_tiles")                        # scratch tiles removed


# ------------------------------------------------------------------ as a job: temp file until Apply or Discard
@pytest.fixture()
def up_svc(tmp_path, ucfg):
    cfg = stub_cfg()
    cfg["upscale"] = ucfg
    s = Service(cfg, str(tmp_path / "data"), app_dir=str(tmp_path / "app"), token="t")
    yield s
    s.jobs._pool.shutdown(wait=False, cancel_futures=True)


def test_upscale_job_result_is_temporary_and_discard_deletes_it(up_svc, frame):
    job, m = run_job(up_svc, "upscale", {"type": "upscale", "image_path": frame[0]})
    assert m["job"]["status"] == "ok" and m["upscale"]["status"] == "ok"
    assert m["upscale"]["scale"] == 2 and m["upscale"]["output_size"] == [80, 120] and m["upscale"]["input_size"] == [40, 60]
    assert m["parts"] == []
    result = os.path.join(job.dir, m["upscale"]["image"])
    assert os.path.isfile(result)
    assert os.sep + "tmp" + os.sep in result                              # kept in the temporary area
    assert up_svc.jobs.discard(job.id) is True                            # Discard
    assert not os.path.exists(result) and not os.path.exists(job.dir)
    assert os.path.isfile(frame[0])                                       # the exported frame is not ours to delete


def test_temporary_results_are_cleaned_when_the_project_closes(up_svc, frame):
    j1, _ = run_job(up_svc, "upscale", {"type": "upscale", "image_path": frame[0]})
    j2, _ = run_job(up_svc, "upscale", {"type": "upscale", "image_path": frame[0]})
    assert up_svc.jobs.cleanup_tmp() >= 2
    assert not os.path.exists(j1.dir) and not os.path.exists(j2.dir)


def test_upscale_failures_are_in_the_manifest(tmp_path, frame):
    cfg = stub_cfg()
    cfg["upscale"]["executable"] = str(tmp_path / "missing-tool")
    s = Service(cfg, str(tmp_path / "d"), app_dir=str(tmp_path / "a"), token="t")
    _, m = run_job(s, "upscale", {"type": "upscale", "image_path": frame[0]})
    assert m["job"]["status"] == "failed"
    assert m["upscale"]["status"] == "failed" and m["upscale"]["error"]["code"] == "UPSCALER_MISSING"
    assert m["errors"][0]["what_to_do"] and m["errors"][0]["help_topic"]


def test_upscale_job_reports_oom_and_timeout(up_svc, frame, monkeypatch):
    monkeypatch.setenv("FAKE_UPSCAYL_OOM_ABOVE", "8")
    _, m = run_job(up_svc, "upscale", {"type": "upscale", "image_path": frame[0]})
    assert m["errors"][0]["code"] == "UPSCALER_OOM"
    monkeypatch.delenv("FAKE_UPSCAYL_OOM_ABOVE")
    monkeypatch.setenv("FAKE_UPSCAYL_SLEEP", "5")
    up_svc.cfg["timeouts_s"]["upscale"] = 0.5
    _, m = run_job(up_svc, "upscale", {"type": "upscale", "image_path": frame[0]})
    assert m["errors"][0]["code"] == "UPSCALER_TIMEOUT"
