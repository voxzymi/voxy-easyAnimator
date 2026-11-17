import copy
import json
import os

import pytest

from conftest import run_job
from voxy_service import schema as S
from voxy_service.manifest import ManifestError, rebuild_groups, validate_manifest


@pytest.fixture()
def made(svc, png_path):
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": png_path})
    return svc, job, m


def test_make_layers_manifest_is_valid(made):
    svc, job, m = made
    assert m["job"]["status"] == "ok"
    validate_manifest(m, base_dir=job.dir)            # schema + semantic rules + files exist
    assert os.path.isfile(os.path.join(job.dir, "manifest.json"))


def test_every_part_has_a_group_from_the_fixed_mapping(made):
    _, _, m = made
    assert m["parts"]
    for p in m["parts"]:
        assert p["group"] in S.GROUP_ORDER
        assert p["group"] == S.GROUP_OF_CLASS[p["class"]]
        assert {"model", "version", "backend"} <= set(p["produced_by"])
        assert p["produced_by"]["model"] == "general"


def test_groups_follow_group_order_and_manifest_part_order(made):
    _, _, m = made
    names = [g["name"] for g in m["groups"]]
    assert names == [g for g in S.GROUP_ORDER if g in names]
    flat = [pid for g in m["groups"] for pid in g["parts"]]
    assert flat == [p["id"] for p in m["parts"]]       # same order the manifest gives them
    assert m["groups"] == rebuild_groups(m["parts"])


def test_hair_group_has_exactly_one_part_named_hair(made):
    _, _, m = made
    hair = [p for p in m["parts"] if p["group"] == "Hair"]
    assert len(hair) == 1
    assert hair[0]["name"] == "Hair" and hair[0]["kind"] == "visible"
    assert m["hair"]["subdivision"]["status"] == "not_run"


def test_groups_without_parts_are_not_listed_and_empty_parts_are_skipped(svc, png_path):
    from voxy_service.models.stub import StubGeneralModel

    class NoLegs(StubGeneralModel):          # a model that finds no pixels for legs and feet
        def predict(self, rgba, cancel):
            out = super().predict(rgba, cancel)
            for c in ("leg_l", "leg_r", "foot_l", "foot_r"):
                out.labels[out.labels == S.CLASS_INDEX[c]] = 0
            return out

    svc.registry.override("general", NoLegs())
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": png_path})
    names = [g["name"] for g in m["groups"]]
    assert "Legs" not in names                                          # no empty group
    skipped = {s["id"] for s in m["skipped"]}
    assert {"leg_l", "leg_r", "foot_l", "foot_r"} <= skipped            # listed in the report
    assert all(s["reason"] == "no_pixels" and s["group"] == "Legs" for s in m["skipped"] if s["id"].startswith(("leg", "foot")))
    assert not skipped & {p["id"] for p in m["parts"]}
    assert all(p["pixel_count"] > 0 for p in m["parts"])                # never an empty part
    validate_manifest(m)


def test_parts_have_masks_outlines_pivots_and_files(made):
    _, job, m = made
    for p in m["parts"]:
        x, y, w, h = p["bbox"]
        assert w > 0 and h > 0
        assert p["outline"] and all(len(o["points"]) >= 3 for o in p["outline"])
        assert len(p["pivot"]) == 2
        assert os.path.isfile(os.path.join(job.dir, p["image"]))
        assert os.path.isfile(os.path.join(job.dir, p["mask"]))


def test_pivots_sit_at_joints(made):
    _, _, m = made
    kp = {k["name"]: (k["x"], k["y"]) for k in m["keypoints"]}
    byid = {p["id"]: p for p in m["parts"]}
    for part, joint in [("arm_l", "shoulder_l"), ("hand_r", "wrist_r"), ("leg_l", "hip_l"), ("foot_r", "ankle_r"),
                        ("eye_l", "eye_l"), ("mouth", "mouth")]:
        assert tuple(byid[part]["pivot"]) == pytest.approx(kp[joint], abs=0.01)


def test_hidden_parts_are_completed_and_grouped(made):
    _, _, m = made
    hidden = [p for p in m["parts"] if p["group"] == "Hidden"]
    assert hidden, "bangs cover part of the face and eyes, so hidden parts are expected"
    assert all(p["kind"] == "hidden" for p in hidden)
    hair_z = [p for p in m["parts"] if p["id"] == "hair"][0]["z_order"]
    assert all(p["z_order"] < hair_z for p in hidden)                   # hidden parts sit behind the hair


# ------------------------------------------------------------------ the validator itself
def _bad(m, mutate):
    m2 = copy.deepcopy(m)
    mutate(m2)
    with pytest.raises(ManifestError):
        validate_manifest(m2)


def test_validator_rejects_wrong_group(made):
    _, _, m = made
    _bad(m, lambda x: x["parts"][0].__setitem__("group", "Legs"))


def test_validator_rejects_unknown_group_name(made):
    _, _, m = made
    _bad(m, lambda x: x["parts"][0].__setitem__("group", "Body"))


def test_validator_rejects_missing_group_field(made):
    _, _, m = made
    _bad(m, lambda x: x["parts"][0].pop("group"))


def test_validator_rejects_two_hair_parts(made):
    _, _, m = made
    def two(x):
        h = [p for p in x["parts"] if p["id"] == "hair"][0]
        dup = copy.deepcopy(h)
        dup["id"] = "hair2"
        x["parts"].append(dup)
        x["groups"] = rebuild_groups(x["parts"])
    _bad(m, two)


def test_validator_rejects_stale_group_list_and_duplicate_ids(made):
    _, _, m = made
    _bad(m, lambda x: x["groups"].reverse())
    _bad(m, lambda x: x["parts"].append(copy.deepcopy(x["parts"][0])))


def test_validator_rejects_empty_pixel_part_and_part_also_skipped(made):
    _, _, m = made
    _bad(m, lambda x: x["parts"][0].__setitem__("pixel_count", 0))
    _bad(m, lambda x: x["skipped"].append({"id": x["parts"][0]["id"], "name": "x", "group": "Head", "reason": "no_pixels"}))


def test_validator_rejects_failed_make_layers_with_parts(made):
    _, _, m = made
    _bad(m, lambda x: x["job"].__setitem__("status", "failed"))


def test_validator_reports_missing_files(made, tmp_path):
    _, job, m = made
    m2 = copy.deepcopy(m)
    m2["parts"][0]["image"] = "parts/nope.png"
    with pytest.raises(ManifestError) as e:
        validate_manifest(m2, base_dir=job.dir)
    assert "missing file" in str(e.value)


def test_schema_fields_are_documented():
    """docs/manifest.md must mention every property the schema defines."""
    here = os.path.dirname(__file__)
    schema = json.load(open(os.path.join(here, "..", "voxy_service", "assets", "manifest.schema.json")))
    doc = open(os.path.join(here, "..", "..", "docs", "manifest.md"), encoding="utf-8").read()
    names = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.get("properties", {}).items():
                names.add(k)
                walk(v)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
    missing = sorted(n for n in names if f"`{n}`" not in doc)
    assert not missing, f"undocumented manifest fields: {missing}"
