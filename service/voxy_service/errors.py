"""Errors with plain-language text for the panel.

Every error that can reach the user carries:
  * ``user_message``  - what happened, in plain words
  * ``what_to_do``    - what the user can do about it
  * ``help_topic``    - which part of the help page to open
The technical ``message`` and any traceback go to the log file, never to the panel.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# code -> (what happened, what to do, help topic)
CATALOG: Dict[str, tuple] = {
    "GENERAL_MODEL_FAILED": (
        "Voxy could not cut your character into parts.",
        "Try a picture with one character and a plain background. If it keeps happening, open the log file from the help page.",
        "make-layers-failed"),
    "GENERAL_MODEL_MISSING": (
        "The part-cutting files are not installed.",
        "Run the installer again to repair Voxy EasyAnimator.",
        "missing-files"),
    "NO_CHARACTER_FOUND": (
        "Voxy did not find a character in this picture.",
        "Pick a layer that shows one character, with a clean background.",
        "no-character"),
    "HAIR_MODEL_FAILED": (
        "Voxy could not split the hair into sections. Your single Hair part is still there.",
        "Try again with fewer sections, or keep the single Hair part.",
        "hair-failed"),
    "HAIR_MODEL_MISSING": (
        "The hair-splitting files are not installed. Your single Hair part is still there.",
        "Run the installer again to repair Voxy EasyAnimator.",
        "missing-files"),
    "NO_HAIR": (
        "There is no hair to split in this picture.",
        "Run Make Layers on a character that has hair.",
        "hair-failed"),
    "IMAGE_UNREADABLE": (
        "Voxy could not read the picture from After Effects.",
        "Select the layer again and retry. Make sure the layer is visible at the current time.",
        "blank-preview"),
    "IMAGE_TOO_LARGE": (
        "The picture is too large to process.",
        "Use a smaller layer, or scale the layer down before you start.",
        "slow-or-memory"),
    "JOB_CANCELLED": (
        "The job was cancelled.",
        "Press the button again when you are ready.",
        "troubleshooting"),
    "STEP_TIMEOUT": (
        "This step took too long and was stopped.",
        "Try a smaller picture. If you are using the slow mode, see the help page about speed.",
        "slow-or-memory"),
    "OUT_OF_MEMORY": (
        "Your graphics card ran out of memory.",
        "Close other programs that use the graphics card, or use a smaller picture.",
        "slow-or-memory"),
    "UPSCALER_MISSING": (
        "The upscaling tool is not installed.",
        "Run the installer again to repair Voxy EasyAnimator.",
        "missing-files"),
    "UPSCALER_FAILED": (
        "The upscale did not finish. Your project was not changed.",
        "Update your NVIDIA driver and try again.",
        "gpu-not-detected"),
    "UPSCALER_TIMEOUT": (
        "The upscale took too long and was stopped. Your project was not changed.",
        "Try a smaller layer.",
        "slow-or-memory"),
    "UPSCALER_OOM": (
        "The upscale ran out of graphics memory. Your project was not changed.",
        "Try a smaller layer. Voxy already tried smaller tiles.",
        "slow-or-memory"),
    "INPAINT_MODEL_MISSING": (
        "The background fill files are not installed.",
        "Switch the background fill to 'Flat colors', or run the installer again to repair.",
        "missing-files"),
    "INPAINT_FAILED": (
        "Voxy could not redraw the background.",
        "Try the 'Flat colors' fill instead.",
        "troubleshooting"),
    "MATTING_FAILED": (
        "Voxy could not remove the background.",
        "Turn Background Removal off, or use a picture with a plain background.",
        "troubleshooting"),
    "UNKNOWN_JOB": (
        "Voxy lost track of that job.",
        "Run the step again.",
        "troubleshooting"),
    "PARENT_JOB_MISSING": (
        "Voxy no longer has the Make Layers result this step needs.",
        "Run Make Layers again, then retry.",
        "troubleshooting"),
    "BAD_REQUEST": (
        "Voxy received a request it could not understand.",
        "Restart After Effects. If it keeps happening, reinstall Voxy EasyAnimator.",
        "troubleshooting"),
    "UNAUTHORIZED": (
        "Voxy could not connect to its helper program.",
        "Close and reopen the panel. If it keeps happening, restart After Effects.",
        "service-wont-start"),
    "INTERNAL_ERROR": (
        "Something went wrong inside Voxy.",
        "Try again. If it keeps happening, open the log file from the help page and send it to support.",
        "troubleshooting"),
}


class VoxyError(Exception):
    """An error with a stable code, a technical message and friendly text."""

    def __init__(self, code: str, message: str = "", *, stage: str = "", recoverable: bool = True,
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(message or code)
        self.code = code if code in CATALOG else "INTERNAL_ERROR"
        self.message = message or code
        self.stage = stage
        self.recoverable = recoverable
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        happened, todo, topic = CATALOG[self.code]
        return {
            "code": self.code,
            "stage": self.stage,
            "user_message": happened,
            "what_to_do": todo,
            "help_topic": topic,
            "recoverable": self.recoverable,
            # Short technical note for the log; the panel never shows it.
            "detail": self.message[:300],
        }


class Cancelled(VoxyError):
    def __init__(self, stage: str = ""):
        super().__init__("JOB_CANCELLED", "cancelled by user", stage=stage)


def error_dict(code: str, message: str = "", stage: str = "", recoverable: bool = True) -> Dict[str, Any]:
    return VoxyError(code, message, stage=stage, recoverable=recoverable).to_dict()


def warning_dict(code: str, user_message: str, what_to_do: str = "", help_topic: str = "troubleshooting") -> Dict[str, Any]:
    return {"code": code, "user_message": user_message, "what_to_do": what_to_do, "help_topic": help_topic}
