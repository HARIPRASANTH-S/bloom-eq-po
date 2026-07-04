# Design: "Loss-Makers with High Delivery" screener + Terminal-caliber roadmap

Status: design approved for implementation section-by-section. No code written yet.
Author: architecture session, 2026-07-04. All data-source claims in the
"VERIFIED" blocks below were live-tested from this machine on that date with
real HTTP calls; everything else is explicitly marked "needs live test".

---

## 0. Live verification results (done 2026-07-04, from this environment)

These were actually fetched and inspected — treat them as ground truth until
NSE breaks something (see §D4 monitoring):

**V1 — NSE quarterly financial-results index API: WORKS.**
`GET https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly`
- Requires the same cookie handshake as fiidiiTradeReact (`GET nseindia.com`
  first). Note: the handshake GET itself returned **403**, but Akamai still
  set usable cookies — the subsequent API call returned **200**. Do not treat
  a 403 handshake as fatal; only treat the API call's status as authoritative.
- Headers needed: browser User-Agent, `Accept-Encoding: gzip, deflate` (never
  `br`), and a Referer of
  `https://www.nseindia.com/companies-listing/corporate-filings-financial-results`.
- Response: JSON list, **3,814 records**, ~220 KB gzipped. Fields per record:
  `symbol`, `companyName`, `isin`, `period` ("Quarterly"), `fromDate`,
  `toDate`, `relatingTo`, `financialYear`, `audited`, `consolidated`
  ("Consolidated"/"Non-Consolidated"), `bank` ("Y"/"N"), `broadCastDate`,
  `filingDate`, `seqNumber`, `xbrl` (archive URL), `resultDetailedDataLink`
  (**always null — do not design around it**).
- **Limitation: ROLLING.** The list covers roughly the trailing quarter of
  filings only. Like bulk.csv, it must be harvested continuously; it is not a
  history API.

**V2 — Per-filing XBRL on nsearchives: WORKS and contains net P&L.**
Example: `https://nsearchives.nseindia.com/corporate/xbrl/INDAS_121104_1430340_02052025114555.xml`
- Same archive host the bhavcopy already uses; no cookie needed; ~20 KB per
  filing, served gzip (decompress like everything else NSE).
- 3,796 of the 3,814 index records had a real XBRL link.
- Verified tags present (Ind-AS taxonomy, prefix `in-bse-fin`):
  - `in-bse-fin:ProfitLossForPeriodFromContinuingOperations`
    (contextRef `OneD` = the reported quarter; value in INR, signed —
    the sample showed `-9237000.00`, a loss)
  - `in-bse-fin:DateOfEndOfReportingPeriod` (e.g. `2024-12-31`)
  - `xbrli:identifier scheme=".../NSESymbol"` → NSE symbol
- Caveats: banks/insurers (`bank == "Y"` in V1) file under a different
  taxonomy — expect parse failures and fall back per §1.4. Companies file
  both Consolidated and Non-Consolidated — keep both, prefer Consolidated.

**V3 — yfinance quarterly income statements for .NS tickers: WORK.**
`yf.Ticker("IDEA.NS").quarterly_income_stmt` → `Net Income` row present, 5-6
quarters of history. Verified IDEA.NS shows Dec-25/Sep-25/Jun-25/Mar-25 all
negative and a huge **one-off positive** latest quarter (Mar-26) — a live
demonstration of why "loss-making" must be defined on trailing-twelve-month /
multi-quarter terms, not the single latest quarter (§1.2).
- Limitation: per-symbol calls (~1-2 s each, unofficial API, rate-limit
  politely). Fine for tens of symbols per day; not for the full 2,000-name
  universe. Use as backfill/fallback only.

