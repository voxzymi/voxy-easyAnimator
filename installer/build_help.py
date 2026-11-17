"""Builds the panel's Help page (ae_host/panel/help/index.html) from docs/INSTALL.md, so the guide inside the panel is
the same text as the guide on disk. A small converter: headings, numbered/bulleted lists, bold, code, links, images."""
import html
import os
import re
import shutil
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: f'<img alt="{html.escape(m.group(1), quote=True)}" src="{m.group(2)}">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def ae_versions() -> str:
    import json
    cfg = json.load(open(os.path.join(root, "installer", "supported_ae_versions.json"), encoding="utf-8"))
    names = ", ".join(v["name"] for v in cfg["versions"])
    return names + ("" if cfg["confirmed"] else " (this list is not final)")


def convert(md: str) -> str:
    md = md.replace("{{AE_VERSIONS}}", ae_versions())
    out, lst, para = [], None, []

    def flush_para():
        nonlocal para
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []

    def close_list():
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = None

    for line in md.splitlines():
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        om = re.match(r"^\s*\d+\.\s+(.*)$", line)
        um = re.match(r"^\s*[-*]\s+(.*)$", line)
        if h:
            flush_para(); close_list()
            n = len(h.group(1))
            text = h.group(2)
            am = re.search(r"\s*\{#([a-z0-9-]+)\}\s*$", text)           # an explicit anchor, used by the panel's help buttons
            anchor = am.group(1) if am else slug(re.sub(r"^[0-9. ]+", "", text))
            if am:
                text = text[:am.start()]
            out.append(f'<h{n} id="{anchor}">{inline(text)}</h{n}>')
        elif om or um:
            flush_para()
            want = "ol" if om else "ul"
            if lst != want:
                close_list(); out.append(f"<{want}>"); lst = want
            out.append("<li>" + inline((om or um).group(1)) + "</li>")
        elif not line.strip():
            flush_para(); close_list()
        else:
            if lst and line.startswith("   "):
                out[-1] = out[-1][:-5] + "<br>" + inline(line.strip()) + "</li>"
            else:
                close_list(); para.append(line.strip())
    flush_para(); close_list()
    return "\n".join(out)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voxy EasyAnimator help</title>
<style>
:root{{color-scheme:light dark;--bg:#fff;--ink:#1c1e24;--mute:#5b6274;--line:#d5d8e0;--acc:#1b6fd8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1f2024;--ink:#eceef3;--mute:#a3a9b8;--line:#444855;--acc:#5aa9ff}}}}
body{{background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,"Segoe UI",sans-serif;margin:0}}
main{{max-width:760px;margin:0 auto;padding:16px 20px 60px}}
h1,h2,h3{{line-height:1.25}}h2{{margin-top:2em;border-top:1px solid var(--line);padding-top:.8em}}
img{{max-width:100%;border:1px solid var(--line);border-radius:6px}}code{{background:rgba(127,127,127,.18);padding:1px 5px;border-radius:4px}}
a{{color:var(--acc)}}li{{margin:.35em 0}}
</style></head><body><main>
{body}
</main></body></html>
"""

if __name__ == "__main__":
    src = os.path.join(root, "docs", "INSTALL.md")
    out_dir = os.path.join(root, "ae_host", "panel", "help")
    os.makedirs(out_dir, exist_ok=True)
    body = convert(open(src, encoding="utf-8").read())
    open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(PAGE.format(body=body))
    img = os.path.join(root, "docs", "images")
    if os.path.isdir(img):
        shutil.copytree(img, os.path.join(out_dir, "images"), dirs_exist_ok=True)
    print("wrote", os.path.join(out_dir, "index.html"))
