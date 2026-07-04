"""Persist a Report into the DB (idempotent) and load it back for the API."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BreakoutWatch, BulkDeal, DeliveryPick, Favorite, MarketFlow


def _num(v: Any) -> Optional[float]:
    """NaN / None -> None; otherwise a plain float."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _int(v: Any) -> Optional[int]:
    f = _num(v)
    return None if f is None else int(f)


def save_report(db: Session, report) -> dict:
    """
    Upsert one trading day's delivery picks + bulk deals. Idempotent: re-running
    the same date deletes that date's rows first, so there are never duplicates.
    `report` is delivery_report.Report.
    """
    day: dt.date = report.day

    db.query(DeliveryPick).filter(DeliveryPick.report_date == day).delete()
    db.query(BulkDeal).filter(BulkDeal.report_date == day).delete()

    # FII/DII comes from a flaky JSON API. Only replace stored flows when this run
    # actually fetched some — otherwise a transient failure would wipe good data.
    if report.fii_dii:
        db.query(MarketFlow).filter(MarketFlow.report_date == day).delete()
        for row in report.fii_dii:
            db.add(MarketFlow(
                report_date=day, category=row.get("category"),
                buy=_num(row.get("buy")), sell=_num(row.get("sell")),
                net=_num(row.get("net")), flow_date=row.get("date"),
            ))

    n_picks = 0
    for side, df in (("BUY", report.buy), ("SELL", report.sell), ("FAV", report.favorites),
                     ("BRK", report.breakout), ("BRKW", report.breakout_watch)):
        for rank, (_, row) in enumerate(df.iterrows(), start=1):
            db.add(DeliveryPick(
                report_date=day, side=side, rank=rank,
                symbol=str(row["SYMBOL"]),
                cap=str(row.get("CAP")) if row.get("CAP") is not None else None,
                score=_num(row.get("SCORE")),
                close_price=_num(row.get("CLOSE_PRICE")),
                pct_change=_num(row.get("PCT_CHANGE")),
                deliv_per=_num(row.get("DELIV_PER")),
                deliv_qty=_int(row.get("DELIV_QTY")),
                volume=_int(row.get("VOLUME")),
                vol_ratio=_num(row.get("VOL_RATIO")),
                inst_buy=_num(row.get("INST_BUY")),
                inst_sell=_num(row.get("INST_SELL")),
            ))
            n_picks += 1

    n_bulk = 0
    for side, df in (("BUY", report.bulk_buy), ("SELL", report.bulk_sell)):
        for rank, (_, row) in enumerate(df.iterrows(), start=1):
            db.add(BulkDeal(
                report_date=day, side=side, rank=rank,
                symbol=str(row["SYMBOL"]),
                n_deals=_int(row.get("N_DEALS")),
                total_qty=_int(row.get("TOTAL_QTY")),
                value_cr=_num(row.get("VALUE_CR")),
                institutions=(str(row.get("INSTITUTIONS"))
                              if row.get("INSTITUTIONS") is not None else None),
            ))
            n_bulk += 1

    db.commit()
    return {"date": day.isoformat(), "picks": n_picks, "bulk_deals": n_bulk,
            "fii_dii": len(report.fii_dii or []), "favorites": len(report.favorites),
            "breakout": len(report.breakout)}


# --------------------------------------------------------------------------- #
# Loaders (return JSON-friendly dicts)
# --------------------------------------------------------------------------- #
def list_dates(db: Session) -> list[str]:
    rows = db.execute(
        select(DeliveryPick.report_date).distinct().order_by(DeliveryPick.report_date.desc())
    ).scalars().all()
    return [d.isoformat() for d in rows]


def latest_date(db: Session) -> Optional[dt.date]:
    return db.execute(
        select(DeliveryPick.report_date).order_by(DeliveryPick.report_date.desc()).limit(1)
    ).scalar_one_or_none()


