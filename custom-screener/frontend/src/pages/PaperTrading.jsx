import React, { useEffect, useMemo, useState } from 'react';

// Paper-trading comparison panel: the three validated POSITIONAL presets run
// forward in parallel (paper2_* tables, marked nightly by systemd timers).
const fmtInr = (v) => {
  const a = Math.abs(v);
  if (a >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `₹${(v / 1e5).toFixed(2)}L`;
  return `₹${Math.round(v).toLocaleString('en-IN')}`;
};

function EquityOverlay({ books }) {
  const series = books.filter((b) => b.equityCurve.length >= 2);
  if (!series.length) {
    return <div className="text-sm text-slate-500 py-6 text-center">
      Equity curves will appear after the first few daily marks.</div>;
  }
  const W = 860, H = 260, L = 56, R = 14, T = 14, B = 30;
  const allDates = [...new Set(series.flatMap((b) => b.equityCurve.map((p) => p.d)))].sort();
  const x = (d) => L + ((W - L - R) * allDates.indexOf(d)) / Math.max(allDates.length - 1, 1);
  const vals = series.flatMap((b) => b.equityCurve.map((p) => p.equity));
  let y0 = Math.min(...vals), y1 = Math.max(...vals);
  if (y1 === y0) { y0 *= 0.98; y1 *= 1.02; }
  const y = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-3 overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[700px]">
        {[0.15, 0.5, 0.85].map((f, i) => {
          const v = y0 + (y1 - y0) * f;
          return (
            <g key={i}>
              <line x1={L} y1={y(v)} x2={W - R} y2={y(v)} stroke="#334155" strokeWidth="0.6" />
              <text x={L - 6} y={y(v) + 3} textAnchor="end" fontSize="10" fill="#64748b">{fmtInr(v)}</text>
            </g>
          );
        })}
        {series.map((b) => (
          <path key={b.book} fill="none" stroke={b.color} strokeWidth="1.8"
            d={b.equityCurve.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(p.d)} ${y(p.equity)}`).join(' ')} />
        ))}
        {[0, Math.floor((allDates.length - 1) / 2), allDates.length - 1]
          .filter((v, i, a) => a.indexOf(v) === i)
          .map((idx) => (
            <text key={idx} x={x(allDates[idx])} y={H - 8} fontSize="9" fill="#94a3b8" textAnchor="middle">
              {allDates[idx]}
            </text>
          ))}
      </svg>
      <div className="flex gap-4 mt-1 px-1">
        {series.map((b) => (
          <span key={b.book} className="text-[11px] text-slate-300 flex items-center gap-1.5">
            <span className="inline-block w-3 h-0.5" style={{ background: b.color }} />{b.label}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function PaperTrading() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [openBook, setOpenBook] = useState(null);

  useEffect(() => {
    let alive = true;
    fetch('/custom-screener/api/paper/summary')
      .then((r) => r.json())
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, []);

  if (error) return <div className="text-sm text-red-300 p-4">{error}</div>;
  if (!data) return <div className="text-sm text-slate-400 p-4 animate-pulse">Loading paper books…</div>;

  return (
    <div className="space-y-4">
      <div className="text-[13px] text-slate-400 max-w-3xl">
        Forward paper trading of all three preset configurations, seeded ₹4L each on{' '}
        {data.books[0]?.startedAt} — the only survivorship-free comparison possible.
        Marked nightly at 19:25 IST; rebalanced every 21 sessions; fills at next open
        with the full cost model. No orders are placed.
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {data.books.map((b) => (
          <div key={b.book} className="bg-slate-900/60 border border-slate-700 rounded-lg p-3.5"
            style={{ borderTopColor: b.color, borderTopWidth: 2 }}>
            <div className="text-xs font-semibold" style={{ color: b.color }}>⭐ {b.label}</div>
            <div className="text-xl font-bold text-slate-100 mt-1.5">{fmtInr(b.equity)}</div>
            <div className={`text-sm font-semibold ${b.returnPct >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
              {b.returnPct >= 0 ? '+' : ''}{b.returnPct}%
              <span className="text-slate-500 font-normal text-xs ml-2">DD {b.ddPct.toFixed(1)}%</span>
            </div>
            <div className="text-[11px] text-slate-400 mt-2 space-y-0.5">
              <div>{b.nOpen} open · {b.nClosed} closed{b.winRate != null ? ` · ${b.winRate}% wins` : ''}</div>
              <div>realized {fmtInr(b.realizedPnl)}{b.cashCredit > 0 ? ` · cash yield ${fmtInr(b.cashCredit)}` : ''}</div>
              <div>last rebalance: {b.lastRebalance || 'pending first fill'}</div>
            </div>
            <button onClick={() => setOpenBook(openBook === b.book ? null : b.book)}
              className="mt-2 px-2 py-1 text-[11px] rounded bg-slate-800 border border-slate-600 text-slate-300 hover:text-white">
              {openBook === b.book ? 'Hide' : 'Show'} positions ({b.nOpen})
            </button>
          </div>
        ))}
      </div>

      {openBook && (() => {
        const b = data.books.find((x) => x.book === openBook);
        if (!b) return null;
        return (
          <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-3 overflow-x-auto">
            <div className="text-xs uppercase tracking-wide mb-2" style={{ color: b.color }}>
              {b.label} — open positions</div>
            {!b.openPositions.length
              ? <div className="text-sm text-slate-500">No open positions yet — first fills land at the next session open after a rebalance.</div>
              : <table className="w-full text-[12px] min-w-[560px]">
                  <thead><tr className="text-left text-[10px] text-slate-500 uppercase">
                    <th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Entry date</th>
                    <th className="py-1 pr-3">Entry ₹</th><th className="py-1 pr-3">Qty</th>
                    <th className="py-1 pr-3">Rank</th><th className="py-1 pr-3">Slip bps</th>
                  </tr></thead>
                  <tbody>
                    {b.openPositions.map((p, i) => (
                      <tr key={i} className="border-t border-slate-800 text-slate-200">
                        <td className="py-1 pr-3 font-medium">{p.symbol}</td>
                        <td className="py-1 pr-3 text-slate-400">{p.entryDate}</td>
                        <td className="py-1 pr-3">{p.entryPrice.toFixed(2)}</td>
                        <td className="py-1 pr-3">{p.qty}</td>
                        <td className="py-1 pr-3 text-sky-300">{p.rank ? `#${p.rank}` : '—'}</td>
                        <td className="py-1 pr-3 text-slate-400">{p.slippageBps != null ? p.slippageBps.toFixed(0) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
          </div>
        );
      })()}

      <EquityOverlay books={data.books} />
    </div>
  );
}
