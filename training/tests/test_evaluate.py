import json
import os

import numpy as np
import pytest

import training  # noqa: F401
from training.shared import evaluate as E, generate_dataset as G
from voxy_service import schema as S
from voxy_service.models.base import GeneralOutput, HairOutput, ModelInfo


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("evalds"))
    G.generate(out, sources=6, variants=2, seed=21, size=(160, 120), raw_dir=None)
    return out


class Oracle:
    """Returns the ground truth: every metric must be perfect."""
    info = ModelInfo("oracle", "0", "stub")

    def __init__(self, items):
        self.by_img = {it["image"].tobytes(): it for it in items}

    def predict(self, rgba, cancel):
        it = self.by_img[np.ascontiguousarray(rgba[:, :, :3]).tobytes()]
        am = {c: it["amodal"][c] for c in S.AMODAL_CLASSES}
        return GeneralOutput(labels=it["labels"], keypoints={k: (x, y, 1.0) for k, (x, y) in it["kps"].items()}, amodal=am,
                             completion_rgb=it["completion"])


class HairOracle:
    info = ModelInfo("oracle", "0", "stub")

    def __init__(self, items):
        self.by_img = {it["image"].tobytes(): it for it in items if it["hair"]}

    def predict(self, rgb, mask, cancel):
        h = self.by_img[np.ascontiguousarray(rgb).tobytes()]["hair"]
        heat = np.zeros(mask.shape, np.float32)
        for x, y in h["roots"]:
            heat[int(round(y)), int(round(x))] = 1
        return HairOutput(flow=h["flow"], roots=heat)


def test_a_perfect_general_model_scores_perfectly(ds):
    items = list(E.iter_synthetic(os.path.join(ds, "test")))
    rep = E.eval_general(Oracle(items), items)
    assert rep["mean_iou"] == pytest.approx(1.0) and rep["hair_iou"] == pytest.approx(1.0)
    assert rep["pixel_group_accuracy"] == 1.0 and rep["part_group_accuracy"] == 1.0
    assert rep["pck_0.05"]["pck"] == 1.0 and rep["pck_0.10"]["pck"] == 1.0
    assert rep["hidden_extent_iou"] == pytest.approx(1.0) and rep["completion_mean_abs_error"] == pytest.approx(0.0, abs=1e-9)
    assert rep["pck_0.05"]["threshold"] == "0.05 x character height"        # the threshold is stated


def test_a_wrong_model_scores_badly_and_group_accuracy_follows_the_mapping(ds):
    items = list(E.iter_synthetic(os.path.join(ds, "test")))

    class SwapLimbs(Oracle):               # calls every arm an eye: wrong class AND wrong group
        def predict(self, rgba, cancel):
            o = super().predict(rgba, cancel)
            lab = o.labels.copy()
            lab[(lab == S.CLASS_INDEX["arm_l"])] = S.CLASS_INDEX["eye_l"]
            lab[(lab == S.CLASS_INDEX["leg_l"])] = S.CLASS_INDEX["foot_l"]     # wrong class, SAME group (Legs)
            return GeneralOutput(labels=lab, keypoints=o.keypoints, amodal=o.amodal, completion_rgb=o.completion_rgb)

    rep = E.eval_general(SwapLimbs(items), items)
    assert rep["per_part_iou"]["arm_l"] == pytest.approx(0.0, abs=1e-6)
    assert rep["per_part_iou"]["leg_l"] < 0.2 and rep["per_part_iou"]["head"] == pytest.approx(1.0)
    assert rep["pixel_group_accuracy"] < 1.0
    # leg->foot keeps the group right, arm->eye does not: group accuracy beats plain class accuracy on the character's pixels
    sw = SwapLimbs(items)
    ok = tot = 0
    for i in items:
        lab = sw.predict(np.dstack([i["image"], np.full(i["image"].shape[:2], 255, np.uint8)]), None).labels
        fg = i["labels"] > 0
        ok += int((lab[fg] == i["labels"][fg]).sum()); tot += int(fg.sum())
    assert rep["pixel_group_accuracy"] > ok / tot


def test_keypoint_pck_threshold():
    gt = [{"eye_l": (10.0, 10.0), "mouth": (50.0, 50.0)}]
    pred = [{"eye_l": (10.0, 14.0, 1.0), "mouth": (50.0, 70.0, 1.0)}]        # 4 px and 20 px off, character 100 px tall
    assert E.pck(pred, gt, [100.0], 0.05)["pck"] == 0.5                        # 5 px allowed: only the first
    assert E.pck(pred, gt, [100.0], 0.25)["pck"] == 1.0


def test_matched_iou_ignores_section_numbering():
    gt = np.zeros((10, 30), np.uint8); gt[:, :10] = 1; gt[:, 10:20] = 2; gt[:, 20:] = 3
    perm = np.zeros_like(gt); perm[gt == 1] = 3; perm[gt == 2] = 1; perm[gt == 3] = 2
    assert E.matched_iou(perm, gt) == pytest.approx(1.0)
    shifted = np.zeros_like(gt); shifted[:, :15] = 1; shifted[:, 15:] = 2
    assert 0.3 < E.matched_iou(shifted, gt) < 0.8


def test_root_f1():
    gt = np.array([[10, 10], [30, 10], [50, 10]], np.float32)
    assert E.root_f1(gt.copy(), gt)[2] == 1.0
    p, r, f = E.root_f1(np.array([[10, 11], [100, 100]], np.float32), gt)
    assert p == 0.5 and r == pytest.approx(1 / 3) and 0 < f < 1


def test_a_perfect_hair_model_scores_perfectly_and_beats_the_baseline(ds, tmp_path):
    items = list(E.iter_synthetic(os.path.join(ds, "test")))
    rep = E.eval_hair(HairOracle(items), items, str(tmp_path / "hair.png"))
    assert rep["flow_angular_error_deg_mean"] < 1.0 and rep["root_f1_4px"] > 0.95
    # Perfect flow and roots still do not give 1.0: root extraction merges roots closer than 4 px, which nudges the
    # section boundaries. The score must still be high and clearly above the vertical-flow baseline.
    assert rep["subdivision_matched_iou"] > 0.75
    assert rep["subdivision_matched_iou"] > rep["subdivision_baseline_vertical_flow"]
    assert os.path.getsize(tmp_path / "hair.png") > 1000                        # a contact sheet was written


def test_full_run_writes_report_and_contact_sheets(ds, tmp_path):
    import argparse
    a = argparse.Namespace(general="stub", hair="stub", synthetic=os.path.join(ds, "test"), real=None, hair_real=None,
                           images="data/raw", out=str(tmp_path / "eval"), max_images=6)
    rep = E.run(a)
    assert set(rep["general"]) == {"synthetic_test"} and set(rep["hair"]) == {"synthetic_test"}
    assert any("stub" in n for n in rep["notes"])                              # the report says a stub was evaluated
    for f in ("report.json", "contact_general_synthetic.png", "contact_hair_synthetic.png"):
        assert os.path.isfile(tmp_path / "eval" / f)
    assert 0 <= rep["general"]["synthetic_test"]["mean_iou"] <= 1


def test_real_art_is_reported_separately_and_empty_is_said_plainly(ds, tmp_path):
    import argparse
    a = argparse.Namespace(general="stub", hair=None, synthetic=None, real=str(tmp_path / "none"), hair_real=None,
                           images=str(tmp_path), out=str(tmp_path / "e"), max_images=5)
    rep = E.run(a)
    assert rep["general"]["held_out_real"]["images"] == 0 and "no held-out real images" in rep["general"]["held_out_real"]["note"]
