#!/usr/bin/env python3
"""Daily ingest: fetch + score the latest trading day and store it in the DB.

Idempotent — re-running the same date overwrites that date's rows. Schedule this
after market close (see README) to build history automatically.

    python ingest.py                      # latest trading day
    python ingest.py --date 2026-06-19
    python ingest.py --top 15 --vol-window 30 --no-cache
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from app.config import DEFAULT_BREAKOUT_WINDOW
from app.db import SessionLocal, init_db
from app.service import run_and_store


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Ingest one trading day's NSE report into the DB.")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: latest available).")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--vol-window", type=int, default=20)
    p.add_argument("--breakout-window", type=int, default=DEFAULT_BREAKOUT_WINDOW)
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args(argv)

    init_db()
    start = dt.datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    db = SessionLocal()
    try:
        result = run_and_store(db, start, top=args.top, vol_window=args.vol_window,
                               breakout_window=args.breakout_window, no_cache=args.no_cache)
    except Exception as exc:
        print(f"ERROR: ingest failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"Stored report for {result['date']}: "
          f"{result['picks']} picks, {result['bulk_deals']} bulk-deal rows, "
          f"{result.get('fii_dii', 0)} FII/DII rows, "
          f"{result.get('breakout', 0)} breakout picks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
