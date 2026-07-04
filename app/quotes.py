"""Live + EOD quotes via Yahoo Finance (yfinance) for NSE symbols.

This is the *follow-up* data source, deliberately separate from the NSE-archive
fetchers in delivery_report.py: NSE's per-symbol live quote API is access-blocked,
whereas Yahoo serves both recent daily closes and intraday/live prices reliably
for `<SYMBOL>.NS` tickers. Swap this module out if you get a better feed.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

from .config import YF_SUFFIX


def _yf():
    import yfinance as yf
    return yf


def latest_eod_closes(symbols: list[str]) -> dict[str, dict]:
    """
    Most recent available daily close per symbol.
    -> {SYMBOL: {"close": float, "asof": "YYYY-MM-DD"}}
    """
    out: dict[str, dict] = {}
    if not symbols:
        return out
    tickers = [s + YF_SUFFIX for s in symbols]
    data = _yf().download(
        tickers, period="7d", interval="1d",
        progress=False, auto_adjust=False, group_by="ticker", threads=True,
    )
    for sym, tk in zip(symbols, tickers):
        try:
            close = data[tk]["Close"].dropna() if len(tickers) > 1 else data["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if close.empty:
            continue
        out[sym] = {"close": float(close.iloc[-1]),
                    "asof": close.index[-1].date().isoformat()}
    return out


def live_quotes(symbols: list[str]) -> dict[str, dict]:
    """
    Intraday/live price + day change per symbol (during market hours). When the
    market is closed Yahoo returns the last traded price, so this still yields a
    sensible "latest" figure.
    -> {SYMBOL: {"last_price": float|None, "prev_close": float|None,
                 "day_change_pct": float|None}}
    """
    out: dict[str, dict] = {}
    yf = _yf()
    for sym in symbols:
        rec = {"last_price": None, "prev_close": None, "day_change_pct": None}
        try:
            fi = yf.Ticker(sym + YF_SUFFIX).fast_info
            last = fi.get("last_price")
            prev = fi.get("previous_close")
            rec["last_price"] = float(last) if last is not None else None
            rec["prev_close"] = float(prev) if prev is not None else None
            if last is not None and prev:
                rec["day_change_pct"] = (float(last) - float(prev)) / float(prev) * 100.0
        except Exception:
            pass
        out[sym] = rec
    return out
