// Thin API client. All calls go through the Vite proxy (/api -> :8000 in dev),
// or hit the same origin when served by FastAPI in production.
async function get(path) {
  const res = await fetch(`/api${path}`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function req(path, method) {
  const res = await fetch(`/api${path}`, { method });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  dates: () => get("/dates"),
  delivery: (date) => get(`/delivery/${date}`),
  followup: (date) => get(`/followup/${date}`),
  ingest: (date) => req(`/ingest${date ? `?date=${date}` : ""}`, "POST"),
  favorites: () => get("/favorites"),
  addFavorite: (symbol) => req(`/favorites/${encodeURIComponent(symbol)}`, "POST"),
  removeFavorite: (symbol) => req(`/favorites/${encodeURIComponent(symbol)}`, "DELETE"),
  breakoutWatch: () => get("/breakout-watch"),
  addBreakoutWatch: (symbol) => req(`/breakout-watch/${encodeURIComponent(symbol)}`, "POST"),
  removeBreakoutWatch: (symbol) => req(`/breakout-watch/${encodeURIComponent(symbol)}`, "DELETE"),
};
