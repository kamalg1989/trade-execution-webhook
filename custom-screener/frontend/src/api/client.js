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

// Tunable IFP recompute over a filtered subset.
export const scoreIfp = (payload) =>
  req('/ifp', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

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

// Sector / index metadata for the universe filters.
export const getSectors = () => req('/meta/sectors');

// --- AI visual analysis (ai_analysis module) ---
export const aiAnalyze = (payload) =>
  req('/ai-analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

export const aiFeedback = (payload) =>
  req('/ai-analyze/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

export const aiOutcomesSummary = () => req('/ai-analyze/outcomes/summary');

export const aiAftermath = (symbol, date) =>
  req(`/ai-analyze/aftermath/${encodeURIComponent(symbol)}?date=${date}`);

// Backend returns chart URLs as /api/ai-analyze/charts/x.png — prefix the
// nginx path (BASE already ends with /api).
export const aiChartSrc = (url) => (url ? url.replace(/^\/api/, BASE) : null);
