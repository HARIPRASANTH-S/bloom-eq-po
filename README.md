# Stock Analysis Portal

Pulls the latest completed NSE (India) trading day's data into a 4-section report:

1. **DELIVERY — TOP N BUY** (by delivery score, names that closed *up*)
2. **DELIVERY — TOP N SELL** (by delivery score, names that closed *down*)
3. **BULK DEALS — TOP N BUY** (by quantity)
4. **BULK DEALS — TOP N SELL** (by quantity)

It ships in two forms that share one engine:

- **CLI** (`delivery_report.py`) — prints the terminal report.
- **Web portal** (`app/` FastAPI + SQLite + `frontend/` React) — stores every
  day's report in a local DB and tracks how past picks have moved since
  ("yesterday's report, updated with today's movement").

## Architecture

```
delivery_report.py   NSE fetch + scoring engine (CLI + build_report()) — shared by all
app/                 FastAPI backend
  config.py            paths, DB URL (SQLite now; set PORTAL_DATABASE_URL for Postgres)
  db.py  models.py     SQLAlchemy engine + tables (delivery_picks, bulk_deals)
  store.py             idempotent upsert (delete-by-date + insert) and loaders
  quotes.py            yfinance live + EOD quotes (follow-up data source)
  followup.py          pick-performance (EOD return + signal hit-rate) and live move
  service.py           build → store orchestration
  api.py               REST routers (new modules = new routers here)
ingest.py            daily fetch → DB (cron target)
frontend/            Vite + React SPA (Delivery Report + Pick Follow-up tabs)
data.db              SQLite, created on first ingest
```

New analysis modules slot in as additional SQLAlchemy models + an `app/<module>.py`
service + a router included in `api.py`, plus a tab in the SPA.

## How to run — every entry point