def _pick_dict(p: DeliveryPick) -> dict:
    return {
        "rank": p.rank, "symbol": p.symbol, "cap": p.cap, "score": p.score,
        "close_price": p.close_price, "pct_change": p.pct_change,
        "deliv_per": p.deliv_per, "deliv_qty": p.deliv_qty,
        "volume": p.volume, "vol_ratio": p.vol_ratio,
        "inst_buy": p.inst_buy, "inst_sell": p.inst_sell,
    }


def _bulk_dict(b: BulkDeal) -> dict:
    return {
        "rank": b.rank, "symbol": b.symbol, "n_deals": b.n_deals,
        "total_qty": b.total_qty, "value_cr": b.value_cr,
        "institutions": b.institutions,
    }


def get_report(db: Session, day: dt.date) -> dict:
    """All four sections for a date, as dicts keyed by side."""
    picks = db.execute(
        select(DeliveryPick).where(DeliveryPick.report_date == day)
        .order_by(DeliveryPick.side, DeliveryPick.rank)
    ).scalars().all()
    bulk = db.execute(
        select(BulkDeal).where(BulkDeal.report_date == day)
        .order_by(BulkDeal.side, BulkDeal.rank)
    ).scalars().all()
    flows = db.execute(
        select(MarketFlow).where(MarketFlow.report_date == day)
        .order_by(MarketFlow.category)
    ).scalars().all()
    return {
        "date": day.isoformat(),
        "fii_dii": [
            {"category": f.category, "buy": f.buy, "sell": f.sell,
             "net": f.net, "date": f.flow_date}
            for f in flows
        ],
        "delivery": {
            "BUY": [_pick_dict(p) for p in picks if p.side == "BUY"],
            "SELL": [_pick_dict(p) for p in picks if p.side == "SELL"],
        },
        "favorites": [_pick_dict(p) for p in picks if p.side == "FAV"],
        "breakout": [_pick_dict(p) for p in picks if p.side == "BRK"],
        "breakout_watch": [_pick_dict(p) for p in picks if p.side == "BRKW"],
        "bulk": {
            "BUY": [_bulk_dict(b) for b in bulk if b.side == "BUY"],
            "SELL": [_bulk_dict(b) for b in bulk if b.side == "SELL"],
        },
    }


def get_picks(db: Session, day: dt.date) -> list[DeliveryPick]:
    return db.execute(
        select(DeliveryPick).where(DeliveryPick.report_date == day)
        .order_by(DeliveryPick.side, DeliveryPick.rank)
    ).scalars().all()


# --------------------------------------------------------------------------- #
# Favorites watchlist
# --------------------------------------------------------------------------- #
def list_favorites(db: Session) -> list[str]:
    return db.execute(
        select(Favorite.symbol).order_by(Favorite.symbol)
    ).scalars().all()


def add_favorite(db: Session, symbol: str) -> list[str]:
    sym = symbol.strip().upper()
    if sym and not db.get(Favorite, sym):
        db.add(Favorite(symbol=sym))
        db.commit()
    return list_favorites(db)


def remove_favorite(db: Session, symbol: str) -> list[str]:
    fav = db.get(Favorite, symbol.strip().upper())
    if fav:
        db.delete(fav)
        db.commit()
    return list_favorites(db)


# --------------------------------------------------------------------------- #
# Breakout watchlist
# --------------------------------------------------------------------------- #
def list_breakout_watch(db: Session) -> list[str]:
    return db.execute(
        select(BreakoutWatch.symbol).order_by(BreakoutWatch.symbol)
    ).scalars().all()


def add_breakout_watch(db: Session, symbol: str) -> list[str]:
    sym = symbol.strip().upper()
    if sym and not db.get(BreakoutWatch, sym):
        db.add(BreakoutWatch(symbol=sym))
        db.commit()
    return list_breakout_watch(db)


def remove_breakout_watch(db: Session, symbol: str) -> list[str]:
    watch = db.get(BreakoutWatch, symbol.strip().upper())
    if watch:
        db.delete(watch)
        db.commit()
    return list_breakout_watch(db)
