import base64
import json
import urllib.error
import os
import urllib.request

import pytest

from voxy_service.manifest import validate_manifest


def call(base, method, path, body=None, token="testtoken", headers=None, raw=False):
    req = urllib.request.Request(base + path, method=method, data=body)
    if token:
        req.add_header("X-Voxy-Token", token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"null"))
    except urllib.error.HTTPError as e:
        data = e.read()
        return e.code, (data if raw else json.loads(data or b"null"))


def jpost(base, path, obj, **kw):
    return call(base, "POST", path, json.dumps(obj).encode(), headers={"Content-Type": "application/json"}, **kw)


def test_health_check(server):
    svc, base = server
    st, h = call(base, "GET", "/v1/health", token=None)             # no token needed
    assert st == 200 and h["status"] == "ok"
    assert h["api_version"] == "1.0" and h["manifest_schema"] == "1.0"
    assert set(h["models"]) >= {"general", "hair", "matting", "inpaint"}
    assert h["models"]["general"]["available"] and h["stub_models"] is True
    assert "cuda_available" in h["device"] and h["busy"] is False


def test_everything_else_needs_the_token(server):
    _, base = server
    assert call(base, "GET", "/v1/preflight", token=None)[0] == 401
    assert call(base, "GET", "/v1/preflight", token="wrong")[0] == 401
    assert jpost(base, "/v1/jobs", {"type": "make_layers"}, token=None)[0] == 401
    st, body = call(base, "GET", "/v1/preflight")
    assert st == 200 and body["overall"] in ("ok", "warn", "fail") and body["checks"]


def test_host_header_must_be_local(server):
    _, base = server
    st, _ = call(base, "GET", "/v1/health", token=None, headers={"Host": "evil.example.com"})
    assert st == 401


def test_make_layers_over_http_with_wait(server, png_path):
    svc, base = server
    st, r = jpost(base, "/v1/jobs?wait=1", {"type": "make_layers", "image_path": png_path})
    assert st == 200 and r["status"] == "done"
    m = r["manifest"]
    validate_manifest(m, base_dir=m["job"]["output_dir"])
    assert {g["name"] for g in m["groups"]} >= {"Head", "Face", "Hair", "Torso", "Arms", "Legs"}


def test_async_job_then_poll_and_fetch_files(server, png_path):
    svc, base = server
    st, r = jpost(base, "/v1/jobs", {"type": "make_layers", "image_path": png_path})
    assert st == 202 and r["status"] in ("queued", "running", "done")
    st, r = call(base, "GET", f"/v1/jobs/{r['job_id']}?wait=1&timeout=30")
    assert r["status"] == "done"
    part = r["manifest"]["parts"][0]
    st, data = call(base, "GET", f"/v1/jobs/{r['job_id']}/files/{part['image']}", raw=True)
    assert st == 200 and data[:8] == b"\x89PNG\r\n\x1a\n"


def test_file_route_blocks_path_traversal(server, png_path):
    svc, base = server
    _, r = jpost(base, "/v1/jobs?wait=1", {"type": "make_layers", "image_path": png_path})
    jid = r["job_id"]
    for evil in ("../../settings.json", "..%2F..%2Fsettings.json", "%2e%2e/%2e%2e/etc/passwd", "/etc/passwd"):
        st, _ = call(base, "GET", f"/v1/jobs/{jid}/files/{evil}", raw=True)
        assert st in (400, 404)


def test_image_as_base64_and_multipart(server, png_path):
    svc, base = server
    b = open(png_path, "rb").read()
    st, r = jpost(base, "/v1/jobs?wait=1", {"type": "make_layers", "image_b64": base64.b64encode(b).decode()})
    assert st == 200 and r["manifest"]["job"]["status"] == "ok"
    boundary = "xBOUNDARYx"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"request\"\r\nContent-Type: application/json\r\n\r\n"
            f"{json.dumps({'type': 'make_layers'})}\r\n--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\nContent-Type: image/png\r\n\r\n").encode()
    body += b + f"\r\n--{boundary}--\r\n".encode()
    st, r = call(base, "POST", "/v1/jobs?wait=1", body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert st == 200 and r["manifest"]["job"]["status"] == "ok" and r["manifest"]["parts"]


def test_bad_requests_get_friendly_errors(server):
    _, base = server
    st, r = jpost(base, "/v1/jobs", {"no": "type"})
    assert st == 400 and r["error"]["code"] == "BAD_REQUEST" and r["error"]["what_to_do"]
    st, r = jpost(base, "/v1/jobs", {"type": "nonsense"})
    assert st == 400
    st, r = call(base, "GET", "/v1/jobs/doesnotexist")
    assert st == 404 and r["error"]["code"] == "UNKNOWN_JOB"
    st, r = call(base, "POST", "/v1/jobs", b"{not json", headers={"Content-Type": "application/json"})
    assert st == 400


def test_cancel_over_http(server, png_path):
    from voxy_service.models.stub import StubGeneralModel
    svc, base = server
    svc.registry.override("general", StubGeneralModel(delay_s=10))
    _, r = jpost(base, "/v1/jobs", {"type": "make_layers", "image_path": png_path})
    st, _ = call(base, "POST", f"/v1/jobs/{r['job_id']}/cancel")
    assert st == 200
    _, r2 = call(base, "GET", f"/v1/jobs/{r['job_id']}?wait=1&timeout=10")
    assert r2["status"] == "cancelled"


def test_settings_round_trip(server):
    _, base = server
    assert call(base, "GET", "/v1/settings")[1] == {}
    jpost(base, "/v1/settings", {"x": 1}) if False else call(base, "PUT", "/v1/settings", json.dumps({"autosave_minutes": 5}).encode())
    assert call(base, "GET", "/v1/settings")[1] == {"autosave_minutes": 5}


def test_sample_character_runs_through_the_real_pipeline(server):
    svc, base = server
    st, r = jpost(base, "/v1/jobs?wait=1", {"type": "make_layers", "sample": True})
    m = r["manifest"]
    assert st == 200 and m["job"]["status"] == "ok" and len(m["parts"]) >= 15
    assert m["source"]["width"] == 512 and m["source"]["height"] == 768
    validate_manifest(m, base_dir=m["job"]["output_dir"])
    assert os.path.isfile(os.path.join(os.path.dirname(__file__), "..", "voxy_service", "assets", "SAMPLE_LICENSE.txt"))


def test_picture_urls_can_carry_the_token_but_other_routes_cannot(server):
    svc, base = server
    _, r = jpost(base, "/v1/jobs?wait=1", {"type": "make_layers", "sample": True})
    part = r["manifest"]["parts"][0]["image"]
    st, data = call(base, "GET", f"/v1/jobs/{r['job_id']}/files/{part}?token=testtoken", token=None, raw=True)
    assert st == 200 and data[:4] == b"\x89PNG"
    st, _ = call(base, "GET", f"/v1/jobs/{r['job_id']}/files/{part}?token=wrong", token=None, raw=True)
    assert st == 401
    st, _ = call(base, "GET", "/v1/preflight?token=testtoken", token=None)          # only picture files accept it
    assert st == 401
    st, _ = call(base, "GET", f"/v1/jobs/{r['job_id']}?token=testtoken", token=None)
    assert st == 401
