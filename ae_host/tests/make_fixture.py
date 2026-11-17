"""Builds the fixture the C++ tests read: real Make Layers / subdivision results from the Python service (stub models),
so the host is always tested against the manifest the service really produces.   usage: make_fixture.py <out dir>"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "service"))
sys.path.insert(0, os.path.join(ROOT, "service", "tests"))

from conftest import make_character, run_job, stub_cfg  # noqa: E402
from voxy_service.imaging import save_png  # noqa: E402
from voxy_service.server import Service  # noqa: E402


def main(out: str) -> None:
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    tmp = tempfile.mkdtemp()
    svc = Service(stub_cfg(), os.path.join(tmp, "data"), app_dir=tmp, token="t")
    img, _ = make_character(h=640, w=400, bg=(238, 228, 246))
    src = os.path.join(tmp, "char.png")
    save_png(src, img)
    job, m = run_job(svc, "make_layers", {"type": "make_layers", "image_path": src, "options": {"background_removal": True, "background_fill": "patch"}})
    assert m["job"]["status"] == "ok", m["errors"]
    shutil.copytree(job.dir, os.path.join(out, "make_layers"))
    sjob, sm = run_job(svc, "subdivide_hair", {"type": "subdivide_hair", "parent_job_id": job.id, "options": {"count": 4}})
    assert sm["job"]["status"] == "ok", sm["errors"]
    shutil.copytree(sjob.dir, os.path.join(out, "subdivide"))
    json.dump({"width": 400, "height": 640}, open(os.path.join(out, "info.json"), "w"))
    print("fixture written to", out, "parts:", len(m["parts"]), "subdivisions:", len(sm["parts"]))


if __name__ == "__main__":
    main(sys.argv[1])