**V4 — niftyindices NIFTY 500 constituents CSV has an `Industry` column: WORKS.**
`https://niftyindices.com/IndexConstituent/ind_nifty500list.csv`
→ `Company Name, Industry, Symbol, Series, ISIN Code` (e.g. 360ONE →
"Financial Services"). Same host/headers the app already uses for cap lists.
This is the free sector map (§P3). The smallcap-250 / microcap-250 lists on
the same host presumably share the format — quick-verify at implementation.

**Needs live test before building against (plausible, same handshake pattern
as V1, but NOT verified):**
- `https://www.nseindia.com/api/corporates-corporateActions?index=equities`
- `https://www.nseindia.com/api/corporate-announcements?index=equities`
- `yf.Ticker("^NSEI")` history for NIFTY benchmark returns
- TradingView `lightweight-charts` npm package (Apache-2.0) for candlesticks

---

# TASK 1 — "Loss-Making Companies with Good Delivery %"

## 1.1 Interpretation: recommend (c), a hybrid — and here is the override

Your option (a) alone (pure fundamentals) was assumed to be blocked on data;
**it isn't** — V1+V2 prove a fully NSE-native fundamentals path exists. Your
option (b) alone (price-only "loss-making") is buildable today but the name
would be a lie: a stock 25% off its high is not "loss-making", it's "losing".
Those are different investment theses and conflating them silently is exactly
the ambiguity you flagged.

**Recommended design — two decoupled parts:**

1. **A daily technical gate + score** ("quiet accumulation into weakness"):
   deep drawdown + unusually high delivery, computed entirely from data
   already flowing through `build_report()`. Ships first, zero data risk.
2. **A fundamentals flag** (`IS_LOSS_MAKER`, from harvested XBRL + yfinance
   backfill) that the screener *filters on* when available. The screener
   section is "fundamentally loss-making companies being quietly accumulated
   on dips" — the literal ask — with an explicit count of technical
   qualifiers excluded for missing fundamentals, so nothing is hidden.

This is the most-value/least-risk split: part 1 works even if NSE breaks the
results API next month; part 2 is additive, isolated, and builds a permanent
fundamentals asset the whole app can reuse (symbol pages, screener engine,
results calendar).

Why this beats pure (b): high delivery% into a drawdown on a *profitable*
company is ordinary value buying — mildly interesting. The same pattern on a
*loss-making* company is anomalous: someone with size is accumulating a name
the P&L says to avoid. That divergence (turnaround bets, promoter buying,
insider positioning ahead of a results inflection — IDEA-style) is the actual
signal you asked for, and it's only expressible with the fundamentals gate.

## 1.2 Definitions and scoring (constants at top of `delivery_report.py`, same style as W_DELIV block)

**Fundamental gate** (from `QuarterlyResult` rows, §1.5):

```
IS_LOSS_MAKER = (TTM_PAT < 0)            # sum of net profit over the most
                                         # recent up-to-4 reported quarters
LOSS_QTRS     = count of negative quarters among the last 4 reported
```

TTM, not latest-quarter, because of exactly what V3 showed for IDEA: one
giant one-off positive quarter after four losses. Expose `LOSS_QTRS` as a
column so the user can see "4/4" vs "2/4" conviction. Prefer Consolidated
rows where both exist; if a symbol has <2 reported quarters on file, treat
fundamentals as *unknown* (excluded + counted, never guessed).

**Technical qualification gates** (all must pass; universe = the existing
scored frame, so EQ-series/equity-master/MIN_TURNOVER filters already apply):

```
ACCD_DD_WINDOW        = 120     # trading days for the reference high (~6 months)
ACCD_DD_MIN           = 0.20    # close must be >= 20% below that high ("in a real drawdown")
ACCD_DELIV_MIN        = 55.0    # absolute DELIV_PER floor
ACCD_DELIV_RATIO_MIN  = 1.20    # today's DELIV_PER / own trailing-20d avg DELIV_PER
ACCD_TURNOVER_MIN_LACS = 500.0  # stricter liquidity bar than global MIN_TURNOVER_LACS —
                                # loss-making microcaps are manipulation bait; ~Rs 5 cr/day min
ACCD_PRICE_FLOOR      = 20.0    # no penny stocks
```

