"""Every message a user can read from the service or the AE host: short sentences, no internal terms, an action to take."""
import os
import re

import pytest

from voxy_service.errors import CATALOG
from voxy_service.pipeline import WARNING_TEXT

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JARGON = re.compile(r"\b(manifest|onnx|aegp|json|api|stack ?trace|exception|traceback|null|undefined|cuda|vulkan|inference|tensor|"
                    r"http|localhost|token|ncnn|uxp|cep|extendscript|stub|checkpoint)\b", re.I)


def check(text: str, where: str, limit_chars: int = 150):
    assert text.strip(), where
    assert len(text) <= limit_chars, f"{where} is {len(text)} characters: {text!r}"
    assert not JARGON.search(text), f"internal term in {where}: {text!r}"
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        assert len(sentence.split()) <= 24, f"long sentence in {where}: {sentence!r}"


@pytest.mark.parametrize("code", sorted(CATALOG))
def test_service_error_texts(code):
    happened, todo, topic = CATALOG[code]
    check(happened, f"{code}.what"); check(todo, f"{code}.todo")
    assert re.fullmatch(r"[a-z0-9-]+", topic)
    assert todo.rstrip().endswith("."), code               # an action, as a sentence


@pytest.mark.parametrize("code", sorted(WARNING_TEXT))
def test_service_warning_texts(code):
    for i, t in enumerate(WARNING_TEXT[code][:2]):
        check(t, f"{code}[{i}]")


def test_preflight_texts(tmp_path):
    from conftest import stub_cfg
    from voxy_service.server import Service
    svc = Service(stub_cfg(), str(tmp_path / "d"), app_dir=str(tmp_path), token="t")
    for c in svc.preflight()["checks"]:
        for k in ("title", "message", "what_to_do"):
            if c[k]:
                check(c[k], f"preflight.{c['id']}.{k}")


def test_host_error_texts():
    src = open(os.path.join(ROOT, "ae_host", "core", "types.cpp"), encoding="utf-8").read()
    rows = re.findall(r'\{"([A-Z_]+)", \{"([^"]*)", "([^"]*)", "([a-z0-9-]+)"\}\}', src)
    assert len(rows) >= 18
    for code, what, todo, topic in rows:
        check(what, f"host {code}.what"); check(todo, f"host {code}.todo")
