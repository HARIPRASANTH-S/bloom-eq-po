"""FastAPI app. Each portal module is a router mounted under /api.

Run:  uvicorn app.api:app --reload
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import followup as followup_mod
from . import service, store
from .config import DEFAULT_BREAKOUT_WINDOW, FRONTEND_DIST
from .db import get_db, init_db

app = FastAPI(title="Stock Analysis Portal", version="0.1.0")

# Dev: React dev server runs on :5173 (Vite). Allow it during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _parse_date(value: str) -> dt.date:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad date '{value}', expected YYYY-MM-DD")


# --------------------------- Module 1: delivery ---------------------------- #
delivery = APIRouter(prefix="/api", tags=["delivery"])


@delivery.get("/health")
def health() -> dict:
    return {"status": "ok"}


@delivery.get("/dates")
def dates(db: Session = Depends(get_db)) -> list[str]:
    return store.list_dates(db)


@delivery.get("/delivery/latest")
def delivery_latest(db: Session = Depends(get_db)) -> dict:
    day = store.latest_date(db)
    if day is None:
        raise HTTPException(status_code=404, detail="no reports stored yet — run ingest")
    return store.get_report(db, day)


@delivery.get("/delivery/{date}")
def delivery_for_date(date: str, db: Session = Depends(get_db)) -> dict:
    day = _parse_date(date)
    report = store.get_report(db, day)
    if not report["delivery"]["BUY"] and not report["delivery"]["SELL"]:
        raise HTTPException(status_code=404, detail=f"no report stored for {date}")
    return report


@delivery.get("/followup/{date}")
def followup_for_date(date: str, db: Session = Depends(get_db)) -> dict:
    return followup_mod.compute_followup(db, _parse_date(date))


@delivery.get("/favorites")
def favorites_list(db: Session = Depends(get_db)) -> list[str]:
    return store.list_favorites(db)


@delivery.post("/favorites/{symbol}")
def favorites_add(symbol: str, db: Session = Depends(get_db)) -> list[str]:
    return store.add_favorite(db, symbol)


@delivery.delete("/favorites/{symbol}")
def favorites_remove(symbol: str, db: Session = Depends(get_db)) -> list[str]:
    return store.remove_favorite(db, symbol)


@delivery.get("/breakout-watch")
def breakout_watch_list(db: Session = Depends(get_db)) -> list[str]:
    return store.list_breakout_watch(db)


@delivery.post("/breakout-watch/{symbol}")
def breakout_watch_add(symbol: str, db: Session = Depends(get_db)) -> list[str]:
    return store.add_breakout_watch(db, symbol)


@delivery.delete("/breakout-watch/{symbol}")
def breakout_watch_remove(symbol: str, db: Session = Depends(get_db)) -> list[str]:
    return store.remove_breakout_watch(db, symbol)


@delivery.post("/ingest")
def ingest(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; default latest"),
    top: int = 10,
    vol_window: int = 20,
    breakout_window: int = DEFAULT_BREAKOUT_WINDOW,
    no_cache: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    start = _parse_date(date) if date else None
    return service.run_and_store(db, start, top=top, vol_window=vol_window,
                                 breakout_window=breakout_window, no_cache=no_cache)


app.include_router(delivery)

# Serve the built React SPA at / if it exists (after `npm run build`).
if os.path.isdir(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