`DELIV_RATIO` (today's delivery% vs the stock's *own* recent norm) is the key
new metric and an upgrade over raw DELIV_PER everywhere: DELIV_PER baselines
differ wildly per stock (holding-company stocks sit at 80%+ every day; F&O
names at 30%). Relative delivery is what "unusual accumulation" actually
means. Recommend also surfacing it in the main BUY/SELL tables later.

**Score** (rank among qualifiers; same transparent weight-and-cap shape):

```
W_ACCD_DELIV  = 0.35    # absolute delivery%           deliv_n  = clip(DELIV_PER/100, 0, 1)
W_ACCD_DRATIO = 0.30    # delivery vs own norm         dratio_n = clip((DELIV_RATIO-1)/(ACCD_DELIV_RATIO_CAP-1), 0, 1)
W_ACCD_DD     = 0.20    # drawdown depth               dd_n     = clip((DD-ACCD_DD_MIN)/(ACCD_DD_CAP-ACCD_DD_MIN), 0, 1)
W_ACCD_VOL    = 0.15    # volume surge                 vol_n    = clip(VOL_RATIO/VOL_RATIO_CAP, 0, 1)
ACCD_DELIV_RATIO_CAP = 2.0
ACCD_DD_CAP          = 0.50   # beyond -50% adds no more score (deeper != better, it's junkier)
ACCD_SCORE = 100 * (W_ACCD_DELIV*deliv_n + W_ACCD_DRATIO*dratio_n + W_ACCD_DD*dd_n + W_ACCD_VOL*vol_n)
where DD = 1 - CLOSE_PRICE / max(HIGH_PRICE over prior ACCD_DD_WINDOW days)
```

Note PCT_CHANGE direction is deliberately NOT a gate: accumulation days can
close green or red. It stays visible as a column.

## 1.3 Engine changes in `delivery_report.py`

**Refactor (override recommendation): merge the walk-back fetchers.**
`fetch_history_volumes` and `fetch_breakout_highs` are already two copies of
the same cached-bhavcopy walk; this feature would need a third (trailing avg
DELIV_PER + 120-day high). Instead, replace all three with one
`fetch_history_aggregates(session, ref_date, no_cache) -> pd.DataFrame`
that walks back `max(BREAKOUT_WINDOW, ACCD_DD_WINDOW, vol lookback)` days
**once** and returns per-symbol columns:
`AVG_VOL` (vol-lookback window), `HIGH_MAX_BRK` (BREAKOUT_WINDOW),
`HIGH_MAX_ACCD` (ACCD_DD_WINDOW), `AVG_DELIV_PER_20` (20 days).
Every day's CSV is disk-cached already, so this is strictly cheaper than
today. `build_report()` passes the frame into `compute_scores`, which adds
`DELIV_RATIO`, `DRAWDOWN`, `ACCD_SCORE`, `IS_ACCD` columns next to the
existing SCORE/IS_BREAKOUT logic. New selector `select_accd(scored, top,
loss_flags) -> pd.DataFrame` mirroring `select_breakouts`.

**New fetchers (fundamentals — these live in `delivery_report.py` per the
"all fetchers live here" convention, but see §1.6 for where harvesting runs):**

- `fetch_financial_results_index(session) -> list[dict]` — V1 endpoint,
  exact headers as documented in §0. Returns the raw record list. Tolerant of
  handshake 403 (proceed if the API call itself returns 200 + parseable JSON).
- `parse_xbrl_result(xml_bytes) -> dict | None` — stdlib
  `xml.etree.ElementTree`; extract symbol (xbrli:identifier, NSESymbol
  scheme), period end (`DateOfEndOfReportingPeriod`, contextRef `OneD`), and
  net profit: try `in-bse-fin:ProfitLossForPeriod` first, fall back to
  `ProfitLossForPeriodFromContinuingOperations` (both contextRef `OneD`).
  Return `None` on any missing tag (bank taxonomy etc.) — never raise.
