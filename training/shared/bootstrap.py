"""Bootstrapping loop: predict on real images, correct in the label tool, retrain, record the round.

    # 1. train a first version on synthetic data (training/general/train.py, training/hair/train.py)
    # 2. pre-label the real reference images with it
    python -m training.shared.bootstrap predict --images data/raw --out data/labeled --general models/general/0.1.0 --hair models/hair/0.1.0
    # 3. correct the pre-labels:  python -m training.shared.label_tool.server
    # 4. retrain with the corrected images added (train.py --labeled data/labeled/general  /  data/labeled/hair)
    # 5. record the round
    python -m training.shared.bootstrap round --model general --prelabel-version 0.1.0 --trained-version 0.2.0 --notes "fixed eyebrows"
    python -m training.shared.bootstrap status --out data/labeled

The two label sets (general / hair) are tracked separately.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

import training  # noqa: F401
from voxy_service import schema as S
from voxy_service.cancel import CancelToken
from voxy_service.hair.subdivide import extract_roots, split_hair
from voxy_service.imaging import load_image
from voxy_service.models.stub import StubGeneralModel, StubHairModel

from .label_tool.server import LabelStore, stem_of


def load_general(spec: str):
    if spec == "stub":
        return StubGeneralModel()
    from voxy_service.models.onnx_models import OnnxGeneralModel
    return OnnxGeneralModel(spec, prefer="cuda")


def load_hair(spec: str):
    if spec == "stub":
        return StubHairModel()
    from voxy_service.models.onnx_models import OnnxHairModel
    return OnnxHairModel(spec, prefer="cuda")


def predict(images: str, out: str, general: str, hair: Optional[str], sections: int = 6) -> List[str]:
    """Write pre-labels (never overwrites corrected labels) for every image."""
    store = LabelStore(images, out)
    gm, hm = load_general(general), (load_hair(hair) if hair else None)
    tok = CancelToken()
    done = []
    for item in store.list_images():
        name = item["name"]
        rgba = load_image(store.image_path(name))
        res = gm.predict(rgba, tok)
        d = os.path.dirname(store.path("general", name, "x", pre=True))
        os.makedirs(d, exist_ok=True)
        Image.fromarray(res.labels, "L").save(os.path.join(d, "labels.png"))
        json.dump([{"name": k, "x": v[0], "y": v[1], "visible": True} for k, v in res.keypoints.items()],
                  open(os.path.join(d, "keypoints.json"), "w"), indent=1)
        json.dump({"model": general, "at": dt.datetime.now().isoformat(timespec="seconds")}, open(os.path.join(d, "meta.json"), "w"))
        if hm is not None:
            mask = res.labels == S.CLASS_INDEX["hair"]
            if mask.sum() > 50:
                hres = hm.predict(rgba[:, :, :3], mask, tok)
                roots = extract_roots(hres.roots, mask)
                lab, _ = split_hair(mask, hres.flow, roots, sections, 0.3)
                hd = os.path.dirname(store.path("hair", name, "x", pre=True))
                os.makedirs(hd, exist_ok=True)
                Image.fromarray(lab.astype(np.uint8), "L").save(os.path.join(hd, "subdivisions.png"))
                Image.fromarray((mask * 255).astype(np.uint8), "L").save(os.path.join(hd, "hair_mask.png"))
                json.dump([[float(x), float(y)] for x, y in roots], open(os.path.join(hd, "roots.json"), "w"))
        done.append(name)
    return done


def _rounds_path(out: str) -> str:
    return os.path.join(out, "rounds.json")


def load_rounds(out: str) -> List[Dict[str, Any]]:
    p = _rounds_path(out)
    return json.load(open(p)) if os.path.isfile(p) else []


def labeled_names(out: str, model: str) -> List[str]:
    d = os.path.join(out, model)
    need = "labels.png" if model == "general" else "subdivisions.png"
    return sorted(n for n in (os.listdir(d) if os.path.isdir(d) else []) if os.path.isfile(os.path.join(d, n, need)))


def record_round(out: str, model: str, *, prelabel_version: str, trained_version: str, notes: str = "",
                 metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Append a round: which corrected images were added since the last round of this model."""
    rounds = load_rounds(out)
    prev = {n for r in rounds if r["model"] == model for n in r["images_total"]}
    now = labeled_names(out, model)
    rec = {"round": 1 + sum(1 for r in rounds if r["model"] == model), "model": model,
           "date": dt.date.today().isoformat(), "prelabels_from": prelabel_version, "trained_version": trained_version,
           "images_added": [n for n in now if n not in prev], "images_total": now, "metrics": metrics or {}, "notes": notes}
    rounds.append(rec)
    os.makedirs(out, exist_ok=True)
    json.dump(rounds, open(_rounds_path(out), "w"), indent=1)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--images", default="data/raw"); p.add_argument("--out", default="data/labeled")
    p.add_argument("--general", default="stub", help="models/general/<version> folder, or 'stub'")
    p.add_argument("--hair", default=None, help="models/hair/<version> folder, or 'stub'")
    p.add_argument("--sections", type=int, default=6)
    r = sub.add_parser("round")
    r.add_argument("--out", default="data/labeled"); r.add_argument("--model", choices=["general", "hair"], required=True)
    r.add_argument("--prelabel-version", required=True); r.add_argument("--trained-version", required=True)
    r.add_argument("--notes", default="")
    s = sub.add_parser("status"); s.add_argument("--out", default="data/labeled")
    a = ap.parse_args(argv)
    if a.cmd == "predict":
        names = predict(a.images, a.out, a.general, a.hair, a.sections)
        print(f"pre-labelled {len(names)} images into {a.out}/prelabels")
    elif a.cmd == "round":
        print(json.dumps(record_round(a.out, a.model, prelabel_version=a.prelabel_version, trained_version=a.trained_version,
                                      notes=a.notes), indent=1))
    else:
        for m in ("general", "hair"):
            print(f"{m}: {len(labeled_names(a.out, m))} corrected images, {sum(1 for x in load_rounds(a.out) if x['model'] == m)} rounds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
