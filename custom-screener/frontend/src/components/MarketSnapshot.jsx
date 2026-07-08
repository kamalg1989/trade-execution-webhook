import React from 'react';

const REGIME_COLOR = {
  'Strong Uptrend': 'text-emerald-400',
  'Moderate Uptrend': 'text-green-400',
  'Consolidation': 'text-yellow-300',
  'Correction': 'text-orange-400',
  'Strong Correction': 'text-red-400',
};

function Stat({ label, value }) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold text-slate-100">{value}</div>
      <div className="text-[10px] text-slate-400 uppercase tracking-wide">{label}</div>
    </div>
  );
}

export default function MarketSnapshot({ snap }) {
  if (!snap) return null;
  const c = snap.counts || {};
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="text-sm text-slate-300">
          Market Snapshot · <span className="text-slate-400">{snap.snapshotDate}</span>
        </div>
        <div className="text-sm">
          Regime: <span className={`font-bold ${REGIME_COLOR[snap.regime] || 'text-slate-200'}`}>{snap.regime}</span>
          <span className="text-slate-500 mx-2">|</span>
          Trend <b className="text-slate-200">{snap.trendScore?.toFixed(2)}</b>
          <span className="text-slate-500 mx-2">|</span>
          Breadth <b className="text-slate-200">{snap.breadthScore?.toFixed(2)}</b>
        </div>
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
        <Stat label=">200 SMA" value={c.above200sma} />
        <Stat label=">50 SMA" value={c.above50sma} />
        <Stat label="New Highs" value={c.newHigh} />
        <Stat label="New Lows" value={c.newLow} />
        <Stat label="≤15% of 52WH" value={c.within15pct52wHigh} />
        <Stat label="≤15% of 52WL" value={c.within15pct52wLow} />
      </div>
      <div className="text-xs text-slate-500 mt-2">{snap.message}</div>
    </div>
  );
}