- yfinance fallback goes in **`app/quotes.py`** (external-feed module
  convention): `fetch_quarterly_net_income(symbols) -> dict[str, list[(period_end, net_income)]]`
  via `Ticker(f"{sym}.NS").quarterly_income_stmt` "Net Income" row (V3).
  Called only for symbols that pass the technical gate but have no XBRL rows
  yet — tens of symbols/day at most, with a polite delay.

## 1.4 Storage (`app/models.py`, `app/store.py`)

**New model — the app's first non-daily table, so idempotency rules differ:**

```python
class QuarterlyResult(Base):
    __tablename__ = "quarterly_results"
    id            Mapped[int]  pk
    symbol        Mapped[str]  indexed
    period_end    Mapped[dt.date]
    consolidated  Mapped[bool]
    net_profit    Mapped[Optional[float]]   # INR, signed
    source        Mapped[str]               # "xbrl" | "yf"
    filing_date   Mapped[Optional[dt.date]]
    __table_args__ = (UniqueConstraint("symbol", "period_end", "consolidated"),)
```

Store rule: **upsert by unique key, never delete-by-date** — fundamentals are
an append-only asset; a flaky harvest run must never remove good rows
(same spirit as the FII/DII exception). On conflict prefer `source="xbrl"`
over `"yf"` (never overwrite xbrl with yf).

**Picks: reuse `DeliveryPick` with `side="ACCD"`** (FAV/BRKW precedent), plus
four new **nullable** columns on DeliveryPick so existing rows/loaders are
untouched: `drawdown_pct`, `deliv_ratio`, `ttm_net_profit`, `loss_qtrs`.
SQLite migration is four `ALTER TABLE ... ADD COLUMN` statements (portable to
Postgres). Loaders keep filtering BUY/SELL so ACCD never leaks into ranked
lists; NaN→None through the existing `_num`/`_int` helpers.

## 1.5 API (`app/api.py`)

- `GET /api/accdip/{date}` → `{ "rows": [...ACCD picks with the new
  columns...], "excluded_no_fundamentals": <int> }` — the count keeps the
  fundamentals gap honest in the UI.
- `GET /api/fundamentals/{symbol}` → quarterly history from QuarterlyResult
  (per-quarter net profit, source, TTM sum) — also the future symbol-page feed.

## 1.6 Ingest & idempotency

`ingest.py` / `service.run_and_store()` gains a `harvest_results(session, db)`
step, wrapped in its own try/except so a results-API failure can never fail
the daily price ingest:

1. Fetch V1 index once. Skip records already present (track max seen
   `seqNumber` in a small `SourceStatus`/kv row, or just upsert-dedupe).
2. Download + parse new XBRLs (cache the XMLs under `.cache/xbrl/` like
   everything else; ~50-150 files/day in results season, near zero
   off-season, ~20 KB each — negligible).
3. Upsert QuarterlyResult rows; yfinance fallback only for that day's
   technical qualifiers still missing fundamentals.

Because V1 is **rolling (~1 quarter)**, the harvester must run at least a few
times per quarter from now on to build history — history *before* harvest
start comes from the yfinance fallback (5-6 quarters deep, V3), which is
enough for TTM immediately.

## 1.7 CLI + frontend

- CLI: new section `LOSS-MAKERS — HIGH DELIVERY INTO WEAKNESS` after
  BREAKOUT. Columns: `SYMBOL CAP SCORE CLOSE PCT_CHANGE DELIV_PER DELIV_RATIO
  DD% VOL_RATIO TTM_PAT(₹Cr) LOSS_QTRS`. Footer line: `N technical qualifiers
  excluded (no fundamentals on file)`.
- Frontend: new tab "Loss Accum" in `App.jsx`, same table columns,
  `TTM_PAT` red-negative formatting, `LOSS_QTRS` as "3/4" badge; an expand
  row (or later the symbol page) renders `/api/fundamentals/{symbol}`.

