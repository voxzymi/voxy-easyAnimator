import os

import cv2
import numpy as np
import pytest

from conftest import make_character, run_job
from voxy_service import inpaint as I
from voxy_service.background import choose_engine, make_background_plate
from voxy_service.cancel import CancelToken
from voxy_service.errors import Cancelled, VoxyError
from voxy_service.imaging import load_image
from voxy_service.matting import FlatMatter, key_background


def gradient_bg(h=200, w=160):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = 200 + 30 * xx / w
    g = 180 + 40 * yy / h
    b = 220 - 20 * xx / w
    return np.clip(np.stack([r, g, b], axis=2), 0, 255).astype(np.uint8)


def test_patch_fill_recovers_a_smooth_background():
    bg = gradient_bg()
    hole = np.zeros(bg.shape[:2], bool)
    hole[50:150, 50:110] = True
    damaged = bg.copy()
    damaged[hole] = (10, 10, 10)
    out = I.patch_fill(damaged, hole)
    assert np.abs(out[hole].astype(int) - bg[hole].astype(int)).mean() < 8          # colour-aware: close to the truth
    assert (out[~hole] == damaged[~hole]).all()                                     # only the hole changes
    assert not (out[hole] == (10, 10, 10)).all(axis=1).any()


def test_patch_fill_is_deterministic():
    bg = gradient_bg()
    hole = np.zeros(bg.shape[:2], bool)
    hole[60:120, 40:100] = True
    assert (I.patch_fill(bg, hole) == I.patch_fill(bg, hole)).all()


def test_patch_fill_can_be_cancelled():
    bg = gradient_bg(400, 400)
    hole = np.zeros(bg.shape[:2], bool)
    hole[50:350, 50:350] = True
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        I.patch_fill(bg, hole, cancel=tok)


def test_patch_fill_without_context_is_a_friendly_error():
    hole = np.ones((30, 30), bool)
    with pytest.raises(VoxyError) as e:
        I.patch_fill(np.zeros((30, 30, 3), np.uint8), hole)
    assert e.value.code == "INPAINT_FAILED"


def test_blend_hides_the_seam():
    orig = np.full((60, 60, 3), 100, np.uint8)
    filled = np.full((60, 60, 3), 140, np.uint8)             # fill is a bit brighter than the surroundings
    hole = np.zeros((60, 60), bool)
    hole[20:40, 20:40] = True
    out = I.blend_fill(orig, filled, hole, 4)
    assert (out[hole] == 140).all()                          # inside the hole only the fill is right
    assert out[0, 0, 0] == 100 and out[59, 59, 0] == 100     # far away: untouched
    row = out[30, 10:20, 0].astype(int)                      # outside the hole: a smooth ramp, no step
    assert (np.diff(row) >= 0).all() and np.diff(row).max() <= 12


def test_blend_without_feather_is_a_hard_edge():
    orig, filled = np.zeros((10, 10, 3), np.uint8), np.full((10, 10, 3), 200, np.uint8)
    hole = np.zeros((10, 10), bool)
    hole[3:6, 3:6] = True
    out = I.blend_fill(orig, filled, hole, 0)
    assert (out[hole] == 200).all() and (out[~hole] == 0).all()


