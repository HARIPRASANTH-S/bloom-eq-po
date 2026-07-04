"""Orchestration shared by the daily ingest CLI and the API trigger."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy.orm import Session

import delivery_report as engine  # the proven NSE fetch+score engine (project root)

from . import store
from .config import DEFAULT_BREAKOUT_WINDOW, DEFAULT_TOP, DEFAULT_VOL_WINDOW


def run_and_store(
    db: Session,
    start: Optional[dt.date] = None,
    top: int = DEFAULT_TOP,
    vol_window: int = DEFAULT_VOL_WINDOW,
    breakout_window: int = DEFAULT_BREAKOUT_WINDOW,
    no_cache: bool = False,
) -> dict:
    """Fetch + score the latest trading day on/before `start`, persist, return a summary."""
    session = engine.make_session()
    favorites = store.list_favorites(db)
    breakout_watch = store.list_breakout_watch(db)
    report = engine.build_report(
        session, start or dt.date.today(),
        top=top, vol_window=vol_window, breakout_window=breakout_window, no_cache=no_cache,
        favorites=favorites, breakout_watch=breakout_watch,
    )
    return store.save_report(db, report)