## 1.8 Edge cases

- **No qualifiers on a day** — the normal case (this is a rare-signal
  screener). Store zero rows; API returns empty list + the exclusion count;
  CLI/UI render an explicit "none today", not an error.
- **Missing fundamentals** — excluded from the list, surfaced as a count,
  backfilled by the yf fallback over subsequent days.
- **Illiquid microcaps** — handled by ACCD_TURNOVER_MIN_LACS (5× the global
  floor) + ACCD_PRICE_FLOOR; both named constants to tune.
- **Splits/bonuses** — a 1:5 split looks like an 80% "drawdown" in raw
  bhavcopy history. Interim guard: if `PREV_CLOSE` from today's bhavcopy
  differs >20% from the prior day's stored close, skip the symbol's DD gate
  that day. Real fix is corporate-actions-aware adjustment (§P5) — this bug
  already affects BREAKOUT (a bonus makes a true breakout undetectable), so
  the fix pays twice.
- **Bank/insurer XBRL taxonomy** — parse returns None → yf fallback → else
  "unknown fundamentals" bucket. Never crash the harvest.
- **Results API disappears** — screener degrades to yf-only fundamentals for
  gate candidates; technical gate unaffected. Log to SourceStatus (§D4).

---

# TASK 2 — Roadmap to a terminal-caliber tool

## 2.0 The honesty ledger

Achievable free, and genuinely desk-grade: **EOD analytics excellence** —
full-universe daily history, delivery/volume anomaly detection, fundamentals
via XBRL, sector relative strength, user-defined screens, compound signals,
alerting on ingest, portfolio marks, candlestick charting. That is most of
what a swing/positional trader uses a terminal for.

Not achievable free (don't chase): real-time ticks/order-book depth, options
chains (NSE blocks the endpoints here), per-symbol FII/DII, consensus
estimates, broker research, transcripts. If intraday/real-time ever matters,
that's a paid feed (e.g. a broker API like Zerodha Kite Connect, ~₹500/mo, or
TrueData/GlobalDataFeeds) — **open question O1, your call, not assumed.**

## 2.1 The one architectural change everything hangs on

Today the app stores only *picks* (top-N rows) and recomputes rolling stats
by re-parsing ~250 cached CSVs per run. That caps every ambition: you can't
chart, screen historically, compute relative strength, or alert on "entered a
screen" from top-N snapshots.

**P1 — persist the full scored universe daily.** New table `daily_metrics`
(or `daily_bars`): one row per symbol per trading day — OHLC, prev_close,
volume, turnover, DELIV_QTY, DELIV_PER, VOL_RATIO, DELIV_RATIO, SCORE, CAP,
IS_BREAKOUT, DRAWDOWN. ~2,000 rows/day ≈ 500k rows/year — trivial for SQLite,
comfortable in Postgres. Written idempotently (delete-by-date + insert) as a
new step in `save_report`. Add `ingest.py --from YYYY-MM-DD --to YYYY-MM-DD`
backfill (the bhavcopy archive serves historical dates; polite delay; bulk.csv
sections will be empty for old dates — already handled). Backfill 2 years.
After P1, every rolling metric becomes a query over the DB instead of a
250-file walk, and ingest gets dramatically faster.

This is the data spine. It is ranked #1 below because P2-P6 all consume it.

## 2.2 Phases

**P0 — Ops floor (do alongside P1, not before or after):**
- Tests: pytest with small fixture CSVs checked into `tests/fixtures/`
  (trimmed real bhavcopy/bulk/EQUITY_L files). Cover: parsers, `compute_scores`
  golden values, `save_report` idempotency (ingest same date twice → same
  rows), NaN→None, and the XBRL parser (V2 sample as fixture). ~15 tests.
  That's the whole suite this project needs now; more is premature.