def test_background_plate_removes_the_character_and_keeps_the_rest():
    img, tr = make_character(bg=(235, 225, 245))
    char = tr.labels > 0
    cfg = {"hole_grow_px": 3, "feather_px": 3, "patch_size": 9, "patch_work_max": 200}
    plate = make_background_plate(img, char, engine="patch", bg_cfg=cfg, inpainter=None, cancel=CancelToken())
    assert plate.shape == img.shape and (plate[:, :, 3] == 255).all()
    on_char = plate[char][:, :3].astype(int)
    assert np.abs(on_char - np.array([235, 225, 245])).max() <= 6          # no character colours left
    far = ~(cv2.dilate(char.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0)
    assert (plate[far] == img[far]).all()                                  # background elsewhere is untouched


def test_background_plate_needs_an_opaque_picture():
    img, tr = make_character(transparent=True)
    with pytest.raises(VoxyError) as e:
        make_background_plate(img, tr.labels > 0, engine="patch",
                              bg_cfg={"hole_grow_px": 3, "feather_px": 3, "patch_size": 9, "patch_work_max": 128},
                              inpainter=None, cancel=CancelToken())
    assert e.value.code == "INPAINT_FAILED"


def test_engine_choice(tmp_path):
    assert choose_engine("patch", str(tmp_path), "onnx") == ("patch", None)
    assert choose_engine("auto", str(tmp_path), "patch") == ("patch", "BACKGROUND_FILL_FALLBACK")
    with pytest.raises(VoxyError) as e:
        choose_engine("model", str(tmp_path), "onnx")
    assert e.value.code == "INPAINT_MODEL_MISSING"
    (tmp_path / "model.onnx").write_bytes(b"x")
    assert choose_engine("auto", str(tmp_path), "onnx") == ("model", None)


# ------------------------------------------------------------------ matting
def test_key_background_keeps_bg_coloured_areas_inside_the_character():
    img = np.full((120, 120, 4), 255, np.uint8)
    cv2.circle(img, (60, 60), 40, (30, 30, 30, 255), -1)
    cv2.circle(img, (60, 60), 12, (255, 255, 255, 255), -1)      # a white eye: bg colour, but enclosed
    a = key_background(img)
    assert a[2, 2] < 0.1 and a[117, 117] < 0.1                    # background removed
    assert a[60, 60] > 0.9                                        # enclosed white kept
    assert a[40, 60] > 0.9


def test_key_background_handles_gradients():
    img = np.dstack([gradient_bg(120, 120), np.full((120, 120), 255, np.uint8)])
    cv2.circle(img, (60, 60), 30, (20, 40, 160, 255), -1)
    a = key_background(img, tolerance=14)
    assert a[5, 5] < 0.1 and a[60, 60] > 0.9


def test_matter_fails_friendly_on_a_picture_with_no_background():
    rng = np.random.default_rng(0)
    img = np.dstack([rng.integers(0, 255, (80, 80, 3), dtype=np.uint8), np.full((80, 80), 255, np.uint8)])
    with pytest.raises(VoxyError) as e:
        FlatMatter().alpha(img, CancelToken())
    assert e.value.code == "MATTING_FAILED"


# ------------------------------------------------------------------ in a full Make Layers run
@pytest.fixture()
def opaque_png(tmp_path):
    from voxy_service.imaging import save_png
    img, tr = make_character(bg=(238, 228, 246))
    p = str(tmp_path / "opaque.png")
    save_png(p, img)
    return p, img, tr


def test_make_layers_with_background_removal_adds_a_background_plate(svc, opaque_png):
    path, img, tr = opaque_png
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": path,
                                          "options": {"background_removal": True}})
    assert m["job"]["status"] == "ok", m["errors"]
    bg = m["background"]
    assert bg and bg["name"] == "Background" and bg["method"] == "patch"
    assert any(w["code"] == "BACKGROUND_FILL_FALLBACK" for w in m["warnings"])      # the user is told why
    plate = load_image(os.path.join(job.dir, bg["image"]))
    assert plate.shape[:2] == img.shape[:2]
    char = tr.labels > 0
    assert np.abs(plate[char][:, :3].astype(int) - np.array([238, 228, 246])).max() <= 8
    assert m["models"]["matting"]["backend"] == "algorithm"
    assert m["parts"] and {g["name"] for g in m["groups"]} >= {"Head", "Hair"}


def test_background_removal_off_makes_no_background_plate(svc, opaque_png):
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": opaque_png[0]})
    assert m["background"] is None and "matting" not in m["models"]


def test_background_failure_keeps_the_parts(svc, opaque_png):
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": opaque_png[0],
                                        "options": {"background_removal": True, "background_fill": "model"}})
    assert m["job"]["status"] == "partial" and m["parts"]                           # parts survive
    assert m["background"] is None and m["errors"][0]["code"] == "INPAINT_MODEL_MISSING"
    assert "Flat colors" in m["errors"][0]["what_to_do"]


def test_user_can_choose_the_flat_fill_explicitly(svc, opaque_png):
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": opaque_png[0],
                                        "options": {"background_removal": True, "background_fill": "patch"}})
    assert m["background"]["method"] == "patch"
    assert not any(w["code"] == "BACKGROUND_FILL_FALLBACK" for w in m["warnings"])   # a choice, not a fallback
