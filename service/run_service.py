"""Entry point for the packaged service (PyInstaller). Same as `python -m voxy_service`."""
import multiprocessing
import sys

from voxy_service.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
