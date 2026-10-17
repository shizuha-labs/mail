#!/usr/local/bin/python3
"""Bridge the legacy system-path launch command into the app virtualenv."""

import os
import sys


os.execv(
    "/app/.venv/bin/python",
    ["/app/.venv/bin/python", "/app/.venv/bin/supervisord", *sys.argv[1:]],
)
