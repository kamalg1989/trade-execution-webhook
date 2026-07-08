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

// On-demand compute for a selected date (manual "fetch data" button).
export const computeDate = (date) =>
  req(`/compute-date?date=${date}`, { method: 'POST' });

export const computeStatus = (date) =>
  req(`/compute-status?date=${date}`);

// Chart SVG from the existing Market Data API (same charts used by the dashboard).
// /api/v1/charts requires BOTH from_date and to_date.
export const chartUrl = (symbol, type = 'daily', theme = 'dark', fromDate, toDate) => {
  const today = new Date().toISOString().slice(0, 10);
  return `/api/v1/charts/${type}?symbol=${encodeURIComponent(symbol)}&theme=${theme}` +
    (fromDate ? `&from_date=${fromDate}` : '') +
    `&to_date=${toDate || today}`;
};
