import React from 'react';

// Dropdown option sets. "All" (value null) is default.
const TURNOVER = [['All', null], ['> ₹1 Cr', 1], ['> ₹5 Cr', 5], ['> ₹10 Cr', 10], ['> ₹50 Cr', 50], ['> ₹100 Cr', 100]];
const PRICE = [['All', null], ['> ₹50', 50], ['> ₹100', 100], ['> ₹200', 200], ['> ₹500', 500], ['> ₹1000', 1000]];
const DIR = [['All', 'any'], ['Above', 'above'], ['Below', 'below']];
const PCT = [['All', null], ['> +4.5%', { min: 4.5 }], ['> +10%', { min: 10 }], ['> +20%', { min: 20 }],
  ['> +50%', { min: 50 }], ['< -5%', { max: -5 }], ['< -10%', { max: -10 }]];

// 52W high: within X% (near) OR more than X% below. Encoded as {k, v}.
const HIGH_52W = [['All', null],
  ['Within 5%', { k: 'within52wHighPct', v: 5 }], ['Within 10%', { k: 'within52wHighPct', v: 10 }],
  ['Within 15%', { k: 'within52wHighPct', v: 15 }], ['Within 20%', { k: 'within52wHighPct', v: 20 }],
  ['> 20% below', { k: 'below52wHighPct', v: 20 }], ['> 40% below', { k: 'below52wHighPct', v: 40 }]];
// 52W low: within X% (near) OR more than X% above.
const LOW_52W = [['All', null],
  ['Within 5%', { k: 'within52wLowPct', v: 5 }], ['Within 10%', { k: 'within52wLowPct', v: 10 }],
  ['Within 15%', { k: 'within52wLowPct', v: 15 }],
  ['> 25% above', { k: 'above52wLowPct', v: 25 }], ['> 50% above', { k: 'above52wLowPct', v: 50 }],
  ['> 100% above', { k: 'above52wLowPct', v: 100 }]];

const BASE_RANGE = [['All', null], ['< 8%', 8], ['< 12%', 12], ['< 15%', 15], ['< 20%', 20]];
const NEAR_HIGH20 = [['All', null], ['Within 2%', 2], ['Within 5%', 5], ['Within 10%', 10]];
const VOL_EXP = [['All', null], ['> 1×', 1], ['> 1.5×', 1.5], ['> 2×', 2], ['> 3×', 3]];
const VOL_DRY = [['All', null], ['≤ 1.0', 1.0], ['≤ 1.3', 1.3]];
const UPMOVE = [['All', null], ['≥ 15%', 15], ['≥ 25%', 25], ['≥ 50%', 50]];
const GIVEBACK = [['All', null], ['≤ 30%', 30], ['≤ 50%', 50]];
const ATR = [['All', null], ['< 3%', { max: 3 }], ['< 5%', { max: 5 }], ['> 5%', { min: 5 }]];
const ALIGN = [['All', null], ['Close>EMA50>SMA200', true]];
const IFP = [['All', null], ['≥ 0.20', 0.2], ['≥ 0.25', 0.25], ['≥ 0.30', 0.3], ['≥ 0.40', 0.4]];
const UDVR = [['All', null], ['≥ 1.0', 1.0], ['≥ 1.2', 1.2], ['≥ 1.5', 1.5], ['≥ 2.0', 2.0]];
const OBV = [['All', null], ['Positive', true]];

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
  // 52W dropdowns write one of two keys; clear both then set the chosen one.
  const set52w = (pair, keys) => setFilters((f) => {
    const next = { ...f };
    keys.forEach((k) => delete next[k]);
    if (pair) next[pair.k] = pair.v;
    return next;
  });
  const cur52w = (keys) => {
    for (const k of keys) if (filters[k] != null) return { k, v: filters[k] };
    return null;
  };

  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4 space-y-4">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Trend &amp; Liquidity</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <Sel label="Min Turnover" options={TURNOVER} value={filters.minTurnoverCr ?? null} onChange={(v) => set('minTurnoverCr', v)} />
          <Sel label="MA Alignment" options={ALIGN} value={filters.maAligned ?? null} onChange={(v) => set('maAligned', v)} />
          <Sel label="SMA 200 Dir" options={DIR} value={filters.sma200 ?? 'any'} onChange={(v) => set('sma200', v)} />
          <Sel label="EMA 50 Dir" options={DIR} value={filters.ema50 ?? 'any'} onChange={(v) => set('ema50', v)} />
          <Sel label="SMA 50 Dir" options={DIR} value={filters.sma50 ?? 'any'} onChange={(v) => set('sma50', v)} />
          <Sel label="EMA 10 >" options={PRICE} value={filters.ema10Above ?? null} onChange={(v) => set('ema10Above', v)} />
        </div>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">52-Week &amp; Base</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <Sel label="52W High" options={HIGH_52W} value={cur52w(['within52wHighPct', 'below52wHighPct'])}
            onChange={(v) => set52w(v, ['within52wHighPct', 'below52wHighPct'])} />
          <Sel label="52W Low" options={LOW_52W} value={cur52w(['within52wLowPct', 'above52wLowPct'])}
            onChange={(v) => set52w(v, ['within52wLowPct', 'above52wLowPct'])} />
          <Sel label="Base tightness (20d)" options={BASE_RANGE} value={filters.baseRange20dMaxPct ?? null} onChange={(v) => set('baseRange20dMaxPct', v)} />
          <Sel label="Near 20d high" options={NEAR_HIGH20} value={filters.within20dHighPct ?? null} onChange={(v) => set('within20dHighPct', v)} />
          <Sel label="Prior upmove" options={UPMOVE} value={filters.priorUpmoveMinPct ?? null} onChange={(v) => set('priorUpmoveMinPct', v)} />
          <Sel label="Giveback" options={GIVEBACK} value={filters.givebackMaxPct ?? null} onChange={(v) => set('givebackMaxPct', v)} />
        </div>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Volume, Volatility &amp; Momentum</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <Sel label="Vol expansion (1d)" options={VOL_EXP} value={filters.volRatioMin ?? null} onChange={(v) => set('volRatioMin', v)} />
          <Sel label="Vol dry-up" options={VOL_DRY} value={filters.volDryupMaxRatio ?? null} onChange={(v) => set('volDryupMaxRatio', v)} />
          <Sel label="ATR %" options={ATR} value={filters.atrPct ?? null} onChange={(v) => set('atrPct', v)} />
          <Sel label="% Chg 1D" options={PCT} value={filters.pctChg1d ?? null} onChange={(v) => set('pctChg1d', v)} />
          <Sel label="% Chg 1M" options={PCT} value={filters.pctChg1m ?? null} onChange={(v) => set('pctChg1m', v)} />
          <Sel label="% Chg 3M" options={PCT} value={filters.pctChg3m ?? null} onChange={(v) => set('pctChg3m', v)} />
          <Sel label="% Chg 1Y" options={PCT} value={filters.pctChg1y ?? null} onChange={(v) => set('pctChg1y', v)} />
        </div>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Institutional Footprint (default 100d/1.5×/0.60 — tune below results)</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <Sel label="IFP score" options={IFP} value={filters.ifpScoreMin ?? null} onChange={(v) => set('ifpScoreMin', v)} />
          <Sel label="Up/Down Vol (50d)" options={UDVR} value={filters.updownVolRatioMin ?? null} onChange={(v) => set('updownVolRatioMin', v)} />
          <Sel label="OBV slope (50d)" options={OBV} value={filters.obvSlopePositive ?? null} onChange={(v) => set('obvSlopePositive', v)} />
        </div>
      </div>

      <div className="flex items-center justify-between">
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
