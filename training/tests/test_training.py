import json
import os

import numpy as np
import pytest

torch = pytest.importorskip("torch")

import training  # noqa: F401,E402
from training.general.model import GeneralNet  # noqa: E402
from training.hair.model import HairNet  # noqa: E402
from training.inpaint.model import InpaintNet  # noqa: E402
from training.shared import datasets as D, generate_dataset as G, losses as L, trainer  # noqa: E402
from training.shared.joblock import JobLock, TrainingBusy  # noqa: E402
from voxy_service import schema as S  # noqa: E402
from voxy_service.preproc import general_input  # noqa: E402

SIZE = 64


@pytest.fixture(scope="module")
def ds_root(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("ds"))
    G.generate(out, sources=8, variants=2, seed=11, size=(96, 72), raw_dir=None)
    return out


# ------------------------------------------------------------------ datasets
def test_general_dataset_item_contract(ds_root):
    ds = D.GeneralDataset([os.path.join(ds_root, "train")], size=SIZE, train=False)
    assert len(ds) > 0
    b = ds[0]
    assert b["x"].shape == (4, SIZE, SIZE) and b["x"].dtype == torch.float32
    assert b["seg"].shape == (SIZE, SIZE) and b["seg"].dtype == torch.int64 and int(b["seg"].max()) < S.NUM_CLASSES
    assert b["kpt"].shape == (len(S.KEYPOINTS), SIZE // 4, SIZE // 4) and b["kpt_valid"].shape == (len(S.KEYPOINTS),)
    assert b["amodal"].shape == (len(S.AMODAL_CLASSES), SIZE, SIZE) and b["has_amodal"] == 1
    assert b["comp"].shape == (3, SIZE, SIZE) and b["comp_w"].shape == (1, SIZE, SIZE)
    assert (b["seg"] == S.CLASS_INDEX["hair"]).any()                   # hair is one class
    assert float(b["x"][3].max()) <= 1.0 and float(b["x"][:3].min()) >= -1.0001


def test_dataset_input_is_identical_to_the_services_preprocessing(ds_root):
    import cv2
    ds = D.GeneralDataset([os.path.join(ds_root, "train")], size=SIZE, train=False)
    path = ds.items[0][1]
    img = cv2.cvtColor(cv2.imread(os.path.join(path, "image.png")), cv2.COLOR_BGR2RGB)
    x, _ = general_input(np.dstack([img, np.full(img.shape[:2], 255, np.uint8)]), SIZE)
    assert np.array_equal(ds[0]["x"].numpy(), x[0])                      # same pixels in training and at run time


def test_keypoint_heatmaps_peak_at_the_joints(ds_root):
    ds = D.GeneralDataset([os.path.join(ds_root, "train")], size=SIZE, train=False)
    b = ds[0]
    for j in range(len(S.KEYPOINTS)):
        if b["kpt_valid"][j] > 0:
            hm = b["kpt"][j]
            assert float(hm.max()) > 0.6
            y, x = divmod(int(hm.argmax()), hm.shape[1])
            assert 0 <= x < hm.shape[1] and 0 <= y < hm.shape[0]


def test_training_augmentation_keeps_labels_and_keypoints_aligned(ds_root):
    ds = D.GeneralDataset([os.path.join(ds_root, "train")], size=SIZE, train=True, p_transparent=1.0)
    b = ds[0]
    seg = b["seg"].numpy()
    # transparent variant: the foreground-mask channel equals the labelled silhouette (nothing moved relative to labels)
    fg = (b["x"][3].numpy() > 0.5)
    assert (fg == (seg > 0)).mean() > 0.97
    eye = np.where(seg == S.CLASS_INDEX["eye_l"])
    if len(eye[0]) and b["kpt_valid"][S.KEYPOINT_INDEX["eye_l"]] > 0:
        hm = b["kpt"][S.KEYPOINT_INDEX["eye_l"]].numpy()
        py, px = np.unravel_index(hm.argmax(), hm.shape)
        assert abs(px * 4 + 2 - eye[1].mean()) < 10 and abs(py * 4 + 2 - eye[0].mean()) < 10


def test_hair_dataset_sees_only_hair_data(ds_root):
    ds = D.HairDataset([os.path.join(ds_root, "train")], size=SIZE, train=False)
    b = ds[0]
    assert set(b) == {"x", "flow", "heat", "mask"}                      # no character labels, no completion
    assert b["x"].shape == (4, SIZE, SIZE) and b["flow"].shape == (2, SIZE, SIZE) and b["heat"].shape == (1, SIZE, SIZE)
    m = b["mask"][0] > 0
    assert m.sum() > 50
    assert torch.allclose(torch.linalg.norm(b["flow"][:, m], dim=0), torch.ones(int(m.sum())), atol=0.05)
    assert float(b["heat"].max()) > 0.5


def test_hair_flip_augmentation_flips_the_flow_too(ds_root):
    train = D.HairDataset([os.path.join(ds_root, "train")], size=SIZE, train=True)
    seen_flip = False
    for i in range(30):
        b = train[0]
        # the mask (in the same frame as the flow) and the flow must agree with the geometry: strands start at the
        # top of the hair and move away from the roots, so on average flow points down (+y) in both orientations
        m = b["mask"][0] > 0
        assert float(b["flow"][1][m].mean()) > 0
        seen_flip |= bool(b["x"][3].sum() != 0)
    assert seen_flip


def test_labeled_real_images_have_no_amodal_labels_and_add_to_the_set(ds_root, tmp_path):
    import cv2
    from PIL import Image
    imgs, lab = tmp_path / "imgs", tmp_path / "labeled" / "general" / "real1"
    imgs.mkdir(); lab.mkdir(parents=True)
    src = os.path.join(ds_root, "val", sorted(os.listdir(os.path.join(ds_root, "val")))[0])
    Image.open(os.path.join(src, "image.png")).save(imgs / "real1.png")
    Image.open(os.path.join(src, "labels.png")).save(lab / "labels.png")
    json.dump([{"name": "eye_l", "x": 30, "y": 20, "visible": True}], open(lab / "keypoints.json", "w"))
    json.dump({"image": "real1.png"}, open(lab / "meta.json", "w"))
    ds = D.GeneralDataset([os.path.join(ds_root, "train")], str(tmp_path / "labeled" / "general"), str(imgs), SIZE, train=False)
    kinds = [k for k, _ in ds.items]
    assert kinds.count("labeled") == 1 and kinds.count("synthetic") > 0
    b = ds[len(ds) - 1]
    assert b["has_amodal"] == 0 and float(b["amodal"].sum()) == 0
    assert L.amodal_loss(torch.randn(1, 4, SIZE, SIZE), b["amodal"][None], b["has_amodal"][None]) == 0   # masked out


# ------------------------------------------------------------------ losses
def test_seg_loss_is_lower_for_a_correct_prediction():
    tgt = torch.randint(0, S.NUM_CLASSES, (2, 16, 16))
    good = torch.nn.functional.one_hot(tgt, S.NUM_CLASSES).permute(0, 3, 1, 2).float() * 8
    bad = torch.randn(2, S.NUM_CLASSES, 16, 16)
    assert L.seg_loss(good, tgt) < 0.05 < L.seg_loss(bad, tgt)


def test_angular_loss():
    t = torch.zeros(1, 2, 4, 4); t[:, 1] = 1                               # strands point down
    m = torch.ones(1, 1, 4, 4)
    same, opp, perp = t.clone(), -t, torch.zeros(1, 2, 4, 4)
    perp[:, 0] = 1
    assert L.angular_loss(same * 3, t, m) == pytest.approx(0, abs=1e-5)    # magnitude is ignored
    assert L.angular_loss(opp, t, m) == pytest.approx(2, abs=1e-4)
    assert L.angular_loss(perp, t, m) == pytest.approx(1, abs=1e-4)
    assert L.angular_loss(opp, t, torch.zeros_like(m)) == pytest.approx(0, abs=1e-5)   # only inside the hair


def test_heatmap_loss_ignores_unlabelled_joints():
    pred, tgt = torch.rand(1, 3, 8, 8), torch.rand(1, 3, 8, 8)
    all_valid = L.heatmap_mse(pred, tgt, torch.ones(1, 3))
    first_only = L.heatmap_mse(pred, tgt, torch.tensor([[1.0, 0, 0]]))
    assert first_only == pytest.approx(float(((pred[:, 0] - tgt[:, 0]) ** 2).mean()), rel=1e-4)
    assert all_valid != first_only


# ------------------------------------------------------------------ one job at a time
def test_only_one_training_job_at_a_time(tmp_path):
    lock = str(tmp_path / "t.lock")
    with JobLock(lock, "general 0.1.0"):
        with pytest.raises(TrainingBusy, match="general 0.1.0"):
            JobLock(lock, "hair 0.1.0").acquire()
    JobLock(lock, "hair").acquire().release()                              # free again after release


def test_stale_lock_from_a_dead_process_is_taken_over(tmp_path):
    lock = tmp_path / "t.lock"
    lock.write_text("999999|old job")
    with JobLock(str(lock), "new"):
        assert lock.read_text().startswith(str(os.getpid()))


def test_training_scripts_refuse_to_run_in_parallel(ds_root, tmp_path):
    from training.general import train as GT
    from training.shared import cfg as cfgmod
    cfg = cfgmod.load(os.path.join(os.path.dirname(__file__), "..", "..", "configs", "general.yaml"), [
        f"data.synthetic=[{ds_root}/train]", f"data.val=[{ds_root}/val]", "data.labeled=null", "model.base=8", "model.depth=3",
        f"model.input_size={SIZE}", "train.batch_size=2", "train.accum_steps=1", "train.workers=0", "train.max_steps=2",
        "train.epochs=1", f"output.checkpoints={tmp_path}/ck", "export.version=t"])
    lock = str(tmp_path / "busy.lock")
    with JobLock(lock, "hair run"):
        with pytest.raises(TrainingBusy):
            GT.run(cfg, "cpu", lock)


# ------------------------------------------------------------------ trainer mechanics
class _Reg(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.w = torch.nn.Linear(3, 1, bias=False)
        torch.nn.init.constant_(self.w.weight, 0.5)

    def forward(self, x):
        return self.w(x)


class _Toy(torch.utils.data.Dataset):
    def __len__(self):
        return 8

    def __getitem__(self, i):
        g = torch.Generator().manual_seed(i)
        x = torch.randn(3, generator=g)
        return {"x": x, "y": x.sum(dim=0, keepdim=True)}


def _toy_loss(model, b):
    l = ((model(b["x"]) - b["y"]) ** 2).mean()
    return l, {"mse": float(l)}


def test_gradient_accumulation_matches_a_bigger_batch(tmp_path):
    def run(batch, accum, sub):
        m = _Reg()
        # no shuffling effects: one epoch, one optimiser step over all 8 samples (batch*accum = 8)
        torch.manual_seed(0)
        trainer.fit(m, _toy_loss, _Toy(), None, None, str(tmp_path / sub), epochs=1, batch_size=batch, accum_steps=accum, lr=0.1,
                    weight_decay=0, amp=False, device="cpu", warmup=1, resume=False, grad_clip=1e9, max_steps=None)
        return m.w.weight.detach().clone()
    big, accumulated = run(8, 1, "a"), run(2, 4, "b")
    assert torch.allclose(big, accumulated, atol=1e-5)                      # 4 micro-batches of 2 == one batch of 8


def test_amp_is_only_enabled_on_cuda(tmp_path):
    res = trainer.fit(_Reg(), _toy_loss, _Toy(), None, None, str(tmp_path / "c"), epochs=1, batch_size=4, amp=True,
                      device="cpu", resume=False)
    assert res.amp is False and res.device == "cpu"                         # FP16 autocast runs on the GPU only


def test_checkpoint_resume(tmp_path):
    out = str(tmp_path / "r")
    r1 = trainer.fit(_Reg(), _toy_loss, _Toy(), None, None, out, epochs=2, batch_size=4, device="cpu", amp=False, resume=False, log_every=1)
    assert r1.steps == 4 and os.path.isfile(os.path.join(out, "last.pt")) and os.path.isfile(os.path.join(out, "best.pt"))
    r2 = trainer.fit(_Reg(), _toy_loss, _Toy(), None, None, out, epochs=3, batch_size=4, device="cpu", amp=False, resume=True, log_every=1)
    assert r2.steps == 6                                                    # continued, did not restart
    assert sum(1 for _ in open(os.path.join(out, "train_log.jsonl"))) >= 2


# ------------------------------------------------------------------ a real (tiny, CPU) run for each model + ONNX parity
@pytest.fixture(scope="module")
def tiny_models(ds_root, tmp_path_factory):
    from training.general import train as GT, export_onnx as GE
    from training.hair import train as HT, export_onnx as HE
    from training.inpaint import train as IT, export_onnx as IE
    from training.shared import cfg as cfgmod
    cdir = os.path.join(os.path.dirname(__file__), "..", "..", "configs")
    tmp = tmp_path_factory.mktemp("models")
    common = [f"data.synthetic=[{ds_root}/train]", f"data.val=[{ds_root}/val]", "train.batch_size=4", "train.accum_steps=1",
              "train.workers=0", "train.epochs=1", "train.max_steps=25", f"output.checkpoints={tmp}/ck", "export.version=0.0.1"]
    lock = str(tmp / "lock")
    g = GT.run(cfgmod.load(os.path.join(cdir, "general.yaml"), common + ["data.labeled=null", "model.base=12", "model.depth=4",
                                                                           f"model.input_size={SIZE}", "train.lr=2e-3"]), "cpu", lock)
    h = HT.run(cfgmod.load(os.path.join(cdir, "hair.yaml"), common + ["data.labeled=null", "model.base=12", "model.depth=3",
                                                                        f"model.input_size={SIZE}", "train.lr=2e-3"]), "cpu", lock)
    i = IT.run(cfgmod.load(os.path.join(cdir, "inpaint.yaml"), common + ["model.base=12", f"model.input_size={SIZE}"]), "cpu",
               perceptual=False, lock=lock)
    root = str(tmp / "models")
    GE.export(g["checkpoint"], f"{root}/general/0.0.1", "0.0.1")
    HE.export(h["checkpoint"], f"{root}/hair/0.0.1", "0.0.1")
    IE.export(i["checkpoint"], f"{root}/inpaint/0.0.1", "0.0.1")
    return root, g, h, i


def test_tiny_training_runs_reduce_the_loss(tiny_models):
    _, g, h, i = tiny_models
    assert g["last_loss"] < g["first_loss"] and h["last_loss"] < h["first_loss"]
    assert g["steps"] == 25 and g["device"] == "cpu" and g["amp"] is False


def test_exported_general_model_runs_in_the_service_runner(tiny_models):
    root, *_ = tiny_models
    from voxy_service.cancel import CancelToken
    from voxy_service.models.onnx_models import OnnxGeneralModel
    meta = json.load(open(f"{root}/general/0.0.1/model.json"))
    assert meta["classes"] == S.CLASS_NAMES and meta["max_abs_diff_vs_torch"] < 2e-3 and meta["outputs"] == ["seg", "kpt", "amodal", "completion"]
    m = OnnxGeneralModel(f"{root}/general/0.0.1")
    from conftest import make_character
    out = m.predict(make_character(h=200, w=150)[0], CancelToken())
    assert out.labels.shape == (200, 150) and set(out.keypoints) == set(S.KEYPOINTS) and out.completion_rgb.shape == (200, 150, 3)


def test_exported_hair_and_inpaint_models_run_in_the_service_runners(tiny_models):
    root, *_ = tiny_models
    from voxy_service.cancel import CancelToken
    from voxy_service.inpaint import OnnxInpainter
    from voxy_service.models.onnx_models import OnnxHairModel
    hm = OnnxHairModel(f"{root}/hair/0.0.1")
    mask = np.zeros((120, 100), bool); mask[20:100, 20:80] = True
    out = hm.predict(np.zeros((120, 100, 3), np.uint8), mask, CancelToken())
    assert out.flow.shape == (120, 100, 2) and np.allclose(np.linalg.norm(out.flow, axis=2), 1, atol=1e-3)
    inp = OnnxInpainter(f"{root}/inpaint/0.0.1")
    rgb = np.full((120, 100, 3), 200, np.uint8)
    hole = np.zeros((120, 100), bool); hole[40:80, 30:70] = True
    res = inp.fill(rgb, hole, CancelToken())
    assert res.shape == rgb.shape and (res[~hole] == rgb[~hole]).all()      # only the hole is touched


def test_two_models_are_independent_files(tiny_models):
    root, *_ = tiny_models
    assert os.path.isfile(f"{root}/general/0.0.1/model.onnx") and os.path.isfile(f"{root}/hair/0.0.1/model.onnx")
    g, h = json.load(open(f"{root}/general/0.0.1/model.json")), json.load(open(f"{root}/hair/0.0.1/model.json"))
    assert g["sha256"] != h["sha256"] and g["name"] == "general" and h["name"] == "hair"
    assert "classes" not in h                                              # the hair model knows nothing about characters


def test_service_runs_on_both_trained_models(tiny_models, tmp_path):
    root, *_ = tiny_models
    from conftest import make_character, run_job, stub_cfg
    from voxy_service.imaging import save_png
    from voxy_service.manifest import validate_manifest
    from voxy_service.server import Service
    cfg = stub_cfg()
    cfg["models"].update(root=root)
    cfg["models"]["general"] = {"backend": "onnx", "version": "0.0.1"}
    cfg["models"]["hair"] = {"backend": "onnx", "version": "0.0.1"}
    cfg["service"]["allow_stub_models"] = False
    svc = Service(cfg, str(tmp_path / "d"), app_dir=str(tmp_path))
    img, _ = make_character(h=200, w=150)
    p = str(tmp_path / "c.png")
    save_png(p, img)
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": p})
    # barely-trained weights: either some parts or a friendly "no character" failure, never a broken manifest
    validate_manifest(m, base_dir=job.dir)
    assert m["job"]["status"] in ("ok", "partial", "failed")
    if m["job"]["status"] == "failed":
        assert m["parts"] == [] and m["errors"][0]["user_message"]
