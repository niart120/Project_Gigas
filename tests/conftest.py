"""Headless Qt defaults; visible acceptance runs use separate processes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
