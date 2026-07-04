import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const fmt = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);
const fmtInt = (v) =>
  v === null || v === undefined ? "—" : Number(v).toLocaleString("en-IN");
const pctClass = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

// ----- Delivery section tables ----------------------------------------- //
function StarButton({ active, onClick }) {
  return (
    <button
      className={`star ${active ? "on" : ""}`}
      title={active ? "Remove from favorites" : "Add to favorites"}
      onClick={onClick}
    >
      {active ? "★" : "☆"}
    </button>
  );
}

function DeliveryTable({ title, rows, favSet, onToggleFav }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th></th><th>#</th><th>Symbol</th><th>Cap</th><th className="r">Score</th>
            <th className="r">Close</th><th className="r">%Chg</th>
            <th className="r">Deliv%</th><th className="r">Deliv Qty</th><th className="r">Volume</th><th className="r">VolRatio</th>
            <th className="r">InstBuy</th><th className="r">InstSell</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={13} className="empty">No data</td></tr>}
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td><StarButton active={favSet?.has(r.symbol)} onClick={() => onToggleFav(r.symbol)} /></td>
              <td>{r.rank}</td>
              <td className="sym">{r.symbol}</td>
              <td><span className={`cap cap-${(r.cap || "").toLowerCase()}`}>{r.cap || "—"}</span></td>
              <td className="r">{fmt(r.score)}</td>
              <td className="r">{fmt(r.close_price)}</td>
              <td className={`r ${pctClass(r.pct_change)}`}>{fmt(r.pct_change)}</td>
              <td className="r">{fmt(r.deliv_per)}</td>
              <td className="r">{fmtInt(r.deliv_qty)}</td>
              <td className="r">{fmtInt(r.volume)}</td>
              <td className="r">{fmt(r.vol_ratio)}</td>
              <td className="r">{fmtInt(r.inst_buy)}</td>
              <td className="r">{fmtInt(r.inst_sell)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BulkTable({ title, rows }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <table>
        <thead>
          <tr>
            <th>#</th><th>Symbol</th><th className="r">Deals</th>
            <th className="r">Total Qty</th><th className="r">Value (₹Cr)</th><th>Institutions</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={6} className="empty">No bulk deals</td></tr>}
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>{r.rank}</td>
              <td className="sym">{r.symbol}</td>
              <td className="r">{r.n_deals}</td>
              <td className="r">{fmtInt(r.total_qty)}</td>
              <td className="r">{fmt(r.value_cr)}</td>
              <td className="inst">{r.institutions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FlowBanner({ flows }) {
  if (!flows || flows.length === 0) return null;
  const asof = flows[0].date;
  const order = { FII: 0, DII: 1 };
  const sorted = [...flows].sort((a, b) => (order[a.category] ?? 9) - (order[b.category] ?? 9));
  return (
    <div className="card flow-banner">
      <h3>Ⓘ FII / DII Cash Flows <span className="muted">— ₹ crore · {asof}</span></h3>
      <div className="cards-row">
        {sorted.map((f) => (
          <div className="flow-card" key={f.category}>
            <div className="flow-cat">{f.category}</div>
            <div className="flow-nums">
              <span>Buy <b>{fmt(f.buy)}</b></span>
              <span>Sell <b>{fmt(f.sell)}</b></span>
              <span>Net <b className={pctClass(f.net)}>{f.net > 0 ? "+" : ""}{fmt(f.net)}</b></span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function BreakoutWatchTable({ date, rows, watchlist, onAdd, onRemove }) {
  const [input, setInput] = useState("");
  const bySym = Object.fromEntries((rows || []).map((r) => [r.symbol, r]));

  const submit = (e) => {
    e.preventDefault();
    const sym = input.trim().toUpperCase();
    if (sym) onAdd(sym);
    setInput("");
  };

  return (
    <div className="card">
      <h3>🚀 Breakout watch <span className="muted">— manually tracked, full details for {date || "—"}</span></h3>
      <form className="fav-add" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Add symbol e.g. RELIANCE"
          spellCheck={false}
        />
        <button type="submit">＋ Add</button>
      </form>
      {watchlist.length === 0 && (
        <p className="muted">No symbols on the breakout watchlist yet. Add one above.</p>
      )}
      {watchlist.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Cap</th><th className="r">Score</th>
              <th className="r">Close</th><th className="r">%Chg</th>
              <th className="r">Deliv%</th><th className="r">VolRatio</th><th></th>
            </tr>
          </thead>
          <tbody>
            {watchlist.map((sym) => {
              const r = bySym[sym];
              return (
                <tr key={sym} className={r ? "" : "pending"}>
                  <td className="sym">{sym}</td>
                  {r ? (
                    <>
                      <td><span className={`cap cap-${(r.cap || "").toLowerCase()}`}>{r.cap || "—"}</span></td>
                      <td className="r">{fmt(r.score)}</td>
                      <td className="r">{fmt(r.close_price)}</td>
                      <td className={`r ${pctClass(r.pct_change)}`}>{fmt(r.pct_change)}</td>
                      <td className="r">{fmt(r.deliv_per)}</td>
                      <td className="r">{fmt(r.vol_ratio)}</td>
                    </>
                  ) : (
                    <td colSpan={6} className="muted">no data for this day</td>
                  )}
                  <td className="r">
                    <button className="remove" title={`Remove ${sym}`} onClick={() => onRemove(sym)}>✕</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function DeliveryView({ date, favSet, onToggleFav, reloadToken, breakoutWatch, onAddBreakoutWatch, onRemoveBreakoutWatch }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!date) return;
    setData(null); setErr(null);
    api.delivery(date).then(setData).catch((e) => setErr(e.message));
  }, [date, reloadToken]);

  if (err) return <p className="err">No stored report for {date}. Run ingest first.</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const tprops = { favSet, onToggleFav };
  return (
    <>
      <FlowBanner flows={data.fii_dii} />
      <DeliveryTable title="① Delivery — Top BUY (by score)" rows={data.delivery.BUY} {...tprops} />
      <DeliveryTable title="② Delivery — Top SELL (by score)" rows={data.delivery.SELL} {...tprops} />
      <BulkTable title="③ Bulk Deals — Top BUY (by qty)" rows={data.bulk.BUY} />
      <BulkTable title="④ Bulk Deals — Top SELL (by qty)" rows={data.bulk.SELL} />
      <DeliveryTable title="⑤ Breakout — new high + volume surge" rows={data.breakout || []} {...tprops} />
      <BreakoutWatchTable date={date} rows={data.breakout_watch} watchlist={breakoutWatch}
                          onAdd={onAddBreakoutWatch} onRemove={onRemoveBreakoutWatch} />
    </>
  );
}

// ----- Favorites watchlist view ---------------------------------------- //
function FavoritesView({ date, favorites, onAdd, onRemove, reloadToken }) {
  const [data, setData] = useState(null);
  const [input, setInput] = useState("");
  useEffect(() => {
    if (!date) { setData({ favorites: [] }); return; }
    setData(null);
    api.delivery(date).then(setData).catch(() => setData({ favorites: [] }));
  }, [date, reloadToken]);

  // Look up each watchlist symbol's snapshot for this date (may be missing).
  const bySym = Object.fromEntries((data?.favorites || []).map((r) => [r.symbol, r]));

  const submit = (e) => {
    e.preventDefault();
    const sym = input.trim().toUpperCase();
    if (sym) onAdd(sym);
    setInput("");
  };

  return (
    <div className="card">
      <h3>★ Favorites <span className="muted">— your watchlist, full delivery details for {date || "—"}</span></h3>

      <form className="fav-add" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Add symbol e.g. RELIANCE"
          spellCheck={false}
        />
        <button type="submit">＋ Add</button>
      </form>

      {favorites.length === 0 && (
        <p className="muted">No favorites yet. Add a symbol above, or click ☆ next to any
          stock in the Delivery Report. Then hit <b>Fetch latest</b> to pull their details.</p>
      )}

      {favorites.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Cap</th><th className="r">Score</th>
              <th className="r">Close</th><th className="r">%Chg</th>
              <th className="r">Deliv%</th><th className="r">Deliv Qty</th><th className="r">Volume</th><th className="r">VolRatio</th>
              <th className="r">InstBuy</th><th className="r">InstSell</th><th></th>
            </tr>
          </thead>
          <tbody>
            {/* Driven by the live watchlist so a removed stock disappears at once. */}
            {favorites.map((sym) => {
              const r = bySym[sym];
              return (
                <tr key={sym} className={r ? "" : "pending"}>
                  <td className="sym">{sym}</td>
                  {r ? (
                    <>
                      <td><span className={`cap cap-${(r.cap || "").toLowerCase()}`}>{r.cap || "—"}</span></td>
                      <td className="r">{fmt(r.score)}</td>
                      <td className="r">{fmt(r.close_price)}</td>
                      <td className={`r ${pctClass(r.pct_change)}`}>{fmt(r.pct_change)}</td>
                      <td className="r">{fmt(r.deliv_per)}</td>
                      <td className="r">{fmtInt(r.deliv_qty)}</td>
                      <td className="r">{fmtInt(r.volume)}</td>
                      <td className="r">{fmt(r.vol_ratio)}</td>
                      <td className="r">{fmtInt(r.inst_buy)}</td>
                      <td className="r">{fmtInt(r.inst_sell)}</td>
                    </>
                  ) : (
                    <td colSpan={10} className="muted">no data for this day — click <b>Fetch latest</b> to pull details</td>
                  )}
                  <td className="r">
                    <button className="remove" title={`Remove ${sym}`} onClick={() => onRemove(sym)}>✕</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ----- Follow-up view --------------------------------------------------- //
function ScoreCard({ side, s }) {
  if (!s || s.n === 0) return null;
  return (
    <div className="scorecard">
      <span className="badge">{side}</span>
      <span>Hit rate <b>{fmt(s.hit_rate, 1)}%</b></span>
      <span>Avg return <b className={pctClass(s.avg_return_pct)}>{fmt(s.avg_return_pct)}%</b></span>
      <span className="muted">({s.n} picks)</span>
    </div>
  );
}

function FollowupView({ date, reloadToken }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!date) return;
    setData(null); setErr(null);
    api.followup(date).then(setData).catch((e) => setErr(e.message));
  }, [date, reloadToken]);

  if (err) return <p className="err">{err}</p>;
  if (!data) return <p className="muted">Loading live + EOD quotes…</p>;
  if (data.rows.length === 0) return <p className="muted">No picks stored for {date}.</p>;

  return (
    <div className="card">
      <h3>Pick performance since {date} — return to latest close + live move</h3>
      <div className="cards-row">
        <ScoreCard side="BUY" s={data.summary.BUY} />
        <ScoreCard side="SELL" s={data.summary.SELL} />
      </div>
      <table>
        <thead>
          <tr>
            <th>Side</th><th>Symbol</th><th>Cap</th>
            <th className="r">Pick Close</th><th className="r">Latest Close</th>
            <th className="r">Return %</th><th>Signal</th>
            <th className="r">Live Price</th><th className="r">Live Day %</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={`${r.side}-${r.symbol}`}>
              <td><span className={`badge ${r.side === "BUY" ? "buy" : "sell"}`}>{r.side}</span></td>
              <td className="sym">{r.symbol}</td>
              <td><span className={`cap cap-${(r.cap || "").toLowerCase()}`}>{r.cap || "—"}</span></td>
              <td className="r">{fmt(r.pick_close)}</td>
              <td className="r">{fmt(r.latest_close)}</td>
              <td className={`r ${pctClass(r.eod_return_pct)}`}>{fmt(r.eod_return_pct)}</td>
              <td>{r.signal_correct === null ? "—" : r.signal_correct ? "✓" : "✗"}</td>
              <td className="r">{fmt(r.live_price)}</td>
              <td className={`r ${pctClass(r.live_day_change_pct)}`}>{fmt(r.live_day_change_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ----- App shell -------------------------------------------------------- //
export default function App() {
  const [dates, setDates] = useState([]);
  const [date, setDate] = useState("");
  const [tab, setTab] = useState("delivery");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [favorites, setFavorites] = useState([]);
  const [breakoutWatch, setBreakoutWatch] = useState([]);
  const [reloadToken, setReloadToken] = useState(0);
  const favSet = new Set(favorites);

  const loadDates = () =>
    api.dates().then((ds) => {
      setDates(ds);
      setDate((cur) => cur || ds[0] || "");
    });

  useEffect(() => {
    loadDates();
    api.favorites().then(setFavorites).catch(() => {});
    api.breakoutWatch().then(setBreakoutWatch).catch(() => {});
  }, []);

  const addFav = (symbol) => api.addFavorite(symbol).then(setFavorites).catch(() => {});
  const removeFav = (symbol) => api.removeFavorite(symbol).then(setFavorites).catch(() => {});
  const toggleFav = (symbol) =>
    (favSet.has(symbol) ? api.removeFavorite(symbol) : api.addFavorite(symbol))
      .then(setFavorites)
      .catch(() => {});
  const addBreakoutWatch = (symbol) => api.addBreakoutWatch(symbol).then(setBreakoutWatch).catch(() => {});
  const removeBreakoutWatch = (symbol) => api.removeBreakoutWatch(symbol).then(setBreakoutWatch).catch(() => {});

  const runIngest = async () => {
    setBusy(true); setMsg("Fetching latest NSE data…");
    try {
      const r = await api.ingest();
      setMsg(`Stored ${r.date}: ${r.picks} picks, ${r.bulk_deals} bulk rows` +
        (r.favorites ? `, ${r.favorites} favorites` : "") +
        (r.breakout ? `, ${r.breakout} breakout picks` : "") + ".");
      await loadDates();
      setDate(r.date);
      setReloadToken((t) => t + 1);   // force views to refetch even if date is unchanged
    } catch (e) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>📈 Stock Analysis Portal</h1>
        <div className="controls">
          <label>
            Trading day:&nbsp;
            <select value={date} onChange={(e) => setDate(e.target.value)}>
              {dates.length === 0 && <option value="">— none —</option>}
              {dates.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <button onClick={runIngest} disabled={busy}>
            {busy ? "Working…" : "Fetch latest"}
          </button>
        </div>
      </header>

      {msg && <div className="msg">{msg}</div>}

      <nav className="tabs">
        <button className={tab === "delivery" ? "active" : ""} onClick={() => setTab("delivery")}>
          Delivery Report
        </button>
        <button className={tab === "followup" ? "active" : ""} onClick={() => setTab("followup")}>
          Pick Follow-up
        </button>
        <button className={tab === "favorites" ? "active" : ""} onClick={() => setTab("favorites")}>
          ★ Favorites{favorites.length ? ` (${favorites.length})` : ""}
        </button>
      </nav>

      <main>
        {tab === "favorites" ? (
          <FavoritesView date={date} favorites={favorites}
                         onAdd={addFav} onRemove={removeFav} reloadToken={reloadToken} />
        ) : !date ? (
          <p className="muted">No data yet — click “Fetch latest”.</p>
        ) : tab === "delivery" ? (
          <DeliveryView date={date} favSet={favSet} onToggleFav={toggleFav} reloadToken={reloadToken}
                        breakoutWatch={breakoutWatch} onAddBreakoutWatch={addBreakoutWatch}
                        onRemoveBreakoutWatch={removeBreakoutWatch} />
        ) : (
          <FollowupView date={date} reloadToken={reloadToken} />
        )}
      </main>

      <footer className="muted">
        Module 1 of {dates.length ? `${dates.length} stored day(s)` : "the portal"} ·
        data from NSE archives + Yahoo Finance
      </footer>
    </div>
  );
}
