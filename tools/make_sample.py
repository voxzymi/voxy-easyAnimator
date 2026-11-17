"""Regenerates service/voxy_service/assets/sample_character.png (an original character drawn by the project's own code)."""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import training  # noqa: E402,F401
import training.shared.procedural_art as pa  # noqa: E402
from voxy_service import schema as S  # noqa: E402
from voxy_service.template import Layout  # noqa: E402

rng = np.random.default_rng(2026)
style = pa.Style(skin=(250, 222, 200), hair=(86, 120, 214), eye_white=(244, 244, 248), iris=(230, 150, 60), pupil=(24, 20, 30),
                 shirt=(245, 196, 70), sleeves=False, legwear=(60, 70, 120), legs_skin=False, shoe=(120, 70, 60), lash=(30, 24, 36),
                 brow=(48, 66, 128), mouth=(224, 110, 130), line=(28, 22, 34), outline_px=2, open_mouth=False)
pa.pose_variant = lambda rng, b: b                      # a fixed, calm pose
r = pa.render_character(rng, style, Layout(hair_len=0.42, arm_angle=(14, 14), elbow_bend=(10, 10)), (768, 512),
                        height_frac=(0.9, 0.9), z=dict(S.Z_ORDER))
bg = np.full((768, 512, 3), (236, 238, 246), np.uint8)
a = r.alpha[..., None]
img = (bg * (1 - a) + r.rgb * a).astype(np.uint8)
out = os.path.join(ROOT, "service", "voxy_service", "assets", "sample_character.png")
cv2.imwrite(out, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
print("wrote", out)
