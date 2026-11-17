"""The ONNX path, tested without any trained weights: a tiny hand-built graph with the same
input/output contract as the real models (see docs/models.md) runs through the real runner,
pre/post-processing, pipeline and manifest validation on the CPU."""
import json
import os

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from conftest import make_character, run_job, stub_cfg
from voxy_service import schema as S
from voxy_service.cancel import CancelToken
from voxy_service.imaging import save_png
from voxy_service.manifest import validate_manifest
from voxy_service.models.onnx_models import OnnxGeneralModel, OnnxHairModel, make_session
from voxy_service.server import Service

SIZE = 128


def _conv(name, x, w, b, out):
    return [helper.make_node("Conv", [x, f"{name}_w", f"{name}_b"], [out], kernel_shape=[1, 1])], [
        numpy_helper.from_array(w.astype(np.float32), f"{name}_w"), numpy_helper.from_array(b.astype(np.float32), f"{name}_b")]


def build_general(path):
    C, K, A = S.NUM_CLASSES, len(S.KEYPOINTS), len(S.AMODAL_CLASSES)
    nodes, inits = [], []
    w = np.zeros((C, 4, 1, 1)); b = np.zeros(C)
    b[0] = 5.0; w[S.CLASS_INDEX["hair"], 3, 0, 0] = 10.0     # foreground -> hair; background -> class 0
    n, i = _conv("seg", "input", w, b, "seg"); nodes += n; inits += i
    w = np.zeros((K, 4, 1, 1)); w[:, 3, 0, 0] = 1.0
    n, i = _conv("kpt0", "input", w, np.zeros(K), "kpt_full"); nodes += n; inits += i
    nodes.append(helper.make_node("AveragePool", ["kpt_full"], ["kpt"], kernel_shape=[4, 4], strides=[4, 4]))
    w = np.zeros((A, 4, 1, 1)); w[:, 3, 0, 0] = 2.0
    n, i = _conv("am", "input", w, np.full(A, -1.0), "amodal"); nodes += n; inits += i
    w = np.zeros((3, 4, 1, 1))
    for c in range(3):
        w[c, c, 0, 0] = 0.5
    n, i = _conv("cp", "input", w, np.zeros(3), "comp_pre"); nodes += n; inits += i
    nodes.append(helper.make_node("Sigmoid", ["comp_pre"], ["completion"]))
    outs = [helper.make_tensor_value_info("seg", TensorProto.FLOAT, [1, C, SIZE, SIZE]),
            helper.make_tensor_value_info("kpt", TensorProto.FLOAT, [1, K, SIZE // 4, SIZE // 4]),
            helper.make_tensor_value_info("amodal", TensorProto.FLOAT, [1, A, SIZE, SIZE]),
            helper.make_tensor_value_info("completion", TensorProto.FLOAT, [1, 3, SIZE, SIZE])]
    g = helper.make_graph(nodes, "general", [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 4, SIZE, SIZE])],
                          outs, initializer=inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)]); m.ir_version = 8
    onnx.save(m, path)


def build_hair(path):
    nodes, inits = [], []
    w = np.zeros((2, 4, 1, 1)); b = np.array([0.0, 1.0])          # flow points down
    n, i = _conv("fl", "input", w, b, "flow"); nodes += n; inits += i
    w = np.zeros((1, 4, 1, 1)); w[0, 3, 0, 0] = 4.0
    n, i = _conv("rt", "input", w, np.array([-2.0]), "roots"); nodes += n; inits += i
    g = helper.make_graph(nodes, "hair", [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 4, SIZE, SIZE])],
                          [helper.make_tensor_value_info("flow", TensorProto.FLOAT, [1, 2, SIZE, SIZE]),
                           helper.make_tensor_value_info("roots", TensorProto.FLOAT, [1, 1, SIZE, SIZE])], initializer=inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)]); m.ir_version = 8
    onnx.save(m, path)


@pytest.fixture()
def model_root(tmp_path):
    root = tmp_path / "models"
    for name, builder, extra in (("general", build_general, {"classes": S.CLASS_NAMES}), ("hair", build_hair, {})):
        d = root / name / "0.0.1"
        d.mkdir(parents=True)
        builder(str(d / "model.onnx"))
        (d / "model.json").write_text(json.dumps({"version": "0.0.1", "input_size": SIZE, **extra}))
    return str(root)


