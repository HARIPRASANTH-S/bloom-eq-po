"""Central configuration for the portal backend."""
from __future__ import annotations

import os

# Project root = parent of this app/ package.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SQLite now; point DATABASE_URL at Postgres later without touching the code.
DATABASE_URL = os.environ.get(
    "PORTAL_DATABASE_URL", f"sqlite:///{os.path.join(ROOT_DIR, 'data.db')}"
)

# Built React frontend (served by FastAPI in production); dev uses Vite on :5173.
FRONTEND_DIST = os.path.join(ROOT_DIR, "frontend", "dist")

# Defaults for report generation (mirror the CLI).
DEFAULT_TOP = 10
DEFAULT_VOL_WINDOW = 20
DEFAULT_BREAKOUT_WINDOW = 250   # ~52 weeks of trading days

# Yahoo Finance suffix for NSE symbols (used by the quotes/follow-up module).
YF_SUFFIX = ".NS"