- Silent-breakage monitoring: `SourceStatus` table (source, last_ok_at,
  last_error, last_row_count) updated by every fetcher; `GET /api/health`;
  ingest warns when a source's row count drops below ~70% of its trailing
  median (NSE endpoints fail *quietly* — a 200 with a stub payload — so row
  counts, not status codes, are the tripwire).
- Not needed yet: CI, Docker, structured logging frameworks, Postgres
  migration (env-var portability is already maintained — keep it that way).

**P1 — Data spine** (§2.1) + the Task 1 screener (its fundamentals harvester
is the second leg of the spine).

**P2 — Symbol page + charting + command bar (the "terminal feel" phase):**
- `GET /api/bars/{symbol}?from=&to=` straight off daily_metrics; no new
  external source. Chart: candlesticks + MA20/50/200 + volume pane +
  **DELIV_PER line overlay** (that overlay is your differentiator — no free
  charting site shows delivery% on the chart). Library: TradingView
  `lightweight-charts` (Apache-2.0, canvas, tiny, no external calls —
  verify license/API at implementation; fallback: ECharts).
- Symbol page assembles what already exists: chart, latest metrics, pick
  history (all sides), fundamentals table (§1.5), bulk-deal appearances.
- Command bar: **worth it, in reduced form.** Not Bloomberg's function-code
  emulation — a Ctrl+K palette with a 20-line grammar:
  `IDEA` → symbol page; `IDEA C` → chart; `IDEA F` → fundamentals;
  `S <screen-name>` → run screen; `D 2026-06-27` → jump to date; bare text →
  fuzzy symbol search (equity master is already in the DB). One React
  component + a token parser + a route registry. The muscle-memory payoff is
  real; the complexity is not, *provided* it ships after symbol pages exist
  (before that there's nothing to navigate to).

**P3 — Sector & relative strength:**
- Sector map: V4 (`ind_nifty500list.csv` Industry column) — covers
  effectively the whole liquid universe; others bucket as "Other".
  New `symbol_meta` table (symbol, industry, cap), refreshed weekly.
- Views: industry treemap heatmap (size = turnover, color = median %change);
  sector RS = median N-day return per industry vs NIFTY (^NSEI via yfinance —
  needs quick live test); per-stock RS percentile within industry.
  All computed from daily_metrics — zero new daily fetches.

**P4 — Screener engine + compound signals + alerts (one phase — they're the
same machinery):**
- Metric registry: dict of {metric name → daily_metrics column, type, unit}.
- Screens as data, not code: JSON condition tree
  `{"all": [{"m": "DELIV_PER", "op": ">=", "v": 60}, {"m": "DRAWDOWN", "op": ">=", "v": 0.2}]}`
  evaluated in pandas over a date's frame. No eval(), no SQL injection
  surface. `SavedScreen` model (name, json, created_at). "Sharing" = JSON
  export/import — it's a solo app; anything more is over-build.
- The hardcoded sections (BUY/SELL/BREAKOUT/ACCD) become *system screens* in
  the same registry — one rendering path, and the CLI keeps its sections.
- **Compound signals:** a `confluence` view needs no new storage — all picks
  already share one table keyed (report_date, side, symbol). 
  `GET /api/confluence/{date}` groups by symbol across sides + same-day bulk
  BUY appearances + screen memberships, returns symbols with ≥2 concurrent
  signals, badge-rendered on every table and on the symbol page. Cheap, high
  value, do early in P4.
- **Alerts, honestly pull-based:** this app ingests once daily; pretending
  otherwise is theater. An alert = saved screen + channel. After each ingest,
  diff screen membership vs the previous trading day → "ENTERED/EXITED"
  events → notify. Channels, simplest first: Windows toast (PowerShell/
  BurntToast) since it's a local app; ntfy.sh or a Telegram bot for phone
  push (both free; needs a token/topic — **open question O2**). Optional
  later: an opt-in intraday loop polling yfinance for favorites every few
  minutes during market hours (separate process, threshold alerts only).

**P5 — Portfolio + corporate actions:**
- `Position` model (symbol, qty, avg_cost, opened_at, closed_at, notes);
  marks via existing `quotes.py`; P&L view reuses the followup machinery
  (which already computes EOD returns from an anchor date/price). Favorites
  stay watch-only; positions are money. Broker-CSV import (Zerodha tradebook)
  is a stretch goal — **open question O3**.
- Corporate actions: **worth it** — not as news, but as *data correctness*
  (split/bonus adjustment for DD/breakout/charts, §1.8) plus a dividend/
  ex-date column. Source: `corporates-corporateActions` API (needs live
  test, same handshake family as V1 which does work here).
- Announcements feed: **medium value** — a "results filed today" list falls
  out of the V1 index already harvested; a general announcements ticker
  (needs live test) is nice-to-have.
- General news scraping: **recommend against** (overriding your list item).
  High maintenance, low edge, legally grey, and off this app's quantitative
  spine. A "open on Screener.in / Google News" deep-link per symbol delivers
  80% of the value for zero maintenance.

**P6 — Terminal UX polish:**
What "trading-desk" actually requires beyond a Tailwind dashboard: density
(12-13px, `font-variant-numeric: tabular-nums`, monospace numerics, tables
not cards, no whitespace padding-as-design), a persistent dark theme with
semantic red/green reserved for signed numbers only, keyboard-first (Ctrl+K
from P2, j/k row navigation, Enter → symbol page), a two-pane master-detail
layout (list left, symbol page right — resist golden-layout multi-pane
docking until a real need appears), and "alive" cues that are honest about
EOD data: last-ingest timestamp chip, and a 60s yfinance soft-refresh of
live quotes only on the favorites/portfolio panes while open. Also the point
where `App.jsx` must split into components/routes — do it in this phase, not
speculatively before.

**Explicitly not on the roadmap (bad ideas for this project now):**
real-time streaming infra, WebSockets, Redis, task queues, microservices,
multi-user auth (open question O4 — changes storage, hosting, and NSE-TOS
posture; decide before building, not after), full Bloomberg function-code
emulation, and news NLP/sentiment.

## 2.3 Next 3-5 by leverage (impact ÷ effort)

1. **P1 data spine** — daily_metrics + backfill + `--from/--to` + P0 tests/
   health riding along. Medium effort, unlocks literally everything else,
   and makes daily ingest faster. Do first, no contest.
2. **Task 1 screener** — technical gate immediately, XBRL harvester in
   parallel. Medium effort; a genuinely novel signal (delivery-into-weakness
   gated on real losses) no free tool offers; starts the fundamentals asset.
3. **Symbol page + chart with delivery% overlay + Ctrl+K palette (P2)** —
   medium effort, transforms the product from "report viewer" to "terminal";
   the delivery overlay is the differentiator.
4. **Sector map + heatmap/RS (P3)** — small-to-medium effort on a verified
   source (V4); big analytical payoff (is this stock weak or is its whole
   sector weak?).
5. **Screener engine + confluence + ingest-time alerts (P4)** — the biggest
   single feature; do it after the spine and symbol pages exist so screens
   have somewhere to link.

## 2.4 Open questions (yours, not mine)

- **O1:** Does intraday/real-time ever matter to you? If yes → paid feed
  (broker API ~₹500/mo is the realistic path); it changes the architecture
  (a polling worker + websocket to the SPA). Default assumption: no.
- **O2:** Alert channel — Windows toast only, or phone push (Telegram bot
  token / ntfy.sh topic — you create the credential)?
- **O3:** Portfolio import from broker CSV (which broker?) or manual entry
  only?
- **O4:** Will this ever be multi-user / hosted? If plausibly yes, flip to
  Postgres at P1-time (env var already supports it) and keep NSE fetching
  server-side-single-instance to respect rate limits.