def test_session_uses_cpu_when_there_is_no_gpu(model_root):
    sess, device, warning = make_session(os.path.join(model_root, "general", "0.0.1", "model.onnx"), prefer="cuda")
    assert device == "cpu"
    assert warning in ("GPU_NOT_AVAILABLE", "GPU_NOT_USED")      # the panel can warn the user


def test_general_runner_outputs_match_the_contract(model_root):
    m = OnnxGeneralModel(os.path.join(model_root, "general", "0.0.1"))
    img, tr = make_character(h=300, w=200)
    out = m.predict(img, CancelToken())
    assert out.labels.shape == (300, 200) and out.labels.dtype == np.uint8
    hair = S.CLASS_INDEX["hair"]
    assert set(np.unique(out.labels)) <= {0, hair}
    assert (out.labels[tr.labels > 0] == hair).mean() > 0.95     # the toy graph maps foreground to hair
    assert set(out.keypoints) == set(S.KEYPOINTS)
    assert all(0 <= x < 200 and 0 <= y < 300 and 0 <= s <= 1 for x, y, s in out.keypoints.values())
    assert set(out.amodal) == set(S.AMODAL_CLASSES) and out.amodal["head"].shape == (300, 200)
    assert out.completion_rgb.shape == (300, 200, 3) and out.completion_rgb.dtype == np.uint8
    assert m.info.backend == "onnx" and m.info.device == "cpu" and m.info.version == "0.0.1"


def test_general_runner_rejects_a_model_for_another_class_list(model_root):
    p = os.path.join(model_root, "general", "0.0.1", "model.json")
    meta = json.load(open(p))
    meta["classes"] = ["background", "x"]
    json.dump(meta, open(p, "w"))
    with pytest.raises(Exception) as e:
        OnnxGeneralModel(os.path.dirname(p))
    assert getattr(e.value, "code", "") == "GENERAL_MODEL_FAILED"


def test_hair_runner_outputs_match_the_contract(model_root):
    m = OnnxHairModel(os.path.join(model_root, "hair", "0.0.1"))
    mask = np.zeros((200, 160), bool)
    mask[30:150, 30:130] = True
    out = m.predict(np.zeros((200, 160, 3), np.uint8), mask, CancelToken())
    assert out.flow.shape == (200, 160, 2) and out.roots.shape == (200, 160)
    assert np.allclose(np.linalg.norm(out.flow, axis=2), 1.0, atol=1e-3)
    assert (out.flow[40:140, 40:120, 1] > 0.99).all()            # points down inside the hair
    assert out.roots[mask].min() >= 0 and out.roots.max() <= 1


def test_full_pipeline_on_onnx_models_with_cpu_warning(model_root, tmp_path):
    cfg = stub_cfg()
    cfg["models"].update(root=model_root)
    cfg["models"]["general"] = {"backend": "onnx", "version": "0.0.1"}
    cfg["models"]["hair"] = {"backend": "onnx", "version": "0.0.1"}
    cfg["service"]["allow_stub_models"] = False
    svc = Service(cfg, str(tmp_path / "data"), app_dir=str(tmp_path))
    img, _ = make_character(h=320, w=200)
    p = str(tmp_path / "c.png")
    save_png(p, img)
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": p})
    assert m["job"]["status"] == "ok", m["errors"]
    assert m["models"]["general"] == {"name": "general", "version": "0.0.1", "backend": "onnx", "device": "cpu"}
    assert any(w["code"] in ("GPU_NOT_AVAILABLE", "GPU_NOT_USED") for w in m["warnings"])   # slow-mode warning
    assert all(p_["produced_by"]["backend"] == "onnx" for p_ in m["parts"])
    validate_manifest(m, base_dir=job.dir)
    health = svc.health()
    assert health["stub_models"] is False and health["models"]["general"]["available"]
    # and the hair model runs on the same job's Hair part
    sj, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": job.id})
    assert sm["models"]["hair"]["backend"] == "onnx"


def test_missing_model_file_is_a_friendly_error(tmp_path):
    cfg = stub_cfg()
    cfg["models"]["general"] = {"backend": "onnx", "version": "9.9.9"}
    cfg["service"]["allow_stub_models"] = False
    svc = Service(cfg, str(tmp_path / "d"), app_dir=str(tmp_path))
    assert svc.health()["models"]["general"]["available"] is False
    img, _ = make_character(h=200, w=120)
    p = str(tmp_path / "c.png")
    save_png(p, img)
    _, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": p})
    assert m["errors"][0]["code"] == "GENERAL_MODEL_MISSING" and m["parts"] == []
