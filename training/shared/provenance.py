"""Provenance log for data/raw: which images may go into a training set that will be shared."""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

FIELDS = ["file", "source", "author", "license", "license_url", "owner_permission", "acquired", "redistributable",
          "sha256", "notes"]
SHAREABLE_LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT", "Apache-2.0", "BSD-3-Clause", "public-domain",
                      "project-original", "owner-permission"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


@dataclass
class Status:
    file: str
    state: str            # shareable | internal_only | unknown | hash_mismatch
    reason: str


def load(raw_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(raw_dir, "PROVENANCE.csv")
    if not os.path.isfile(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = {r["file"].replace("\\", "/"): r for r in csv.DictReader(f) if r.get("file")}
    return rows


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status_of(raw_dir: str, rel: str, rows: Optional[Dict[str, Dict[str, str]]] = None) -> Status:
    rows = load(raw_dir) if rows is None else rows
    rel = rel.replace("\\", "/")
    row = rows.get(rel)
    if row is None:
        return Status(rel, "unknown", "no row in PROVENANCE.csv")
    lic = (row.get("license") or "").strip()
    if not lic:
        return Status(rel, "unknown", "license is empty")
    want = (row.get("sha256") or "").strip().lower()
    full = os.path.join(raw_dir, rel)
    if want and os.path.isfile(full) and sha256_file(full) != want:
        return Status(rel, "hash_mismatch", "file does not match the logged sha256")
    if lic == "owner-permission" and not (row.get("owner_permission") or "").strip():
        return Status(rel, "unknown", "owner-permission needs the 'owner_permission' column filled in")
    if lic in SHAREABLE_LICENSES and (row.get("redistributable") or "").strip().lower() == "yes":
        return Status(rel, "shareable", lic)
    return Status(rel, "internal_only", f"license {lic} / redistributable={row.get('redistributable') or '?'}")


def audit(raw_dir: str) -> List[Status]:
    rows = load(raw_dir)
    out: List[Status] = []
    for root, _, files in os.walk(raw_dir):
        for fn in sorted(files):
            if fn.lower().endswith(IMAGE_EXT):
                rel = os.path.relpath(os.path.join(root, fn), raw_dir).replace(os.sep, "/")
                out.append(status_of(raw_dir, rel, rows))
    for rel in rows:
        if not os.path.isfile(os.path.join(raw_dir, rel)):
            out.append(Status(rel, "unknown", "logged but the file is missing"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit data/raw against PROVENANCE.csv")
    ap.add_argument("cmd", choices=["audit"])
    ap.add_argument("--raw", default=os.path.join(os.path.dirname(__file__), "..", "..", "data", "raw"))
    args = ap.parse_args(argv)
    res = audit(args.raw)
    if not res:
        print("data/raw has no images and no provenance rows.")
        return 0
    bad = 0
    for s in res:
        print(f"{s.state:14s} {s.file}  ({s.reason})")
        bad += s.state in ("unknown", "hash_mismatch")
    print(f"{len(res)} files, {bad} need attention")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
