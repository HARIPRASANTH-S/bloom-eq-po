"""Follow-up tracking: how a past day's picks have moved since.

Two views per pick (the user asked for both):
  * EOD pick-performance: % move from the pick-day close to the latest daily
    close, plus whether the signal was directionally correct.
  * Intraday live movement: today's live price + day change (market hours).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from . import quotes, store


def _signal_correct(side: str, ret: float | None) -> bool | None:
    if ret is None:
        return None
    return ret > 0 if side == "BUY" else ret < 0


def compute_followup(db: Session, day: dt.date) -> dict:
    picks = store.get_picks(db, day)
    if not picks:
        return {"date": day.isoformat(), "rows": [], "summary": {}}

    symbols = sorted({p.symbol for p in picks})
    eod = quotes.latest_eod_closes(symbols)
    live = quotes.live_quotes(symbols)

    rows = []
    for p in picks:
        e = eod.get(p.symbol, {})
        lv = live.get(p.symbol, {})
        latest_close = e.get("close")
        ret = None
        if p.close_price and latest_close is not None:
            ret = (latest_close - p.close_price) / p.close_price * 100.0
        rows.append({
            "side": p.side,
            "rank": p.rank,
            "symbol": p.symbol,
            "cap": p.cap,
            "score": p.score,
            "pick_close": p.close_price,
            "latest_close": latest_close,
            "latest_asof": e.get("asof"),
            "eod_return_pct": None if ret is None else round(ret, 2),
            "signal_correct": _signal_correct(p.side, ret),
            "live_price": lv.get("last_price"),
            "live_day_change_pct": (None if lv.get("day_change_pct") is None
                                    else round(lv["day_change_pct"], 2)),
        })

    # Per-side scorecard: hit rate + average return.
    summary = {}
    for side in ("BUY", "SELL"):
        side_rows = [r for r in rows if r["side"] == side
                     and r["eod_return_pct"] is not None]
        if not side_rows:
            summary[side] = {"n": 0, "hit_rate": None, "avg_return_pct": None}
            continue
        hits = sum(1 for r in side_rows if r["signal_correct"])
        avg = sum(r["eod_return_pct"] for r in side_rows) / len(side_rows)
        summary[side] = {
            "n": len(side_rows),
            "hit_rate": round(hits / len(side_rows) * 100, 1),
            "avg_return_pct": round(avg, 2),
        }
    return {"date": day.isoformat(), "rows": rows, "summary": summary}
