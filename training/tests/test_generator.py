import csv
import hashlib
import json
import os

import cv2
import numpy as np
import pytest

import training  # noqa: F401
from training.shared import augment, generate_dataset as G, provenance, sheets
from training.shared.procedural_art import random_base_layout, random_style, render_character
from voxy_service import schema as S

HW = (256, 192)


def build(seed=1, source="proc-test", variant=0, pool=None, shareable=True):
    rng = np.random.default_rng([seed, 99])
    return G.build_sample(source, variant, seed, HW, random_style(rng), random_base_layout(rng), pool, shareable,
                          {"kind": "procedural", "id": source})


def test_generation_is_deterministic():
    a, b = build(), build()
    assert a["info"] == b["info"]
    for k in ("image", "labels", "completion"):
        assert np.array_equal(a["arrays"][k], b["arrays"][k])
    assert not np.array_equal(a["arrays"]["image"], build(seed=2)["arrays"]["image"])


def test_part_masks_are_exact_and_partition_the_labels():
    s = build()
    labels, masks, parts = s["arrays"]["labels"], s["arrays"]["masks"], s["info"]["parts"]
    total = np.zeros(labels.shape, int)
    for p in parts:
        if p["kind"] != "visible":
            continue
        m = masks[p["id"]]
        assert np.array_equal(m, labels == p["label_value"])             # the script placed it, so the mask is exact
        assert p["group"] == S.GROUP_OF_CLASS[p["class"]] and p["pixel_count"] == int(m.sum())
        total += m
    assert total.max() == 1                                              # no overlap
    assert ((total > 0) == (labels > 0)).all()                           # and nothing unlabelled
    assert labels.shape == s["arrays"]["image"].shape[:2] == (HW[0], HW[1])


def test_hair_is_one_class_front_and_back_together():
    s = build()
    assert [c for c in S.CLASS_NAMES if "hair" in c] == ["hair"]
    labels = s["arrays"]["labels"]
    hair = s["arrays"]["hair"]
    assert hair is not None and np.array_equal(hair["mask"], labels == S.CLASS_INDEX["hair"])
    n, _ = cv2.connectedComponents(hair["mask"].astype(np.uint8))
    assert hair["mask"].sum() > 500


def test_each_sample_json_lists_parts_masks_keypoints_zorder_group_and_models():
    info = build()["info"]
    assert {"parts", "keypoints", "files", "labels_for", "sources", "license", "shareable", "id"} <= set(info)
    for p in info["parts"]:
        assert {"id", "group", "mask", "z_order", "for_models", "kind"} <= set(p)
        assert p["group"] in S.GROUP_ORDER
    assert {k["name"] for k in info["keypoints"]} == set(S.KEYPOINTS)
    # the two models' labels are kept separate
    assert all("hair" not in p["for_models"] for p in info["parts"])
    assert info["labels_for"]["hair"] and all("flow" in x or "mask" in x for x in info["labels_for"]["hair"])
    assert "inpaint" in info["labels_for"]


def test_left_and_right_are_the_characters_own_even_after_mirroring():
    for v in range(8):                                                   # covers flipped and unflipped variants
        s = build(variant=v)
        kp = {k["name"]: (k["x"], k["y"]) for k in s["info"]["keypoints"]}
        assert kp["eye_l"][0] > kp["eye_r"][0] and kp["shoulder_l"][0] > kp["shoulder_r"][0]
        lab = s["arrays"]["labels"]
        xs = lambda c: np.where(lab == S.CLASS_INDEX[c])[1].mean()
        assert xs("eye_l") > xs("eye_r") and xs("arm_l") > xs("arm_r") and xs("foot_l") > xs("foot_r")


def test_mirroring_moves_labels_keypoints_and_flow_together():
    lab = np.zeros((4, 6), np.uint8)
    lab[1, 1] = S.CLASS_INDEX["eye_l"]
    f = augment.flip_labels(lab)
    assert f[1, 4] == S.CLASS_INDEX["eye_r"]
    kp = augment.flip_keypoints({"eye_l": (1.0, 1.0, True)}, 6)
    assert kp == {"eye_r": (4.0, 1.0, True)}
    flow = np.zeros((2, 3, 2), np.float32)
    flow[..., 0] = 1
    assert (augment.flip_flow(flow)[..., 0] == -1).all()


