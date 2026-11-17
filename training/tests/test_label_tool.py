import base64
import json
import os
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest
from PIL import Image

import training  # noqa: F401
from training.shared import bootstrap
from training.shared.label_tool.server import LabelStore, make_handler
from http.server import ThreadingHTTPServer
from voxy_service import schema as S


@pytest.fixture()
def env(tmp_path):
    imgs, out = tmp_path / "imgs", tmp_path / "labeled"
    imgs.mkdir()
    from voxy_service.template import Layout, render_template
    tr = render_template(Layout(), (320, 200), 300, (100 - 144, 5))
    arr = np.full((320, 200, 3), 240, np.uint8)
    arr[tr.labels > 0] = (120, 90, 160)
    Image.fromarray(arr).save(imgs / "a.png")
    Image.fromarray(arr).save(imgs / "b.png")
    return str(imgs), str(out), tr


def b64(a):
    return base64.b64encode(np.ascontiguousarray(a, np.uint8).tobytes()).decode()


def test_two_label_sets_are_stored_separately(env):
    imgs, out, tr = env
    st = LabelStore(imgs, out)
    h, w = 320, 200
    general = tr.labels.copy()
    st.save("general", "a.png", {"width": w, "height": h, "labels": b64(general),
                                 "points": [{"name": "eye_l", "x": 10, "y": 20}]})
    hair = general == S.CLASS_INDEX["hair"]
    subs = np.where(hair, 1 + (np.arange(w)[None, :] * 3 // w), 0).astype(np.uint8)
    st.save("hair", "a.png", {"width": w, "height": h, "labels": b64(subs), "hair_mask": b64(hair),
                              "points": [{"x": 100, "y": 10}]})
    gdir, hdir = os.path.join(out, "general", "a"), os.path.join(out, "hair", "a")
    assert sorted(os.listdir(gdir)) == ["keypoints.json", "labels.png", "meta.json"]
    assert sorted(os.listdir(hdir)) == ["hair_mask.png", "meta.json", "roots.json", "subdivisions.png"]
    g = np.array(Image.open(os.path.join(gdir, "labels.png")))
    assert np.array_equal(g, general) and (g == S.CLASS_INDEX["hair"]).sum() == hair.sum()   # hair is one class here
    s = np.array(Image.open(os.path.join(hdir, "subdivisions.png")))
    assert s.max() == 3 and ((s > 0) == hair).all()                                         # sections only inside the hair
    meta = json.load(open(os.path.join(gdir, "meta.json")))
    assert meta["groups"]["hair"] == "Hair" and meta["groups"]["arm_l"] == "Arms"          # the group comes with the class
    assert st.list_images()[0] == {"name": "a.png", "general": True, "hair": True}
    assert st.list_images()[1] == {"name": "b.png", "general": False, "hair": False}


def test_save_rejects_bad_labels(env):
    imgs, out, tr = env
    st = LabelStore(imgs, out)
    ok = {"width": 200, "height": 320}
    with pytest.raises(ValueError, match="size"):
        st.save("general", "a.png", {"width": 10, "height": 10, "labels": b64(np.zeros(100))})
    with pytest.raises(ValueError, match="length"):
        st.save("general", "a.png", {**ok, "labels": b64(np.zeros(5))})
    bad = tr.labels.copy(); bad[0, 0] = 200
    with pytest.raises(ValueError, match="class id"):
        st.save("general", "a.png", {**ok, "labels": b64(bad)})
    with pytest.raises(ValueError, match="unknown key point"):
        st.save("general", "a.png", {**ok, "labels": b64(tr.labels), "points": [{"name": "knee_x", "x": 1, "y": 1}]})
    subs = np.ones((320, 200), np.uint8)
    with pytest.raises(ValueError, match="inside the hair"):
        st.save("hair", "a.png", {**ok, "labels": b64(subs), "hair_mask": b64(np.zeros((320, 200)))})
    assert not os.path.exists(os.path.join(out, "general"))                                 # nothing half-written


def test_paths_cannot_escape(env):
    imgs, out, _ = env
    st = LabelStore(imgs, out)
    with pytest.raises(FileNotFoundError):
        st.image_path("../../etc/passwd")


def test_prelabels_then_corrections(env):
    imgs, out, _ = env
    names = bootstrap.predict(imgs, out, "stub", "stub")
    assert names == ["a.png", "b.png"]
    st = LabelStore(imgs, out)
    s = st.load("general", "a.png")
    assert s["prelabel"] and not s["saved"] and s["labels"]                                  # pre-labels are offered
    assert len(s["points"]) == len(S.KEYPOINTS)
    assert st.list_images()[0]["general"] is False                                           # but are not "labelled" yet
    h = st.load("hair", "a.png")
    assert h["prelabel"] and h["hair_mask"]
    st.save("general", "a.png", {"width": 200, "height": 320, "labels": s["labels"], "points": s["points"]})
    assert st.load("general", "a.png")["saved"] is True                                      # saved corrections win
    assert st.list_images()[0]["general"] is True


def test_rounds_are_recorded_per_model(env):
    imgs, out, tr = env
    st = LabelStore(imgs, out)
    st.save("general", "a.png", {"width": 200, "height": 320, "labels": b64(tr.labels), "points": []})
    r1 = bootstrap.record_round(out, "general", prelabel_version="0.1.0", trained_version="0.2.0", notes="first")
    assert r1["round"] == 1 and r1["images_added"] == ["a"]          # label folders are named by image stem
    st.save("general", "b.png", {"width": 200, "height": 320, "labels": b64(tr.labels), "points": []})
    r2 = bootstrap.record_round(out, "general", prelabel_version="0.2.0", trained_version="0.3.0")
    assert r2["round"] == 2 and r2["images_added"] == ["b"] and r2["images_total"] == ["a", "b"]
    r3 = bootstrap.record_round(out, "hair", prelabel_version="0.1.0", trained_version="0.2.0")
    assert r3["round"] == 1 and r3["images_total"] == []                                     # separate counters per model
    assert len(bootstrap.load_rounds(out)) == 3


def test_http_api(env):
    imgs, out, tr = env
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(LabelStore(imgs, out)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        sch = json.load(urllib.request.urlopen(base + "/api/schema"))
        assert sch["classes"] == S.CLASS_NAMES and sch["groups"]["hair"] == "Hair"
        assert [i["name"] for i in json.load(urllib.request.urlopen(base + "/api/images"))] == ["a.png", "b.png"]
        assert urllib.request.urlopen(base + "/api/image/a.png").read()[:4] == b"\x89PNG"
        body = json.dumps({"width": 200, "height": 320, "labels": b64(tr.labels), "points": []}).encode()
        assert json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/save/a.png?kind=general", body)))["ok"]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/api/save/a.png?kind=general", b'{"width":1,"height":1,"labels":""}'))
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/image/..%2F..%2Fetc%2Fpasswd")
        assert e.value.code == 404
    finally:
        httpd.shutdown()
