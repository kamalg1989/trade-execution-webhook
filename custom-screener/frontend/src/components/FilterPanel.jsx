import React, { useState } from 'react';

// Dropdown option sets. "All" (value null) is default.
const TURNOVER = [['All', null], ['> ₹1 Cr', 1], ['> ₹5 Cr', 5], ['> ₹10 Cr', 10], ['> ₹25 Cr', 25], ['> ₹50 Cr', 50], ['> ₹100 Cr', 100], ['> ₹250 Cr', 250]];
const PRICE = [['All', null], ['> ₹20', 20], ['> ₹50', 50], ['> ₹100', 100], ['> ₹200', 200], ['> ₹500', 500], ['> ₹1000', 1000]];
const TREND = [
  ['Any', 'any'],
  ['Uptrend — C>SMA200', 'uptrend'],
  ['Confirmed — C>SMA50>200', 'confirmed'],
  ['Momentum — C>EMA21>50>200', 'momentum'],
  ['Power — C>10>21>50>200', 'power'],
];
const DIR = [['All', 'any'], ['Above', 'above'], ['Below', 'below']];
const PCT = [['All', null], ['> +4.5%', { min: 4.5 }], ['> +10%', { min: 10 }], ['> +20%', { min: 20 }],
  ['> +50%', { min: 50 }], ['< -5%', { max: -5 }], ['< -10%', { max: -10 }]];

const HIGH_52W = [['All', null],
  ['Within 5%', { k: 'within52wHighPct', v: 5 }], ['Within 10%', { k: 'within52wHighPct', v: 10 }],
  ['Within 15%', { k: 'within52wHighPct', v: 15 }], ['Within 20%', { k: 'within52wHighPct', v: 20 }],
  ['Within 25%', { k: 'within52wHighPct', v: 25 }],
  ['> 20% below', { k: 'below52wHighPct', v: 20 }], ['> 40% below', { k: 'below52wHighPct', v: 40 }]];
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

// Filter purpose notes, written for the base-and-bounce setup.
const TIPS = {
  turnover: 'Liquidity gate: 1-month avg of price × volume (₹Cr). Ensures clean entries/exits and reliable stop fills. ₹10Cr+ minimum for swing; higher = safer.',
  minPrice: 'Excludes penny stocks — wide spreads, manipulation-prone, stops slip badly. ₹50+ keeps the universe tradeable.',
  trend: 'One control for the full MA stack; each level includes the previous. Uptrend = above 200SMA (long-term OK). Confirmed = 50>200 (institutions committed). Momentum = riding EMA21 (swing zone). Power = perfect stack, strongest names.',
  high52: 'Bases that matter form near highs. Within 10–15% = basing zone for breakout entries. Deep below the high = broken structure — not our setup.',
  sme: 'NSE EMERGE (SME) stocks trade in fixed lots with thin order books — position sizing is constrained and stop-losses are unreliable. Keep ON unless you specifically trade SME.',
  align: 'Legacy alignment check (Close>EMA50>SMA200). Superseded by the Trend ladder — use that instead.',
  dir: 'Fine-grained above/below vs a single MA. Only needed when the Trend ladder is too coarse.',
  low52: 'Distance from the 52W low. Bottom-fishing filter — rarely useful for base-and-bounce.',
  baseTight: '20-day price range as % of price. Tight (<15%) = supply absorbed, constructive base; wild ranges = not ready.',
  near20: 'Trigger proximity: close is within X% of the 20d high — breakout could fire any day. Good for building the daily watchlist.',
  upmove: "The 'bounce' credential: a strong prior advance proves institutional demand before the base formed. Deck rule: ≥ 50% for swing candidates.",
  giveback: 'Constructive bases give back < 30% of the prior advance (deck rule). More = weak hands, suspect base.',
  volExp: 'Entry-day trigger: today volume vs 20d avg. > 1.5× on a breakout = institutional participation confirming the move.',
  volDry: 'Base volume ÷ prior-advance volume. ≤ 1.0 = sellers exhausted inside the base — the quiet before the breakout.',
  atr: 'Daily volatility as % of price. Sloppy (>5%) bases fail more; calm ones give tighter stops.',
  pct: 'Simple momentum window. Mostly informational — Prior upmove is the deck-native momentum filter.',
  ifp: 'Institutional Footprint: fraction of recent days showing the accumulation signature (high-volume up days closing strong + quiet pullbacks). Institutions cannot hide volume.',
  udvr: 'Up-day volume ÷ down-day volume (50d). > 1.2 = buying pressure dominates. Component of IFP.',
  obv: 'On-balance volume slope (50d). Positive = net accumulation. Component of IFP.',
};

function Tip({ text }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <span className="relative inline-block"
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" tabIndex={-1} onClick={() => setOpen(!open)}
        className="w-4 h-4 rounded-full bg-slate-700 text-slate-300 text-[10px] leading-4 text-center cursor-help align-middle">i</button>
      {open && (
        <span className="absolute z-30 left-1/2 -translate-x-1/2 bottom-6 w-64 bg-slate-800 border border-slate-600 rounded-lg p-2.5 text-[11px] leading-relaxed text-slate-200 normal-case tracking-normal shadow-xl pointer-events-none">
          {text}
        </span>
      )}
    </span>
  );
}