def test_stacking_order_is_recorded_and_true():
    rng = np.random.default_rng(3)
    seen_variation = set()
    for _ in range(12):
        r = render_character(rng, random_style(rng), random_base_layout(rng), HW)
        seen_variation.add((r.z_order["arm_l"] < r.z_order["torso"], r.z_order["leg_l"] > r.z_order["leg_r"]))
        order = sorted(r.full, key=lambda c: r.z_order[c])
        expect = np.zeros(HW, np.uint8)
        for c in order:
            expect[r.full[c]] = S.CLASS_INDEX[c]
        assert np.array_equal(expect, r.labels)                          # visible label = topmost part by z
    assert len(seen_variation) > 1                                       # z-order really is randomised


def test_hidden_parts_are_covered_amodal_regions_with_completion():
    rng = np.random.default_rng(4)
    r = render_character(rng, random_style(rng), random_base_layout(rng), HW)
    assert r.hidden["head"].sum() > 0
    for base, hm in r.hidden.items():
        vis = r.labels == S.CLASS_INDEX[base]
        assert not (hm & vis).any()                                      # hidden means not visible
        assert (hm <= r.amodal[base]).all()                              # inside the part's full extent
        assert (r.labels[hm] != 0).all()                                 # something else covers it
    covered = r.hidden["head"]
    assert not np.array_equal(r.completion_rgb[covered], r.rgb[covered]) # the completion shows what is underneath


def test_hair_ground_truth_flow_roots_and_sections():
    h = build()["arrays"]["hair"]
    m = h["mask"]
    flow = h["flow"].astype(np.float32)
    nrm = np.linalg.norm(flow[m], axis=1)
    assert np.allclose(nrm, 1.0, atol=0.02)                              # unit strand directions
    roots = h["roots"]
    assert len(roots) >= 4
    ok = [m[min(max(int(round(y)), 0), m.shape[0] - 1), min(max(int(round(x)), 0), m.shape[1] - 1)] for x, y in roots]
    assert np.mean(ok) > 0.9                                             # roots start inside the hair
    subs = h["subdivisions"]
    assert ((subs > 0) == m).all() and subs.max() >= 3                   # strands are sections of ONE hair mask
    assert h["root_heat"].astype(np.float32).max() == pytest.approx(1.0, abs=0.01)


def test_augmentations_vary_and_are_recorded():
    kinds, jpegs = set(), set()
    for v in range(20):
        i = build(variant=v)["info"]
        kinds.add(i["background"])
        if "jpeg_quality" in i["augmentations"]:
            jpegs.add(i["augmentations"]["jpeg_quality"])
        assert "outline_px" in i["augmentations"]
    assert len(kinds) >= 2 and len(jpegs) >= 3


# ------------------------------------------------------------------ splits
def test_splits_are_by_source_and_never_leak(tmp_path):
    out = str(tmp_path / "ds")
    info = G.generate(out, sources=9, variants=2, seed=5, size=(128, 96), raw_dir=None)
    where = {}
    for split in G.SPLITS:
        d = os.path.join(out, split)
        for sample in os.listdir(d):
            sj = json.load(open(os.path.join(d, sample, "sample.json")))
            assert sj["split"] == split
            where.setdefault(sj["source_id"], set()).add(split)
    assert all(len(v) == 1 for v in where.values())                      # a source is in exactly one split
    assert len(where) == 9 and {s for v in where.values() for s in v} == set(G.SPLITS)
    srcs = info["sources"]
    assert not (set(srcs["train"]) & set(srcs["val"])) and not (set(srcs["train"]) & set(srcs["test"])) \
        and not (set(srcs["val"]) & set(srcs["test"]))
    assert info["samples"] == {"train": 14, "val": 2, "test": 2} or sum(info["samples"].values()) == 18
    assert info["shareable"] is True and os.path.isfile(os.path.join(out, "dataset_info.json"))


def test_assign_splits_is_stable_and_complete():
    ids = [f"s{i}" for i in range(50)]
    a = G.assign_splits(ids, (0.8, 0.1, 0.1), 1)
    assert a == G.assign_splits(ids, (0.8, 0.1, 0.1), 1) and set(a) == set(ids)
    assert sum(v == "val" for v in a.values()) == 5 and sum(v == "test" for v in a.values()) == 5


def test_written_sample_files(tmp_path):
    out = str(tmp_path / "ds")
    G.generate(out, sources=3, variants=1, seed=2, size=(128, 96), raw_dir=None)
    split = next(s for s in G.SPLITS if os.listdir(os.path.join(out, s)))
    d = os.path.join(out, split, os.listdir(os.path.join(out, split))[0])
    for f in ("image.png", "labels.png", "completion.png", "amodal.npz", "hair.npz", "sample.json"):
        assert os.path.isfile(os.path.join(d, f)), f
    sj = json.load(open(os.path.join(d, "sample.json")))
    for p in sj["parts"]:
        m = cv2.imread(os.path.join(d, p["mask"]), cv2.IMREAD_GRAYSCALE)
        assert m is not None and (m > 0).sum() == p["pixel_count"]
    lab = cv2.imread(os.path.join(d, "labels.png"), cv2.IMREAD_UNCHANGED)
    assert lab.dtype == np.uint8 and lab.max() < S.NUM_CLASSES