There are four runnable pieces. Commands below are **Windows PowerShell** (the
project's shell); on macOS/Linux replace `;` chains with `&&` and use `python3`.

### 0. One-time setup

```powershell
# from the project root: E:\Projects\Delivery
pip install -r requirements.txt          # Python deps (pandas, requests, yfinance, fastapi, sqlalchemy)
cd frontend; npm install; npm run build   # JS deps + build the SPA into frontend\dist
cd ..
```

### 1. `delivery_report.py` — terminal report (no database)

Prints the 4-section report to the console. Standalone; touches no DB.

```powershell
python delivery_report.py                      # latest trading day
python delivery_report.py --date 2026-06-19    # a specific day
python delivery_report.py --top 15 --vol-window 30
python delivery_report.py --no-cache           # force re-download
```

### 2. `ingest.py` — store one trading day into the DB

Fetches + scores a day and upserts it into `data.db` (idempotent). Run daily to
build history (see [Scheduling](#scheduling-run-after-market-close)).

```powershell
python ingest.py                       # latest trading day
python ingest.py --date 2026-06-19     # backfill a specific day
python ingest.py --top 15 --vol-window 30 --no-cache
```

### 3. `app.api:app` — backend API (+ serves the built SPA)

Serves the REST API and, if `frontend\dist` exists, the React app on the **same port**.

```powershell
python -m uvicorn app.api:app --port 8000
# open http://localhost:8000   (API at http://localhost:8000/api/...)
```

### 4. `frontend` — the React SPA

- **Production:** already served by step 3 after `npm run build`. Just open
  http://localhost:8000.
- **Dev (hot reload):** run the backend (step 3) *and* the Vite dev server in a
  second terminal; Vite serves on `:5173` and proxies `/api` → `:8000`.

```powershell
cd frontend; npm run dev    # http://localhost:5173
```

### Typical first run

```powershell
pip install -r requirements.txt
cd frontend; npm install; npm run build; cd ..
python ingest.py                          # populate the DB
python -m uvicorn app.api:app --port 8000  # then open http://localhost:8000
```

### Pick follow-up

`ingest.py` saves each day's BUY/SELL picks. The **Pick Follow-up** tab (and
`GET /api/followup/{date}`) compares a past day's picks to the latest close and
the live intraday price (via Yahoo Finance), showing:

- **EOD return %** since the pick and a ✓/✗ for whether the signal was directionally
  right (BUY should rise, SELL should fall), plus a per-side **hit-rate / avg-return** scorecard;
- **live price + day %** during market hours.

So after running `ingest.py` daily, selecting yesterday's date shows how each pick
has moved today.

### Favorites (watchlist)

Star any stock (☆ in the Delivery Report) or type a symbol in the **★ Favorites**
tab to add it to your watchlist. On the next **Fetch latest**, every favorite gets
the **same full delivery details** as the ranked picks — score, close, %change,
`DELIV_PER`, `DELIV_QTY`, `VOL_RATIO`, cap, institutional flow — even if it's
nowhere near the top-10. The watchlist persists in the DB; favorites are stored
per day as `side="FAV"` rows so history is kept alongside the picks.

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | liveness |
| `GET /api/dates` | trading days stored in the DB |
| `GET /api/delivery/latest` | most recent stored report |
| `GET /api/delivery/{date}` | stored report for a date (includes `favorites`) |
| `GET /api/followup/{date}` | pick performance + live movement for a date's picks |
| `GET /api/favorites` · `POST /api/favorites/{symbol}` · `DELETE /api/favorites/{symbol}` | manage the watchlist |
| `POST /api/ingest?date=&top=&vol_window=&no_cache=` | fetch + store (also the "Fetch latest" button) |

## Data sources

Everything comes from NSE's public archive CSVs, fetched with a browser-like
`requests` session (retries + backoff) and cached under `.cache/`:

| Signal | Source |
| --- | --- |
| `CLOSE_PRICE`, `PCT_CHANGE`, `DELIV_PER`, `DELIV_QTY`, `VOLUME` | `sec_bhavdata_full_DDMMYYYY.csv` (security-wise delivery / full bhavcopy). `VOLUME` = total shares traded (`TTL_TRD_QNTY`); `DELIV_QTY` = shares delivered; `DELIV_PER` = `DELIV_QTY` ÷ `VOLUME` × 100. |
| `VOL_RATIO` | today's `TTL_TRD_QNTY` ÷ average over the prior `--vol-window` trading days (more bhavcopies) |
| `CAP` (Large/Mid/Small) | SEBI-aligned market-cap rank: NIFTY 100 → `Large` (rank 1–100), NIFTY Midcap 150 → `Mid` (101–250), **everything else → `Small`** (rank 251+, open-ended). Lists from `niftyindices.com`, refreshed by NSE twice a year. |
| Bulk deals (sections 3 & 4) | `content/equities/bulk.csv` (rolling recent bulk-deals file) |
| `INST_BUY` / `INST_SELL` | per-symbol institutional buy/sell **quantities** derived from **bulk + block deals** (`bulk.csv` + `block.csv`). `NaN` where a symbol had no institutional deal activity that day. |
| Follow-up quotes (live + latest close) | **Yahoo Finance** via `yfinance` (`<SYMBOL>.NS`). NSE's per-symbol live quote API is access-blocked, so the portal uses Yahoo for the pick-performance / live-movement view only. |
| **FII / DII cash flows** (top banner) | NSE `fiidiiTradeReact` JSON API (needs a cookie handshake). Market-wide aggregate buy/sell/net in ₹ crore for the day. Shown as section `0)` in the CLI and a banner atop the SPA. |

The delivery universe is restricted to **listed equities only** — symbols present in
NSE's equity master (`EQUITY_L.csv`). ETFs (and other non-equity instruments) trade
in the same `EQ` series but are absent from that file, so they're dropped.

> **On institutional data:** NSE does *not* publish per-symbol FII/DII buy/sell
> figures for free — the `fiidiiTradeReact` API gives only a **market-wide
> aggregate** (used for the top FII/DII banner), and the per-symbol quote API is
> blocked. For the per-symbol `INST_BUY`/`INST_SELL` columns, bulk + block deals
> (named large/institutional clients) are the only per-symbol institutional-flow
> data freely available, so those columns are sourced from there. If you get
> access to a true per-symbol institutional feed, swap it into
> `institutional_flow()`.

The three fetchers — `fetch_delivery`, `fetch_history_volumes`, `fetch_bulk_deals`
— are independent functions, so any single endpoint can be swapped without
touching the rest.

## Delivery score (tunable)

A transparent 0–100 composite, defined by named constants at the top of
`delivery_report.py`:

```
deliv_n = clip(DELIV_PER / 100, 0, 1)            # conviction / holding
vol_n   = clip(VOL_RATIO / VOL_RATIO_CAP, 0, 1)  # volume surge vs average
pct_n   = clip(abs(PCT_CHANGE) / PCT_CAP, 0, 1)  # move magnitude

SCORE = 100 * (W_DELIV*deliv_n + W_VOL*vol_n + W_PCT*pct_n)
```

Defaults: `W_DELIV=0.50`, `W_VOL=0.30`, `W_PCT=0.20`, `VOL_RATIO_CAP=5.0`,
`PCT_CAP=5.0`. The universe is filtered to `SERIES==EQ` (covers equities and most
ETFs) with a minimum turnover (`MIN_TURNOVER_LACS`) to drop illiquid noise.
BUY = positive `PCT_CHANGE`, SELL = negative, each ranked by `SCORE` descending.

Tune the weights/caps to taste — raise `W_DELIV` to favour high-delivery
conviction names, raise `W_VOL` to reward volume surges, raise a `*_CAP` to make
that signal harder to max out.

## CLI / ingest options

Both `delivery_report.py` and `ingest.py` accept the same flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--date` | latest trading day | `YYYY-MM-DD`; weekends/holidays fall back to the most recent trading day |
| `--top` | `10` | rows per section |
| `--vol-window` | `20` | lookback trading days for the `VOL_RATIO` average |
| `--no-cache` | off | re-download instead of reading `.cache/` |

First run downloads ~`vol-window` bhavcopies for the volume average; subsequent
runs are fast thanks to the cache.

## Notes

- `VALUE_CR` = Σ(qty × price) / 1e7 (₹ crores). Bulk deals include the trade
  price, so this is computed; it shows `NaN` only if a price is missing.
- The bulk-deals file (`bulk.csv`) is a *rolling recent* file. Requesting a
  `--date` older than what NSE currently keeps there yields empty sections 3 & 4
  (handled gracefully, not a crash).

## Scheduling (run after market close)

NSE publishes the full bhavcopy and bulk-deals file after the close (~6–7 pm IST).
Schedule **`ingest.py`** for the evening on weekdays so the DB grows automatically
(it's idempotent — re-running a date overwrites, never duplicates).

**Linux/macOS cron** (7:30 pm IST on Mon–Fri):

```cron
30 19 * * 1-5  cd /path/to/Delivery && /usr/bin/python3 ingest.py >> ingest.log 2>&1
```

(Set `TZ=Asia/Kolkata` in the crontab, or convert to the server's timezone.)

**Windows Task Scheduler** (PowerShell):

```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument "ingest.py" -WorkingDirectory "E:\Projects\Delivery"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30PM
Register-ScheduledTask -TaskName "NSE Portal Ingest" -Action $action -Trigger $trigger
```

(For just the terminal print without storing, swap `ingest.py` → `delivery_report.py`.)
