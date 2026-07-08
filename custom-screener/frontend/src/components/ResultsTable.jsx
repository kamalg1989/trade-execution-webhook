import React, { useMemo, useState } from 'react';

const COLS = [
  ['symbol', 'Symbol', (r) => r.symbol],
  ['close', 'Close', (r) => fmt(r.close)],
  ['dist52wHighPct', '52WH %', (r) => pct(r.dist52wHighPct)],
  ['distSma200Pct', 'SMA200 %', (r) => pct(r.distSma200Pct)],
  ['baseRange20dPct', 'Base20d', (r) => (r.baseRange20dPct != null ? `${fmt(r.baseRange20dPct)}%` : '—')],
  ['volRatio1d', 'Vol×', (r) => (r.volRatio1d != null ? `${fmt(r.volRatio1d)}×` : '—')],
  ['pctChg1m', '%1M', (r) => pct(r.pctChg1m)],
  ['pctChg3m', '%3M', (r) => pct(r.pctChg3m)],
  ['pctChg1y', '%1Y', (r) => pct(r.pctChg1y)],
  ['turnover1mAvgCr', 'Turnover', (r) => (r.turnover1mAvgCr != null ? `${fmt(r.turnover1mAvgCr)}Cr` : '—')],
];

const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2));
const pct = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`);
const pctClass = (v) => (v == null ? 'text-slate-400' : v >= 0 ? 'text-emerald-400' : 'text-red-400');

export default function ResultsTable({ rows, onPick }) {
  const [sortBy, setSortBy] = useState('pctChg1m');
  const [asc, setAsc] = useState(false);

  const sorted = useMemo(() => {
    const arr = [...rows];
    arr.sort((a, b) => {
      const x = a[sortBy], y = b[sortBy];
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === 'string') return asc ? x.localeCompare(y) : y.localeCompare(x);
      return asc ? x - y : y - x;
    });
    return arr;
  }, [rows, sortBy, asc]);

  const clickHead = (key) => {
    if (key === sortBy) setAsc((a) => !a);
    else { setSortBy(key); setAsc(false); }
  };

  return (
    <div className="overflow-auto max-h-[60vh] rounded-lg border border-slate-700">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-slate-800 text-slate-300">
          <tr>
            {COLS.map(([key, label]) => (
              <th key={key} onClick={() => clickHead(key)}
                className="px-3 py-2 text-left cursor-pointer select-none whitespace-nowrap hover:text-white">
                {label}{sortBy === key ? (asc ? ' ▲' : ' ▼') : ''}
              </th>
            ))}
            <th className="px-3 py-2 text-left whitespace-nowrap">Chart</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.symbol} onClick={() => onPick(r)}
              className="border-t border-slate-800 hover:bg-slate-800/60 cursor-pointer">
              {COLS.map(([key, , render]) => (
                <td key={key} className={`px-3 py-1.5 whitespace-nowrap ${key.startsWith('pct') || key.includes('Pct') ? pctClass(r[key]) : 'text-slate-200'}`}>
                  {render(r)}
                </td>
              ))}
              <td className="px-3 py-1.5 whitespace-nowrap">
                <button
                  onClick={(e) => { e.stopPropagation(); onPick(r); }}
                  className="px-2 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold">
                  📈 Chart
                </button>
              </td>
            </tr>
          ))}
          {sorted.length === 0 && (
            <tr><td colSpan={COLS.length + 1} className="px-3 py-6 text-center text-slate-500">No stocks match these filters.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
