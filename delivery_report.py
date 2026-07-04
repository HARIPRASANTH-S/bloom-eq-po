#!/usr/bin/env python3
"""
NSE (India) Delivery + Bulk-Deals report.

Reproduces a 4-section terminal report from the latest completed NSE trading day:

  1) DELIVERY  -- TOP N BUY  (by delivery score, positive move on the day)
  2) DELIVERY  -- TOP N SELL (by delivery score, negative move on the day)
  3) BULK DEALS -- TOP N BUY  (by quantity)
  4) BULK DEALS -- TOP N SELL (by quantity)

Data sources (all are public NSE archive CSVs, fetched with a browser-like
requests session, retried with backoff, and cached locally):

  * Security-wise delivery / full bhavcopy:
        https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
    -> CLOSE_PRICE, PREV_CLOSE (=> PCT_CHANGE), TTL_TRD_QNTY (volume), DELIV_PER
  * Bulk deals (rolling recent file):
        https://nsearchives.nseindia.com/content/equities/bulk.csv
    -> client name, side, quantity, price

The three fetchers (delivery, historical volume, bulk deals) are independent
functions so any one endpoint can be swapped without touching the rest.

Usage:
    python delivery_report.py                 # latest trading day
    python delivery_report.py --date 2026-06-19
    python delivery_report.py --top 15 --vol-window 30
    python delivery_report.py --no-cache
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import time
import typing
from typing import Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --------------------------------------------------------------------------- #
# DELIVERY SCORE -- tune these.
#
# The score is a transparent 0-100 composite of three signals, each normalised
# to 0..1 then combined with the weights below (weights should sum to 1.0):
#
#     deliv_n = clip(DELIV_PER / 100, 0, 1)              # conviction / holding
#     vol_n   = clip(VOL_RATIO / VOL_RATIO_CAP, 0, 1)    # volume surge vs avg
#     pct_n   = clip(abs(PCT_CHANGE) / PCT_CAP, 0, 1)    # move magnitude
#
#     SCORE = 100 * (W_DELIV*deliv_n + W_VOL*vol_n + W_PCT*pct_n)
#
# Then the universe is split by direction of the day's move:
#     BUY  = positive PCT_CHANGE, ranked by SCORE descending
#     SELL = negative PCT_CHANGE, ranked by SCORE descending
#
# Raise W_DELIV to favour high-delivery (conviction) names; raise W_VOL to
# reward volume surges; raise the *_CAP values to make those signals harder to
# max out (a higher cap = a given ratio contributes less).
# --------------------------------------------------------------------------- #
W_DELIV = 0.50          # weight: delivery percentage
W_VOL = 0.30            # weight: volume ratio (today vs lookback average)
W_PCT = 0.20            # weight: magnitude of percent change
VOL_RATIO_CAP = 5.0     # VOL_RATIO at/above this contributes the full vol weight
PCT_CAP = 5.0           # |PCT_CHANGE| at/above this contributes the full pct weight

# --------------------------------------------------------------------------- #
# BREAKOUT -- a stock is "in breakout stage" when today's close clears its
# highest HIGH_PRICE over the prior BREAKOUT_WINDOW trading days (a new
# 52-week high by default) AND that move is confirmed by a volume surge
# (VOL_RATIO >= BREAKOUT_VOL_RATIO_MIN), so a thin/illiquid new-high print
# doesn't qualify. Candidates are then ranked by the existing delivery SCORE.
# --------------------------------------------------------------------------- #
BREAKOUT_WINDOW = 250            # trading days (~52 weeks) used as the reference high
BREAKOUT_VOL_RATIO_MIN = 1.5     # today's volume must be >= this multiple of the average

# Universe filters (also tunable).
ALLOWED_SERIES = {"EQ"}     # EQ covers ordinary equities AND most NSE ETFs
MIN_TURNOVER_LACS = 100.0   # drop illiquid names (< ~Rs 1 cr turnover) that
                            # otherwise produce noisy VOL_RATIO spikes

# Presentation.
SEP_WIDTH = 58          # width of the "=" separator / title-centering band

# Networking.
ARCHIVE = "https://nsearchives.nseindia.com"
BHAV_URL = ARCHIVE + "/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
BULK_URL = ARCHIVE + "/content/equities/bulk.csv"
BLOCK_URL = ARCHIVE + "/content/equities/block.csv"
# Equity master: the authoritative list of listed EQUITIES. ETFs trade in the
# same EQ series but are NOT in this file (they live in eq_etfseclist.csv), so
# membership here is how we keep equities and drop ETFs / other instruments.
EQUITY_MASTER_URL = ARCHIVE + "/content/equities/EQUITY_L.csv"

# Market-cap classification (SEBI-aligned, via NIFTY index membership).
# SEBI ranks all listed companies by full market cap:
#   rank   1-100  -> Large    (== NIFTY 100 constituents)
#   rank 101-250  -> Mid      (== NIFTY Midcap 150 constituents)
#   rank  251+    -> Small    (open-ended: EVERY other company)
# NIFTY 100 + Midcap 150 = exactly the top 250 names, so we only need those two
# lists and classify everything else as Small (this is why the Smallcap-250 list
# is unnecessary -- it only covers ranks 251-500, leaving 500+ unlabelled).
# Listed Large-last so Large takes precedence over Mid on any overlap.
CAP_LISTS = [
    ("Mid", "https://niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv"),
    ("Large", "https://niftyindices.com/IndexConstituent/ind_nifty100list.csv"),
]
CAP_DEFAULT = "Small"   # any equity not in the top-250 baskets is small-cap

# Market-wide FII/FPI + DII cash-segment buy/sell (Rs crore). This is the one
# institutional figure NSE publishes free, and it is a daily AGGREGATE for the
# whole market (not per symbol). JSON API -> needs the cookie handshake below.
NSE_HOME = "https://www.nseindia.com"
FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

MAX_TRADING_DAY_LOOKBACK = 10   # days to walk back when resolving a trading day
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


# --------------------------------------------------------------------------- #
# Networking helpers
# --------------------------------------------------------------------------- #
def make_session() -> requests.Session:
    """A requests session with browser headers and retry/backoff."""
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=1.5,          # 0s, 1.5s, 3s, 6s ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, name)


def download_csv(
    session: requests.Session,
    url: str,
    cache_name: str,
    no_cache: bool = False,
    timeout: int = 30,
) -> Optional[str]:
    """
    Download a CSV as text, caching the raw bytes locally so repeated runs do
    not re-hammer NSE. Returns None on a 404 (so callers can treat a missing
    bhavcopy as "not a trading day").
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(cache_name)
    if not no_cache and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    resp = session.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    text = resp.text
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# Fetcher 1: delivery / full bhavcopy
# --------------------------------------------------------------------------- #
def _parse_bhav(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    for col in ("SYMBOL", "SERIES"):
        df[col] = df[col].astype(str).str.strip()
    # Numeric coercion ("-" -> NaN for non-deliverable rows).
    for col in ("PREV_CLOSE", "CLOSE_PRICE", "HIGH_PRICE", "TTL_TRD_QNTY",
                "TURNOVER_LACS", "DELIV_QTY", "DELIV_PER"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_bhav_for_date(
    session: requests.Session, date: dt.date, no_cache: bool = False
) -> Optional[pd.DataFrame]:
    """Return the parsed bhavcopy for a specific date, or None if unavailable."""
    ddmmyyyy = date.strftime("%d%m%Y")
    text = download_csv(
        session,
        BHAV_URL.format(ddmmyyyy=ddmmyyyy),
        cache_name=f"bhav_{ddmmyyyy}.csv",
        no_cache=no_cache,
    )
    return _parse_bhav(text) if text else None


def resolve_trading_day(
    session: requests.Session, start: dt.date, no_cache: bool = False
) -> tuple[dt.date, pd.DataFrame]:
    """
    Walk back from `start` until a bhavcopy is available, transparently
    handling weekends and market holidays. Returns (date, bhav_df).
    """
    for back in range(MAX_TRADING_DAY_LOOKBACK + 1):
        day = start - dt.timedelta(days=back)
        if day.weekday() >= 5:        # Sat/Sun: skip without a network call
            continue
        df = fetch_bhav_for_date(session, day, no_cache=no_cache)
        if df is not None and not df.empty:
            return day, df
    raise RuntimeError(
        f"No NSE bhavcopy found within {MAX_TRADING_DAY_LOOKBACK} days of {start}"
    )


def fetch_equity_universe(
    session: requests.Session, no_cache: bool = False
) -> set[str]:
    """
    Set of listed-equity SYMBOLs from the equity master (EQUITY_L.csv). Used to
    drop ETFs and other non-equity instruments, which share the EQ series but are
    absent from this file. Empty set => caller should skip the filter.
    """
    text = download_csv(
        session, EQUITY_MASTER_URL, cache_name="EQUITY_L.csv", no_cache=no_cache
    )
    if not text:
        return set()
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    return set(df["SYMBOL"].astype(str).str.strip())


def fetch_delivery(
    session: requests.Session,
    day: dt.date,
    df: pd.DataFrame,
    equity_symbols: set[str],
) -> pd.DataFrame:
    """
    Reduce a bhavcopy to the deliverable EQUITY universe with the fields we score
    on. `day`/`df` come from resolve_trading_day so we don't download twice.
    ETFs/other instruments are removed via `equity_symbols` (the equity master).
    """
    out = df[df["SERIES"].isin(ALLOWED_SERIES)].copy()
    if equity_symbols:
        out = out[out["SYMBOL"].isin(equity_symbols)]
    out = out[out["TTL_TRD_QNTY"].fillna(0) > 0]
    out = out[out["TURNOVER_LACS"].fillna(0) >= MIN_TURNOVER_LACS]
    out = out[out["DELIV_PER"].notna()]
    out["PCT_CHANGE"] = (out["CLOSE_PRICE"] - out["PREV_CLOSE"]) / out["PREV_CLOSE"] * 100.0
    return out[["SYMBOL", "CLOSE_PRICE", "PCT_CHANGE",
                "DELIV_PER", "DELIV_QTY", "TTL_TRD_QNTY"]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Fetcher 2: historical volume (for VOL_RATIO)
# --------------------------------------------------------------------------- #
def fetch_history_volumes(
    session: requests.Session,
    ref_date: dt.date,
    window: int,
    no_cache: bool = False,
    polite_delay: float = 0.3,
) -> pd.Series:
    """
    Average TTL_TRD_QNTY per symbol over the `window` trading days strictly
    BEFORE ref_date. Returns a Series indexed by SYMBOL. Skips holidays/weekends
    (missing bhavcopies) automatically.
    """
    frames: list[pd.DataFrame] = []
    collected = 0
    back = 1
    while collected < window and back <= window * 3:   # generous cap for holidays
        day = ref_date - dt.timedelta(days=back)
        back += 1
        if day.weekday() >= 5:
            continue
        df = fetch_bhav_for_date(session, day, no_cache=no_cache)
        if df is None or df.empty:
            continue
        sub = df[df["SERIES"].isin(ALLOWED_SERIES)][["SYMBOL", "TTL_TRD_QNTY"]]
        frames.append(sub)
        collected += 1
        if no_cache and polite_delay:
            time.sleep(polite_delay)
    if not frames:
        return pd.Series(dtype="float64")
    hist = pd.concat(frames, ignore_index=True)
    return hist.groupby("SYMBOL")["TTL_TRD_QNTY"].mean()


def fetch_breakout_highs(
    session: requests.Session,
    ref_date: dt.date,
    window: int,
    no_cache: bool = False,
    polite_delay: float = 0.3,
) -> pd.Series:
    """
    Highest HIGH_PRICE per symbol over the `window` trading days strictly BEFORE
    ref_date -- the reference level a breakout must clear. Same walk-back shape
    as fetch_history_volumes; each day's bhavcopy is disk-cached, so days already
    pulled for VOL_RATIO (or a prior run) cost no extra network calls, only reuse.
    """
    frames: list[pd.DataFrame] = []
    collected = 0
    back = 1
    while collected < window and back <= window * 3:
        day = ref_date - dt.timedelta(days=back)
        back += 1
        if day.weekday() >= 5:
            continue
        df = fetch_bhav_for_date(session, day, no_cache=no_cache)
        if df is None or df.empty:
            continue
        sub = df[df["SERIES"].isin(ALLOWED_SERIES)][["SYMBOL", "HIGH_PRICE"]]
        frames.append(sub)
        collected += 1
        if no_cache and polite_delay:
            time.sleep(polite_delay)
    if not frames:
        return pd.Series(dtype="float64")
    hist = pd.concat(frames, ignore_index=True)
    return hist.groupby("SYMBOL")["HIGH_PRICE"].max()


# --------------------------------------------------------------------------- #
# Fetcher 3: bulk deals
# --------------------------------------------------------------------------- #
_DEAL_COLS = ["SYMBOL", "CLIENT", "SIDE", "QTY", "PRICE"]
_DEAL_RENAME = {
    "Symbol": "SYMBOL",
    "Client Name": "CLIENT",
    "Buy/Sell": "SIDE",
    "Quantity Traded": "QTY",
    "Trade Price / Wght. Avg. Price": "PRICE",
}


def _fetch_deals(
    session: requests.Session,
    url: str,
    cache_name: str,
    day: dt.date,
    no_cache: bool = False,
) -> pd.DataFrame:
    """
    Parse a bulk/block-deals CSV (identical schema) and filter to `day`.
    Normalised columns: SYMBOL, CLIENT, SIDE, QTY, PRICE. Empty DataFrame if
    the file is missing, empty ("NO RECORDS"), or has no rows for the day.
    """
    text = download_csv(session, url, cache_name=cache_name, no_cache=no_cache)
    if not text:
        return pd.DataFrame(columns=_DEAL_COLS)
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    if "Date" not in df.columns:                       # e.g. "NO RECORDS" body
        return pd.DataFrame(columns=_DEAL_COLS)
    df = df.rename(columns=_DEAL_RENAME)
    df["DATE"] = pd.to_datetime(df["Date"], format="%d-%b-%Y", errors="coerce").dt.date
    df = df[df["DATE"] == day]
    if df.empty:
        return pd.DataFrame(columns=_DEAL_COLS)
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    df["CLIENT"] = df["CLIENT"].astype(str).str.strip()
    df["SIDE"] = df["SIDE"].astype(str).str.strip().str.upper()
    df["QTY"] = pd.to_numeric(df["QTY"], errors="coerce")
    df["PRICE"] = pd.to_numeric(df["PRICE"], errors="coerce")
    return df[_DEAL_COLS].reset_index(drop=True)


def fetch_bulk_deals(
    session: requests.Session, day: dt.date, no_cache: bool = False
) -> pd.DataFrame:
    """Bulk deals for `day` from the rolling bulk.csv."""
    # bulk.csv is a ROLLING file (NSE overwrites it with the latest day), so the
    # cache must be keyed by trading day -- otherwise a new day would reuse an old
    # snapshot and report zero deals. (The date-stamped bhavcopies are immutable,
    # hence safe to cache by name; the deal files are not.)
    return _fetch_deals(session, BULK_URL, f"bulk_{day:%d%m%Y}.csv", day, no_cache=no_cache)


def fetch_block_deals(
    session: requests.Session, day: dt.date, no_cache: bool = False
) -> pd.DataFrame:
    """Block deals for `day` from the rolling block.csv (same schema as bulk)."""
    return _fetch_deals(session, BLOCK_URL, f"block_{day:%d%m%Y}.csv", day, no_cache=no_cache)


def institutional_flow(*deal_frames: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """
    Per-symbol institutional buy/sell QUANTITIES derived from bulk + block deals
    (the only per-symbol institutional-flow data NSE publishes free; NSE does not
    release per-symbol FII/DII figures). Returns (inst_buy, inst_sell) Series
    indexed by SYMBOL. Symbols with no deal activity simply won't appear -> NaN
    once mapped onto the delivery universe.
    """
    frames = [d for d in deal_frames if d is not None and not d.empty]
    if not frames:
        empty = pd.Series(dtype="float64")
        return empty, empty.copy()
    deals = pd.concat(frames, ignore_index=True)
    by = deals.groupby(["SYMBOL", "SIDE"])["QTY"].sum()
    buy = by.xs("BUY", level="SIDE") if "BUY" in deals["SIDE"].values else pd.Series(dtype="float64")
    sell = by.xs("SELL", level="SIDE") if "SELL" in deals["SIDE"].values else pd.Series(dtype="float64")
    return buy, sell


# --------------------------------------------------------------------------- #
# Fetcher 4: market-cap classification
# --------------------------------------------------------------------------- #
def fetch_cap_classification(
    session: requests.Session, no_cache: bool = False
) -> dict[str, str]:
    """
    Map SYMBOL -> "Large" / "Mid" using NIFTY top-250 index membership
    (SEBI-aligned). Updated by NSE twice a year, so the cached copies are fine
    between rebalances. Symbols absent from both lists default to CAP_DEFAULT
    ("Small") at mapping time -- so every stock in the report gets a label.
    """
    cap: dict[str, str] = {}
    for label, url in CAP_LISTS:   # Mid first, Large last -> Large wins on overlap
        try:
            text = download_csv(
                session, url, cache_name=f"cap_{label.lower()}.csv", no_cache=no_cache
            )
        except Exception as exc:
            print(f"WARNING: cap list '{label}' unavailable: {exc}", file=sys.stderr)
            continue
        if not text:
            continue
        df = pd.read_csv(io.StringIO(text))
        df.columns = [c.strip() for c in df.columns]
        if "Symbol" not in df.columns:
            continue
        for sym in df["Symbol"].astype(str).str.strip():
            cap[sym] = label
    return cap


# --------------------------------------------------------------------------- #
# Fetcher 5: market-wide FII / DII flows (daily aggregate)
# --------------------------------------------------------------------------- #
def fetch_fii_dii(session: requests.Session) -> list[dict]:
    """
    Latest market-wide FII/FPI + DII cash-segment buy/sell/net (Rs crore).
    Returns e.g. [{"category": "FII", "buy": ..., "sell": ..., "net": ...,
    "date": "22-Jun-2026"}, {"category": "DII", ...}] -- or [] on any failure
    (this JSON endpoint is occasionally blocked / rate-limited; never fatal).

    NOTE: this is a whole-market aggregate, not per-symbol; NSE does not publish
    per-stock FII/DII data. The figure is for the latest day NSE has released,
    which usually matches the trading day but can lag by a session.
    """
    # Avoid 'br' (brotli) in Accept-Encoding -- if brotli isn't installed, requests
    # can't decode the body and json() fails. A JSON Accept + a Referer also help
    # this endpoint return data rather than an HTML/empty body.
    api_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Referer": NSE_HOME + "/market-data/live-equity-market",
    }
    try:
        session.get(NSE_HOME, timeout=20)                 # obtain session cookie
        resp = session.get(FII_DII_URL, timeout=20, headers=api_headers)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        print(f"WARNING: FII/DII flows unavailable: {exc}", file=sys.stderr)
        return []

    out: list[dict] = []
    for r in rows:
        cat = str(r.get("category", "")).upper()
        cat = "FII" if cat.startswith("FII") else ("DII" if cat.startswith("DII") else cat)
        try:
            out.append({
                "category": cat,
                "buy": float(r["buyValue"]),
                "sell": float(r["sellValue"]),
                "net": float(r["netValue"]),
                "date": r.get("date"),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------- #
# Aggregation / scoring
# --------------------------------------------------------------------------- #
def compute_scores(
    deliv: pd.DataFrame,
    avg_vol: pd.Series,
    inst_buy: pd.Series,
    inst_sell: pd.Series,
    cap_map: dict[str, str],
    breakout_high: Optional[pd.Series] = None,
) -> pd.DataFrame:
    df = deliv.copy()
    df["CAP"] = df["SYMBOL"].map(cap_map).fillna(CAP_DEFAULT)
    df["VOLUME"] = df["TTL_TRD_QNTY"]                 # total shares traded on the day
    df["AVG_VOL"] = df["SYMBOL"].map(avg_vol)
    df["VOL_RATIO"] = df["TTL_TRD_QNTY"] / df["AVG_VOL"]

    deliv_n = (df["DELIV_PER"] / 100.0).clip(0, 1)
    vol_n = (df["VOL_RATIO"] / VOL_RATIO_CAP).clip(0, 1).fillna(0)
    pct_n = (df["PCT_CHANGE"].abs() / PCT_CAP).clip(0, 1).fillna(0)
    df["SCORE"] = 100.0 * (W_DELIV * deliv_n + W_VOL * vol_n + W_PCT * pct_n)

    # INST_BUY / INST_SELL: per-symbol institutional buy/sell QUANTITIES from
    # bulk + block deals (NSE publishes no per-symbol FII/DII feed). NaN where a
    # symbol had no institutional deal activity that day.
    df["INST_BUY"] = df["SYMBOL"].map(inst_buy)
    df["INST_SELL"] = df["SYMBOL"].map(inst_sell)

    # BREAKOUT: today's close clears the prior-window high on a volume surge.
    high_ref = df["SYMBOL"].map(breakout_high) if breakout_high is not None else pd.Series(dtype="float64")
    df["IS_BREAKOUT"] = (
        high_ref.notna()
        & (df["CLOSE_PRICE"] > high_ref)
        & (df["VOL_RATIO"].fillna(0) >= BREAKOUT_VOL_RATIO_MIN)
    )
    return df


# Columns exposed for each scored delivery row (top picks AND favorites).
DELIVERY_COLS = ["SYMBOL", "CAP", "SCORE", "CLOSE_PRICE", "PCT_CHANGE",
                 "DELIV_PER", "DELIV_QTY", "VOLUME", "VOL_RATIO", "INST_BUY", "INST_SELL"]


def split_buy_sell(scored: pd.DataFrame, top: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    buy = (scored[scored["PCT_CHANGE"] > 0]
           .sort_values("SCORE", ascending=False).head(top))[DELIVERY_COLS]
    sell = (scored[scored["PCT_CHANGE"] < 0]
            .sort_values("SCORE", ascending=False).head(top))[DELIVERY_COLS]
    return buy.reset_index(drop=True), sell.reset_index(drop=True)


def select_favorites(scored: pd.DataFrame, favorites) -> pd.DataFrame:
    """Full scored rows for the watchlist symbols (regardless of rank/direction)."""
    fav_syms = {str(s).strip().upper() for s in (favorites or [])}
    if not fav_syms:
        return scored.iloc[0:0][DELIVERY_COLS].reset_index(drop=True)
    out = (scored[scored["SYMBOL"].isin(fav_syms)]
           .sort_values("SCORE", ascending=False))[DELIVERY_COLS]
    return out.reset_index(drop=True)


def select_breakouts(scored: pd.DataFrame, top: int) -> pd.DataFrame:
    """Top-N stocks currently in breakout stage (new high + volume surge; see
    IS_BREAKOUT in compute_scores), ranked by the existing delivery SCORE."""
    out = (scored[scored["IS_BREAKOUT"]]
           .sort_values("SCORE", ascending=False).head(top))[DELIVERY_COLS]
    return out.reset_index(drop=True)


def select_breakout_watch(scored: pd.DataFrame, watchlist) -> pd.DataFrame:
    """Full scored rows for a manually-curated breakout watchlist (regardless of
    whether IS_BREAKOUT is true today) -- mirrors select_favorites."""
    watch_syms = {str(s).strip().upper() for s in (watchlist or [])}
    if not watch_syms:
        return scored.iloc[0:0][DELIVERY_COLS].reset_index(drop=True)
    out = (scored[scored["SYMBOL"].isin(watch_syms)]
           .sort_values("SCORE", ascending=False))[DELIVERY_COLS]
    return out.reset_index(drop=True)


def aggregate_bulk(bulk: pd.DataFrame, top: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = ["SYMBOL", "SIDE", "N_DEALS", "TOTAL_QTY", "VALUE_CR", "INSTITUTIONS"]
    if bulk.empty:
        empty = pd.DataFrame(columns=cols)
        return empty, empty.copy()

    bulk = bulk.copy()
    bulk["VALUE"] = bulk["QTY"] * bulk["PRICE"]
    grouped = bulk.groupby(["SYMBOL", "SIDE"], dropna=False)
    agg = grouped.agg(
        N_DEALS=("QTY", "size"),
        TOTAL_QTY=("QTY", "sum"),
        VALUE=("VALUE", "sum"),
        INSTITUTIONS=("CLIENT", lambda s: "; ".join(dict.fromkeys(s.dropna()))),
    ).reset_index()
    # VALUE_CR = qty*price / 1e7 (crores); NaN if any price was missing.
    agg["VALUE_CR"] = (agg["VALUE"] / 1e7).round(2)
    agg = agg[cols]

    buy = (agg[agg["SIDE"] == "BUY"]
           .sort_values("TOTAL_QTY", ascending=False).head(top))
    sell = (agg[agg["SIDE"] == "SELL"]
            .sort_values("TOTAL_QTY", ascending=False).head(top))
    return buy.reset_index(drop=True), sell.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #
def _format_table(df: pd.DataFrame, float_cols: list[str]) -> str:
    """Right-aligned fixed-width table mirroring pandas to_string output."""
    disp = df.copy()
    for col in float_cols:
        if col in disp.columns:
            disp[col] = disp[col].map(
                lambda v: "NaN" if pd.isna(v) else f"{v:.2f}"
            )
    # Integer-like columns: render without decimals.
    for col in ("N_DEALS", "TOTAL_QTY", "INST_BUY", "INST_SELL", "DELIV_QTY", "VOLUME"):
        if col in disp.columns:
            disp[col] = disp[col].map(
                lambda v: "NaN" if pd.isna(v) else f"{int(v)}"
            )
    return disp.to_string(index=False, na_rep="NaN")


def _format_fii_dii(rows: list[dict]) -> str:
    """Small CATEGORY / BUY / SELL / NET table for the FII-DII banner."""
    df = pd.DataFrame(rows)[["category", "buy", "sell", "net"]]
    df.columns = ["CATEGORY", "BUY", "SELL", "NET"]
    for c in ("BUY", "SELL", "NET"):
        df[c] = df[c].map(lambda v: f"{v:,.2f}")
    return df.to_string(index=False)


def print_section(title: str, table_text: str) -> None:
    sep = "=" * SEP_WIDTH
    print(sep)
    print(title.center(SEP_WIDTH))
    print(sep)
    print(table_text)
    print()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NSE delivery + bulk-deals report.")
    p.add_argument("--date", default=None,
                   help="Trading day YYYY-MM-DD (default: latest available).")
    p.add_argument("--top", type=int, default=10, help="Rows per section (default 10).")
    p.add_argument("--vol-window", type=int, default=20,
                   help="Lookback trading days for VOL_RATIO average (default 20).")
    p.add_argument("--breakout-window", type=int, default=BREAKOUT_WINDOW,
                   help=f"Lookback trading days for the breakout reference high "
                        f"(default {BREAKOUT_WINDOW}, ~52 weeks).")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass the local cache and re-download everything.")
    return p.parse_args(argv)


class Report(typing.NamedTuple):
    """Structured result of build_report, reused by the CLI and the backend."""
    day: dt.date
    buy: pd.DataFrame
    sell: pd.DataFrame
    bulk_buy: pd.DataFrame
    bulk_sell: pd.DataFrame
    fii_dii: list[dict]
    favorites: pd.DataFrame
    breakout: pd.DataFrame
    breakout_watch: pd.DataFrame


def build_report(
    session: requests.Session,
    start: dt.date,
    top: int = 10,
    vol_window: int = 20,
    breakout_window: int = BREAKOUT_WINDOW,
    no_cache: bool = False,
    favorites=(),
    breakout_watch=(),
) -> Report:
    """
    Fetch + score everything for the latest trading day on/before `start`.
    Pure data path (no printing) so the CLI, the daily ingest, and the API can
    all share it. Resolves weekends/holidays back to a real trading day.

    `favorites` is an optional list of watchlist symbols; their full scored rows
    are returned in Report.favorites regardless of rank or up/down direction.
    `breakout_watch` is the same idea for a manually-curated breakout watchlist
    (Report.breakout_watch); Report.breakout is the auto-detected top-N instead.
    """
    day, bhav_df = resolve_trading_day(session, start, no_cache=no_cache)

    equity_symbols = fetch_equity_universe(session, no_cache=no_cache)
    deliv = fetch_delivery(session, day, bhav_df, equity_symbols)
    avg_vol = fetch_history_volumes(session, day, vol_window, no_cache=no_cache)
    breakout_high = fetch_breakout_highs(session, day, breakout_window, no_cache=no_cache)

    # Deal data feeds both the bulk-deal sections AND the per-symbol INST columns.
    try:
        bulk = fetch_bulk_deals(session, day, no_cache=no_cache)
    except Exception as exc:
        print(f"WARNING: bulk deals unavailable: {exc}", file=sys.stderr)
        bulk = pd.DataFrame(columns=_DEAL_COLS)
    try:
        block = fetch_block_deals(session, day, no_cache=no_cache)
    except Exception as exc:
        print(f"WARNING: block deals unavailable: {exc}", file=sys.stderr)
        block = pd.DataFrame(columns=_DEAL_COLS)

    inst_buy, inst_sell = institutional_flow(bulk, block)
    cap_map = fetch_cap_classification(session, no_cache=no_cache)
    scored = compute_scores(deliv, avg_vol, inst_buy, inst_sell, cap_map, breakout_high)
    buy, sell = split_buy_sell(scored, top)
    fav = select_favorites(scored, favorites)
    breakout = select_breakouts(scored, top)
    brk_watch = select_breakout_watch(scored, breakout_watch)
    bulk_buy, bulk_sell = aggregate_bulk(bulk, top)

    # FII/DII endpoint only serves the LATEST day's figures (no history). Keep
    # them only when they match `day` -- otherwise a backfill would mislabel
    # today's flows as a past date's.
    fii_dii = [f for f in fetch_fii_dii(session) if _fii_date_matches(f, day)]
    return Report(day, buy, sell, bulk_buy, bulk_sell, fii_dii, fav, breakout, brk_watch)


def _fii_date_matches(flow: dict, day: dt.date) -> bool:
    raw = flow.get("date")
    if not raw:
        return False
    try:
        return dt.datetime.strptime(raw, "%d-%b-%Y").date() == day
    except (TypeError, ValueError):
        return False


def main(argv=None) -> int:
    args = parse_args(argv)
    # Ensure em-dash / unicode in titles render on Windows consoles (cp1252).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    session = make_session()

    start = (dt.datetime.strptime(args.date, "%Y-%m-%d").date()
             if args.date else dt.date.today())

    try:
        rep = build_report(session, start, top=args.top, vol_window=args.vol_window,
                           breakout_window=args.breakout_window, no_cache=args.no_cache)
    except Exception as exc:
        print(f"ERROR: could not fetch NSE delivery data: {exc}", file=sys.stderr)
        return 1
    day, buy, sell = rep.day, rep.buy, rep.sell
    bulk_buy, bulk_sell = rep.bulk_buy, rep.bulk_sell

    print(f"\nNSE report for trading day: {day:%d-%b-%Y}  "
          f"(vol window {args.vol_window}d, top {args.top})\n")

    if rep.fii_dii:
        asof = rep.fii_dii[0].get("date") or ""
        print_section(
            f"0) FII / DII CASH FLOWS (₹ crore, {asof})",
            _format_fii_dii(rep.fii_dii),
        )

    dfloats = ["SCORE", "CLOSE_PRICE", "PCT_CHANGE", "DELIV_PER", "VOL_RATIO"]
    print_section(f"1) DELIVERY — TOP {args.top} BUY (by delivery score)",
                  _format_table(buy, dfloats))
    print_section(f"2) DELIVERY — TOP {args.top} SELL (by delivery score)",
                  _format_table(sell, dfloats))
    print_section(f"3) BULK DEALS — TOP {args.top} BUY (by quantity)",
                  _format_table(bulk_buy, ["VALUE_CR"]))
    print_section(f"4) BULK DEALS — TOP {args.top} SELL (by quantity)",
                  _format_table(bulk_sell, ["VALUE_CR"]))
    print_section(f"5) BREAKOUT — TOP {args.top} "
                  f"(new {args.breakout_window}d high + volume surge)",
                  _format_table(rep.breakout, dfloats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
