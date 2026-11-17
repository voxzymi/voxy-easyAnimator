"""Single source of truth for part classes, groups, keypoints and stacking order.

Everything that needs to agree on these lists (the service, the training code,
the dataset generator, the evaluation code, the tests) imports them from here.

Left/right convention: ``_l`` / ``_r`` are the *character's* own left and right.
On a character facing the viewer, the character's left is on the right side of
the image.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

API_VERSION = "1.0"
MANIFEST_SCHEMA_VERSION = "1.0"

# Group names, in the order the panel shows them and the manifest lists them.
GROUP_ORDER: List[str] = ["Head", "Face", "Hair", "Torso", "Arms", "Legs", "Hidden"]

# (class id, display name, group). The order here is the order of parts inside
# a group in the manifest, and it is also the channel order of the general
# model (channel 0 is background).
PART_CLASSES: List[Tuple[str, str, str]] = [
    ("head", "Head", "Head"),
    ("eye_l", "Eye L", "Face"),
    ("eye_r", "Eye R", "Face"),
    ("iris_l", "Iris L", "Face"),
    ("iris_r", "Iris R", "Face"),
    ("eyelash_l", "Eyelashes L", "Face"),
    ("eyelash_r", "Eyelashes R", "Face"),
    ("eyebrow_l", "Eyebrow L", "Face"),
    ("eyebrow_r", "Eyebrow R", "Face"),
    ("nose", "Nose", "Face"),
    ("mouth", "Mouth", "Face"),
    ("hair", "Hair", "Hair"),
    ("torso", "Torso", "Torso"),
    ("arm_l", "Arm L", "Arms"),
    ("arm_r", "Arm R", "Arms"),
    ("hand_l", "Hand L", "Arms"),
    ("hand_r", "Hand R", "Arms"),
    ("leg_l", "Leg L", "Legs"),
    ("leg_r", "Leg R", "Legs"),
    ("foot_l", "Foot L", "Legs"),
    ("foot_r", "Foot R", "Legs"),
]

CLASS_NAMES: List[str] = ["background"] + [c for c, _, _ in PART_CLASSES]
CLASS_INDEX: Dict[str, int] = {n: i for i, n in enumerate(CLASS_NAMES)}
NUM_CLASSES = len(CLASS_NAMES)
DISPLAY_NAME: Dict[str, str] = {c: d for c, d, _ in PART_CLASSES}
GROUP_OF_CLASS: Dict[str, str] = {c: g for c, _, g in PART_CLASSES}

# Hidden (completed) parts. ``base`` is the visible class whose hidden extent
# the general model's "completion" head predicts.
HIDDEN_PARTS: List[Tuple[str, str, str]] = [
    ("hidden_face", "Hidden Face", "head"),
    ("hidden_eye_l", "Hidden Eye L", "eye_l"),
    ("hidden_eye_r", "Hidden Eye R", "eye_r"),
    ("hidden_mouth", "Hidden Mouth", "mouth"),
]
AMODAL_CLASSES: List[str] = [b for _, _, b in HIDDEN_PARTS]  # channel order of the amodal head
HIDDEN_DISPLAY: Dict[str, str] = {i: d for i, d, _ in HIDDEN_PARTS}
HIDDEN_BASE: Dict[str, str] = {i: b for i, _, b in HIDDEN_PARTS}
for _hid, _, _ in HIDDEN_PARTS:
    GROUP_OF_CLASS[_hid] = "Hidden"

KEYPOINTS: List[str] = [
    "eye_l", "eye_r", "mouth",
    "shoulder_l", "shoulder_r", "elbow_l", "elbow_r", "wrist_l", "wrist_r",
    "hip_l", "hip_r", "knee_l", "knee_r", "ankle_l", "ankle_r",
]
KEYPOINT_INDEX: Dict[str, int] = {n: i for i, n in enumerate(KEYPOINTS)}

# Default stacking order: 0 is the back-most, larger is nearer the viewer.
# Hidden parts sit behind the things that cover them (hair) and in front of the
# part they complete.
Z_ORDER: Dict[str, int] = {
    "foot_l": 10, "foot_r": 10, "leg_l": 12, "leg_r": 12,
    "torso": 20, "arm_l": 30, "arm_r": 30, "hand_l": 32, "hand_r": 32,
    "head": 40, "hidden_face": 41,
    "hidden_eye_l": 49, "hidden_eye_r": 49, "hidden_mouth": 49,
    "eye_l": 50, "eye_r": 50, "iris_l": 52, "iris_r": 52,
    "eyelash_l": 54, "eyelash_r": 54, "eyebrow_l": 72, "eyebrow_r": 72,  # brows draw over the hair
    "nose": 58, "mouth": 58,
    "hair": 70,
}


def _swap_pairs(names: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for n in names:
        if n.endswith("_l"):
            out[n] = n[:-2] + "_r"
        elif n.endswith("_r"):
            out[n] = n[:-2] + "_l"
        else:
            out[n] = n
    return out


# Mirroring the image left-right swaps these names.
FLIP_CLASS: Dict[str, str] = _swap_pairs(CLASS_NAMES)
FLIP_KEYPOINT: Dict[str, str] = _swap_pairs(KEYPOINTS)
FLIP_CLASS_INDEX: List[int] = [CLASS_INDEX[FLIP_CLASS[n]] for n in CLASS_NAMES]
FLIP_KEYPOINT_INDEX: List[int] = [KEYPOINT_INDEX[FLIP_KEYPOINT[n]] for n in KEYPOINTS]


def group_of(part_class: str) -> str:
    """Group name for a class id (the fixed mapping; the model has no group head)."""
    return GROUP_OF_CLASS[part_class]


def manifest_order_key(part_class: str) -> Tuple[int, int]:
    """Sort key giving the manifest's canonical order: group order, then class order."""
    group = GROUP_OF_CLASS[part_class]
    names = [c for c, _, _ in PART_CLASSES] + [h for h, _, _ in HIDDEN_PARTS]
    return (GROUP_ORDER.index(group), names.index(part_class))
