"""Builds the text the installer shows on its license page: the product license, then the third-party notices."""
import os
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "build", "license_and_notices.txt")
os.makedirs(os.path.dirname(out), exist_ok=True)
parts = []
for name in ("LICENSE.md", "THIRD_PARTY_NOTICES.md"):
    with open(os.path.join(root, name), encoding="utf-8") as f:
        parts.append(f.read())
with open(out, "w", encoding="utf-8") as f:
    f.write("\n\n" + "=" * 78 + "\n\n".join(parts))
print("wrote", out)
