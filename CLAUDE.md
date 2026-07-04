# CLAUDE.md

Guidance for Claude Code working in this repo. See `README.md` for user-facing docs.

## What this is

A local **stock-analysis portal** for NSE (India) market data. Module 1 is an NSE
delivery + bulk-deals report with daily persistence and pick follow-up. Designed to
grow: more analysis modules get added over time.

- **Platform:** Windows 11, Python 3.12, PowerShell 5.1 (no `&&` chaining — use `;`).
- **Node 24 / npm** available for the frontend.

## Layout

```
delivery_report.py   NSE fetch + scoring ENGINE. CLI entry point AND the shared
                     library: build_report() returns a Report NamedTuple. All
                     fetchers/scoring live here; reuse them, don't reimplement.
app/                 FastAPI backend package
  config.py            constants, DATABASE_URL (env PORTAL_DATABASE_URL overrides)
  db.py                SQLAlchemy 2.0 engine, SessionLocal, Base, init_db, get_db dep
  models.py            ORM: DeliveryPick, BulkDeal (unique on report_date+side+symbol),
                       MarketFlow (FII/DII aggregate, unique on report_date+category),
                       Favorite (watchlist symbols)
  store.py             save_report() = idempotent upsert (delete-by-date + insert);
                       loaders return JSON-friendly dicts (NaN -> None via _num/_int)
  quotes.py            yfinance live + EOD closes (the follow-up data source)
  followup.py          compute_followup(): EOD return %, signal hit-rate, live move
  service.py           run_and_store(): build_report -> save_report
  api.py               FastAPI app; routers under /api; mounts frontend/dist at /
ingest.py            CLI: fetch+score+store one day (cron target)
frontend/            Vite + React SPA (src/App.jsx is the whole UI; tabs per module)
data.db              SQLite (gitignored, created by first ingest)
.cache/              raw NSE CSV downloads (gitignored)
```

## Running (from project root)

```powershell
pip install -r requirements.txt
cd frontend; npm install; npm run build; cd ..
python delivery_report.py                      # terminal report, no DB
python ingest.py                               # store latest day into data.db
python -m uvicorn app.api:app --port 8000      # API + built SPA on one port
cd frontend; npm run dev                       # dev SPA on :5173 (proxies /api -> :8000)
```

If launching uvicorn when the shell cwd isn't the project root, pass
`--app-dir <project-root>` so the `app` package and `delivery_report` import resolve.

## Data sources (all verified working from this environment)

- **NSE archive CSVs** via a browser-headed `requests` session (most JSON
  `/api/...` endpoints are 403/404-blocked here — do NOT rely on them):
  - delivery/bhavcopy `sec_bhavdata_full_DDMMYYYY.csv`
  - bulk `content/equities/bulk.csv`, block `content/equities/block.csv` (rolling)
  - equity master `content/equities/EQUITY_L.csv` (used to drop ETFs)
  - cap lists from `niftyindices.com` (NIFTY 100 = Large, Midcap 150 = Mid, else Small)
- **NSE `fiidiiTradeReact` JSON API** DOES work, but ONLY after a cookie handshake
  (`GET nseindia.com` first) and with `Accept-Encoding: gzip, deflate` (NOT `br` —
  brotli isn't installed, so a `br` response is undecodable). Returns the LATEST
  day's market-wide FII/DII only — no history; `build_report` keeps it only when
  its date matches the trading day so backfills don't mislabel it.
- **Yahoo Finance** (`yfinance`, `<SYMBOL>.NS`) for live/EOD follow-up quotes only.

NSE is rate-limited; downloads are cached under `.cache/`. Use `--no-cache` to refresh.

## Conventions / gotchas

- **Delivery score** is a tunable composite — weights/caps are named constants at the
  top of `delivery_report.py` (`W_DELIV`, `W_VOL`, `W_PCT`, `*_CAP`). Documented there.
- **Universe = listed equities only**: filtered to `SERIES==EQ` AND present in the
  equity master, so ETFs are excluded.
- **CAP** is open-ended Small: anything outside NIFTY top-250 defaults to "Small".
- **bulk.csv is rolling** (recent days only) — past `--date` may yield empty bulk
  sections; this is handled gracefully, not an error.
- **NaN handling**: pandas NaN must become `None` before hitting the DB/JSON — use the
  `_num`/`_int` helpers in `store.py`.
- **Idempotency**: re-ingesting a date overwrites it (delete-then-insert in save_report).
  Exception: FII/DII rows are only replaced when the (flaky) API returned data, so a
  transient failure never wipes good flows.
- **Favorites** are stored as `DeliveryPick` rows with `side="FAV"` (reusing all the
  same columns); the watchlist itself is the `Favorite` table. `build_report(...,
  favorites=[...])` returns their full scored rows via `select_favorites`, so a
  favorite outside the top-N still gets every metric. Loaders filter BUY/SELL, so FAV
  never leaks into the ranked lists.
- Windows console is cp1252; `delivery_report.py` reconfigures stdout to UTF-8 for the
  em-dash in titles.

## Adding a new analysis module (the intended growth path)

1. Add ORM model(s) in `app/models.py`.
2. Add `app/<module>.py` with the fetch/compute logic (reuse `delivery_report`
   fetchers or add new ones; keep external feeds in their own module like `quotes.py`).
3. Add storage in `store.py` (idempotent) if it persists.
4. Add an `APIRouter` in `app/api.py` under `/api` and `include_router` it.
5. Add a tab + view in `frontend/src/App.jsx` and an `api.js` method.

## Verifying changes

- CLI: `python delivery_report.py --top 5` should print 4 sections.
- Backend: `python ingest.py` then `GET /api/dates`, `/api/delivery/{date}`,
  `/api/followup/{date}`.
- Frontend: `npm run build` must succeed; preview/screenshot the running app and
  check the browser console has no errors.
