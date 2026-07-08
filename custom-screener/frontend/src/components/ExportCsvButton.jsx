import React from 'react';

const FIELDS = [
  'symbol', 'close', 'ema10', 'ema21', 'sma50', 'sma200', 'distSma200Pct',
  'price52wHigh', 'price52wLow', 'dist52wHighPct', 'dist52wLowPct',
  'pctChg1d', 'pctChg1m', 'pctChg3m', 'pctChg6m', 'pctChg1y',
  'turnover1mAvgCr', 'barsAvailable',
];

export default function ExportCsvButton({ rows, date }) {
  const download = () => {
    if (!rows?.length) return;
    const header = FIELDS.join(',');
    const lines = rows.map((r) => FIELDS.map((f) => (r[f] ?? '')).join(','));
    const csv = [header, ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `custom_screener_${date || 'latest'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button onClick={download} disabled={!rows?.length}
      className="px-3 py-1.5 text-sm rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-200">
      Export CSV
    </button>
  );
}
