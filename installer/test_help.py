"""Tests for the help builder: every help topic any part of the product can point at must exist in the guide."""
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "installer"))
sys.path.insert(0, os.path.join(ROOT, "service"))
import build_help  # noqa: E402


def anchors():
    page = build_help.convert(open(os.path.join(ROOT, "docs", "INSTALL.md"), encoding="utf-8").read())
    return set(re.findall(r'id="([a-z0-9-]+)"', page)), page


def topics_in_product():
    from voxy_service.errors import CATALOG
    topics = {v[2] for v in CATALOG.values()}
    for f in ("ae_host/core/types.cpp", "ae_host/panel/js/core/errors.js", "ae_host/panel/js/core/strings.js", "ae_host/panel/js/core/controller.js"):
        text = open(os.path.join(ROOT, f), encoding="utf-8").read()
        topics |= set(re.findall(r"help_topic:\s*'([a-z0-9-]+)'", text))
        topics |= set(re.findall(r'\{"[A-Z_]+",\s*\{"[^"]*",\s*"[^"]*",\s*"([a-z0-9-]+)"\}\}', text))
    from voxy_service.pipeline import WARNING_TEXT
    topics |= {v[2] for v in WARNING_TEXT.values()}
    return topics


def test_every_help_topic_has_a_section_in_the_guide():
    a, _ = anchors()
    missing = sorted(t for t in topics_in_product() if t not in a)
    assert not missing, f"help topics with no section in docs/INSTALL.md: {missing}"


def test_guide_has_the_twelve_numbered_sections_and_step_images():
    md = open(os.path.join(ROOT, "docs", "INSTALL.md"), encoding="utf-8").read()
    nums = re.findall(r"^## (\d+)\.", md, re.M)
    assert nums == [str(i) for i in range(1, 13)]
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12):
        assert f"images/step-{n:02d}.png" in md and os.path.isfile(os.path.join(ROOT, "docs", "images", f"step-{n:02d}.png"))


def test_guide_uses_short_sentences_and_no_internal_terms():
    md = open(os.path.join(ROOT, "docs", "INSTALL.md"), encoding="utf-8").read()
    body = re.sub(r"```.*?```", "", md, flags=re.S)
    assert not re.search(r"\b(manifest|ONNX|AEGP|JSON|CUDA|Vulkan|stack trace)\b", body, re.I)
    for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"[#*`>\[\]()!_{}]|\bimages/\S+", "", body)):
        assert len(sent.split()) <= 40, sent


def test_converter_output_and_placeholder():
    html, page = anchors()[1], None
    out = build_help.convert("# T {#top}\n\n1. One\n2. Two **bold**\n\nText `code` [x](y)\n\n![a](b.png)\n")
    assert '<h1 id="top">T</h1>' in out and "<ol>" in out and "<strong>bold</strong>" in out and "<code>code</code>" in out and '<img alt="a" src="b.png">' in out
    cfg = json.load(open(os.path.join(ROOT, "installer", "supported_ae_versions.json")))
    assert "{{AE_VERSIONS}}" not in anchors()[1] and cfg["versions"][0]["name"] in anchors()[1]
    if not cfg["confirmed"]:
        assert "not final" in anchors()[1]
