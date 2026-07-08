// API base — matches the nginx dedicated path. In dev, Vite proxies these.
const BASE = '/custom-screener/api';

async function req(path, opts) {
  const res = await fetch(`${BASE}${path}`, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

export const getSnapshot = (date) =>
  req(`/market-snapshot${date ? `?date=${date}` : ''}`);

export const runFilter = (payload) =>
  req('/filter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

export const getHistorical = (symbol, fromDate, toDate) =>
  req(`/historical?symbol=${symbol}&fromDate=${fromDate}&toDate=${toDate}`);

// Chart SVG from the existing Market Data API (data-only dependency).
export const chartUrl = (symbol, type = 'daily', theme = 'dark') =>
  `/api/v1/charts/${type}?symbol=${encodeURIComponent(symbol)}&theme=${theme}`;
