import React from 'react';

// Dropdown option sets. "All" (value null) is default.
const TURNOVER = [['All', null], ['> ₹1 Cr', 1], ['> ₹5 Cr', 5], ['> ₹10 Cr', 10], ['> ₹50 Cr', 50], ['> ₹100 Cr', 100]];
const PRICE = [['All', null], ['> ₹50', 50], ['> ₹100', 100], ['> ₹200', 200], ['> ₹500', 500], ['> ₹1000', 1000]];
const DIR = [['All', 'any'], ['Above', 'above'], ['Below', 'below']];
const HIGH_PROX = [['All', null], ['Within 5%', 5], ['Within 10%', 10], ['Within 15%', 15], ['Within 20%', 20]];
const LOW_PROX = [['All', null], ['Within 5%', 5], ['Within 10%', 10], ['Within 15%', 15]];
const PCT = [['All', null], ['> +4.5%', { min: 4.5 }], ['> +10%', { min: 10 }], ['> +20%', { min: 20 }],
  ['> +50%', { min: 50 }], ['< -5%', { max: -5 }], ['< -10%', { max: -10 }]];

function Sel({ label, options, value, onChange }) {
  return (
    <label className="flex flex-col text-xs text-slate-300 gap-1">
      <span>{label}</span>
      <select
        className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100"
        value={JSON.stringify(value)}
        onChange={(e) => onChange(JSON.parse(e.target.value))}
      >
        {options.map(([lbl, val], i) => (
          <option key={i} value={JSON.stringify(val)}>{lbl}</option>
        ))}
      </select>
    </label>
  );
}

export default function FilterPanel({ filters, setFilters, includeInsufficient, setIncludeInsufficient, onApply, onReset, loading }) {
  const set = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <Sel label="Min Turnover" options={TURNOVER} value={filters.minTurnoverCr ?? null} onChange={(v) => set('minTurnoverCr', v)} />
        <Sel label="SMA 200 Dir" options={DIR} value={filters.sma200 ?? 'any'} onChange={(v) => set('sma200', v)} />
        <Sel label="SMA 50 Dir" options={DIR} value={filters.sma50 ?? 'any'} onChange={(v) => set('sma50', v)} />
        <Sel label="EMA 10 >" options={PRICE} value={filters.ema10Above ?? null} onChange={(v) => set('ema10Above', v)} />
        <Sel label="52W High Prox" options={HIGH_PROX} value={filters.within52wHighPct ?? null} onChange={(v) => set('within52wHighPct', v)} />
        <Sel label="52W Low Prox" options={LOW_PROX} value={filters.within52wLowPct ?? null} onChange={(v) => set('within52wLowPct', v)} />
        <Sel label="% Chg 1D" options={PCT} value={filters.pctChg1d ?? null} onChange={(v) => set('pctChg1d', v)} />
        <Sel label="% Chg 1M" options={PCT} value={filters.pctChg1m ?? null} onChange={(v) => set('pctChg1m', v)} />
        <Sel label="% Chg 3M" options={PCT} value={filters.pctChg3m ?? null} onChange={(v) => set('pctChg3m', v)} />
        <Sel label="% Chg 1Y" options={PCT} value={filters.pctChg1y ?? null} onChange={(v) => set('pctChg1y', v)} />
      </div>
      <div className="flex items-center justify-between mt-4">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={includeInsufficient} onChange={(e) => setIncludeInsufficient(e.target.checked)} />
          include &lt;200-bar symbols
        </label>
        <div className="flex gap-2">
          <button onClick={onReset} className="px-3 py-1.5 text-sm rounded bg-slate-700 hover:bg-slate-600 text-slate-200">Reset</button>
          <button onClick={onApply} disabled={loading}
            className="px-4 py-1.5 text-sm rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-semibold">
            {loading ? 'Applying…' : 'Apply Filters'}
          </button>
        </div>
      </div>
    </div>
  );
}
