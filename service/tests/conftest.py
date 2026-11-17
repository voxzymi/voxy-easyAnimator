import os
import sys
import threading

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from voxy_service import schema as S  # noqa: E402
from voxy_service.config import load_config  # noqa: E402
from voxy_service.models.registry import ModelRegistry  # noqa: E402
from voxy_service.server import Service, make_server  # noqa: E402
from voxy_service.template import Layout, render_template  # noqa: E402

_rng = np.random.default_rng(7)
PALETTE = _rng.integers(30, 225, (S.NUM_CLASSES, 3)).astype(np.uint8)


def make_character(h=640, w=400, transparent=False, bg=(250, 250, 250), layout=None):
    """A flat-colour test character drawn from the template. Returns (rgba, TemplateRender)."""
    tr = render_template(layout or Layout(), (h, w), h * 0.96, (w / 2 - 0.48 * h, h * 0.02))
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = bg
    img[..., 3] = 255
    fg = tr.labels > 0
    img[fg, :3] = PALETTE[tr.labels][fg]
    if transparent:
        img[~fg, 3] = 0
    return img, tr


@pytest.fixture()
def character():
    return make_character()


@pytest.fixture()
def png_path(tmp_path, character):
    from voxy_service.imaging import save_png
    p = str(tmp_path / "char.png")
    save_png(p, character[0])
    return p


def stub_cfg(**over):
    cfg = load_config(None, over)
    cfg["models"]["general"]["backend"] = "stub"
    cfg["models"]["hair"]["backend"] = "stub"
    return cfg


@pytest.fixture()
def svc(tmp_path):
    cfg = stub_cfg()
    s = Service(cfg, str(tmp_path / "data"), app_dir=str(tmp_path / "app"), token="testtoken")
    yield s
    s.jobs._pool.shutdown(wait=False, cancel_futures=True)


@pytest.fixture()
def server(svc):
    httpd = make_server(svc)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield svc, f"http://127.0.0.1:{svc.port}"
    httpd.shutdown()


def run_job(svc, kind, req, timeout=60):
    job = svc.jobs.submit(kind, req)
    svc.jobs.wait(job.id, timeout)
    return job, job.manifest