function Sel({ label, tip, options, value, onChange }) {
  return (
    <label className="flex flex-col text-xs text-slate-300 gap-1">
      <span className="flex items-center gap-1.5">{label} <Tip text={tip} /></span>
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
  const [showAdvanced, setShowAdvanced] = useState(false);
  const set = (k, v) => setFilters((f) => ({ ...f, [k]: v }));
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
      {/* PRIMARY TIER — universe definition */}
      <div>
        <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Primary — define your universe</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <Sel label="Avg daily turnover" tip={TIPS.turnover} options={TURNOVER}
            value={filters.minTurnoverCr ?? null} onChange={(v) => set('minTurnoverCr', v)} />
          <Sel label="Min price" tip={TIPS.minPrice} options={PRICE}
            value={filters.minPrice ?? null} onChange={(v) => set('minPrice', v)} />
          <Sel label="Trend ladder" tip={TIPS.trend} options={TREND}
            value={filters.trendLadder ?? 'any'} onChange={(v) => set('trendLadder', v)} />
          <Sel label="52W high proximity" tip={TIPS.high52} options={HIGH_52W}
            value={cur52w(['within52wHighPct', 'below52wHighPct'])}
            onChange={(v) => set52w(v, ['within52wHighPct', 'below52wHighPct'])} />
        </div>
        <div className="flex flex-wrap items-center gap-4 mt-3">
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={filters.excludeSme !== false}
              onChange={(e) => set('excludeSme', e.target.checked)} />
            Exclude SME / lot-traded <Tip text={TIPS.sme} />
          </label>
          <span className="text-[11px] text-slate-600">Universe · Sector · Market cap filters coming next (index membership feed)</span>
        </div>
      </div>

      {/* ADVANCED — setup-specific filters */}
      <button onClick={() => setShowAdvanced(!showAdvanced)}
        className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1">
        {showAdvanced ? '▾' : '▸'} Advanced filters {showAdvanced ? '' : '(base quality, volume, IFP, momentum)'}
      </button>

      {showAdvanced && (
        <>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Base &amp; Structure</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              <Sel label="Base tightness (20d)" tip={TIPS.baseTight} options={BASE_RANGE} value={filters.baseRange20dMaxPct ?? null} onChange={(v) => set('baseRange20dMaxPct', v)} />
              <Sel label="Near 20d high" tip={TIPS.near20} options={NEAR_HIGH20} value={filters.within20dHighPct ?? null} onChange={(v) => set('within20dHighPct', v)} />
              <Sel label="Prior upmove" tip={TIPS.upmove} options={UPMOVE} value={filters.priorUpmoveMinPct ?? null} onChange={(v) => set('priorUpmoveMinPct', v)} />
              <Sel label="Giveback" tip={TIPS.giveback} options={GIVEBACK} value={filters.givebackMaxPct ?? null} onChange={(v) => set('givebackMaxPct', v)} />
              <Sel label="52W low" tip={TIPS.low52} options={LOW_52W} value={cur52w(['within52wLowPct', 'above52wLowPct'])}
                onChange={(v) => set52w(v, ['within52wLowPct', 'above52wLowPct'])} />
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Volume &amp; Volatility</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              <Sel label="Vol expansion (1d)" tip={TIPS.volExp} options={VOL_EXP} value={filters.volRatioMin ?? null} onChange={(v) => set('volRatioMin', v)} />
              <Sel label="Vol dry-up" tip={TIPS.volDry} options={VOL_DRY} value={filters.volDryupMaxRatio ?? null} onChange={(v) => set('volDryupMaxRatio', v)} />
              <Sel label="ATR %" tip={TIPS.atr} options={ATR} value={filters.atrPct ?? null} onChange={(v) => set('atrPct', v)} />
              <Sel label="% Chg 1M" tip={TIPS.pct} options={PCT} value={filters.pctChg1m ?? null} onChange={(v) => set('pctChg1m', v)} />
              <Sel label="% Chg 3M" tip={TIPS.pct} options={PCT} value={filters.pctChg3m ?? null} onChange={(v) => set('pctChg3m', v)} />
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Institutional Footprint (default 100d/1.5×/0.60 — tune below results)</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              <Sel label="IFP score" tip={TIPS.ifp} options={IFP} value={filters.ifpScoreMin ?? null} onChange={(v) => set('ifpScoreMin', v)} />
              <Sel label="Up/Down Vol (50d)" tip={TIPS.udvr} options={UDVR} value={filters.updownVolRatioMin ?? null} onChange={(v) => set('updownVolRatioMin', v)} />
              <Sel label="OBV slope (50d)" tip={TIPS.obv} options={OBV} value={filters.obvSlopePositive ?? null} onChange={(v) => set('obvSlopePositive', v)} />
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Legacy MA controls (superseded by Trend ladder)</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              <Sel label="MA Alignment" tip={TIPS.align} options={ALIGN} value={filters.maAligned ?? null} onChange={(v) => set('maAligned', v)} />
              <Sel label="SMA 200 Dir" tip={TIPS.dir} options={DIR} value={filters.sma200 ?? 'any'} onChange={(v) => set('sma200', v)} />
              <Sel label="EMA 50 Dir" tip={TIPS.dir} options={DIR} value={filters.ema50 ?? 'any'} onChange={(v) => set('ema50', v)} />
              <Sel label="SMA 50 Dir" tip={TIPS.dir} options={DIR} value={filters.sma50 ?? 'any'} onChange={(v) => set('sma50', v)} />
            </div>
          </div>
        </>
      )}

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
