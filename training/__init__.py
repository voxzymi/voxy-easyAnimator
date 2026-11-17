"""Voxy EasyAnimator training code. Importing this package makes ``voxy_service`` importable
(the training code shares the class list, preprocessing and geometry with the service)."""
import os
import sys

_SERVICE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service"))
if _SERVICE not in sys.path:
    sys.path.insert(0, _SERVICE)