# ------------------------------------------------------------------ provenance and reference sheets
def make_sheet(raw, name="sheet1.png"):
    img = np.full((120, 300, 3), 255, np.uint8)
    pieces = []
    for i, col in enumerate([(200, 60, 60), (60, 90, 200), (230, 200, 70)]):
        x0 = 10 + i * 95
        poly = np.array([[x0 + 40, 10], [x0 + 70, 60], [x0 + 55, 110], [x0 + 20, 110], [x0 + 5, 60]], np.int32)
        cv2.fillPoly(img, [poly], col)
        pieces.append({"class": "hair", "polygon": poly.tolist(), "anchor": [x0 + 40, 10]})
    os.makedirs(raw, exist_ok=True)
    cv2.imwrite(os.path.join(raw, name), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    json.dump({"pieces": pieces}, open(os.path.join(raw, name.replace(".png", ".parts.json")), "w"))
    return name


def log_row(raw, name, **kw):
    row = dict(file=name, source="test", author="tester", license="CC0-1.0", license_url="", owner_permission="",
               acquired="2026-01-01", redistributable="yes", sha256=provenance.sha256_file(os.path.join(raw, name)), notes="")
    row.update(kw)
    path = os.path.join(raw, "PROVENANCE.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, provenance.FIELDS)
        w.writeheader()
        w.writerow(row)


def test_unlicensed_sheets_are_refused(tmp_path):
    raw = str(tmp_path / "raw")
    name = make_sheet(raw)
    with pytest.raises(PermissionError, match="no row"):
        sheets.load_all(raw)
    log_row(raw, name, license="")
    with pytest.raises(PermissionError, match="license is empty"):
        sheets.load_all(raw)
    log_row(raw, name, license="CC-BY-NC-4.0")
    with pytest.raises(PermissionError, match="internal_only"):
        sheets.load_all(raw)                                              # known license, but not shareable
    log_row(raw, name, license="owner-permission", owner_permission="")
    with pytest.raises(PermissionError):
        sheets.load_all(raw)
    log_row(raw, name, sha256="0" * 64)
    with pytest.raises(PermissionError, match="hash_mismatch"):
        sheets.load_all(raw)


def test_licensed_sheets_feed_hair_pieces_and_keep_the_dataset_shareable(tmp_path):
    raw, out = str(tmp_path / "raw"), str(tmp_path / "ds")
    name = make_sheet(raw)
    log_row(raw, name)
    info = G.generate(out, sources=6, variants=1, seed=3, size=(160, 120), raw_dir=raw)
    assert info["shareable"] is True and info["reference_sheets"] == [name]
    used = 0
    for split in G.SPLITS:
        for sample in os.listdir(os.path.join(out, split)):
            sj = json.load(open(os.path.join(out, split, sample, "sample.json")))
            if sj["hair"] and sj["hair"]["pieces_from_sheets"]:
                used += 1
                assert any(s.get("id") == name for s in sj["sources"])    # the sheet is recorded per sample
                lab = cv2.imread(os.path.join(out, split, sample, "labels.png"), cv2.IMREAD_UNCHANGED)
                assert (lab == S.CLASS_INDEX["hair"]).sum() > 0
    assert used >= 1


def test_internal_only_sheets_need_a_flag_and_mark_the_dataset_not_shareable(tmp_path):
    raw, out = str(tmp_path / "raw"), str(tmp_path / "ds")
    name = make_sheet(raw)
    log_row(raw, name, license="CC-BY-NC-4.0", redistributable="no")
    assert G.main(["--out", out, "--sources", "3", "--variants", "1", "--raw", raw, "--height", "96", "--width", "72"]) == 2
    info = G.generate(out, sources=3, variants=1, seed=1, size=(96, 72), raw_dir=raw, allow_internal=True)
    assert info["shareable"] is False


def test_provenance_audit_reports_every_image(tmp_path):
    raw = str(tmp_path / "raw")
    name = make_sheet(raw)
    cv2.imwrite(os.path.join(raw, "stray.png"), np.zeros((4, 4, 3), np.uint8))
    log_row(raw, name)
    res = {s.file: s.state for s in provenance.audit(raw)}
    assert res[name] == "shareable" and res["stray.png"] == "unknown"
    assert provenance.main(["audit", "--raw", raw]) == 1
