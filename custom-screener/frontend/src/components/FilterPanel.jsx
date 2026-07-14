import React, { useEffect, useState } from 'react';
import { getSectors } from '../api/client.js';

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
const UNIVERSE = [
  ['All NSE', null],
  ['Nifty 50', 'NIFTY50'], ['Nifty 100', 'NIFTY100'], ['Nifty 200', 'NIFTY200'],
  ['Nifty 500', 'NIFTY500'], ['Midcap 150', 'MIDCAP150'],
  ['Smallcap 250', 'SMALLCAP250'], ['Microcap 250', 'MICROCAP250'],
];
const MCAPS = [['large', 'Large'], ['mid', 'Mid'], ['small', 'Small'], ['micro', 'Micro']];

const HIGH_52W = [['All', null],
  ['Within 5%', { k: 'within52wHighPct', v: 5 }], ['Within 10%', { k: 'within52wHighPct', v: 10 }],
  ['Within 15%', { k: 'within52wHighPct', v: 15 }], ['Within 20%', { k: 'within52wHighPct', v: 20 }],
  ['Within 25%', { k: 'within52wHighPct', v: 25 }],
  ['> 20% below', { k: 'below52wHighPct', v: 20 }], ['> 40% below', { k: 'below52wHighPct', v: 40 }]];

const BASE_RANGE = [['All', null], ['< 8%', 8], ['< 12%', 12], ['< 15%', 15], ['< 20%', 20]];
const NEAR_HIGH20 = [['All', null], ['Within 2%', 2], ['Within 5%', 5], ['Within 10%', 10]];
const VOL_EXP = [['All', null], ['> 1×', 1], ['> 1.5×', 1.5], ['> 2×', 2], ['> 3×', 3]];
const VOL_DRY = [['All', null], ['≤ 1.0', 1.0], ['≤ 1.3', 1.3]];
const UPMOVE = [['All', null], ['≥ 15%', 15], ['≥ 25%', 25], ['≥ 50%', 50]];
const GIVEBACK = [['All', null], ['≤ 30%', 30], ['≤ 50%', 50]];
const ATR = [['All', null], ['< 3%', { max: 3 }], ['< 5%', { max: 5 }], ['> 5%', { min: 5 }]];
const IFP = [['All', null], ['≥ 0.20', 0.2], ['≥ 0.25', 0.25], ['≥ 0.30', 0.3], ['≥ 0.40', 0.4]];

// Filter purpose notes, written for the base-and-bounce setup.
const TIPS = {
  turnover: 'Liquidity gate: 1-month avg of price × volume (₹Cr). Ensures clean entries/exits and reliable stop fills. ₹10Cr+ minimum for swing; higher = safer.',
  minPrice: 'Excludes penny stocks — wide spreads, manipulation-prone, stops slip badly. ₹50+ keeps the universe tradeable.',
  trend: 'One control for the full MA stack; each level includes the previous. Uptrend = above 200SMA (long-term OK). Confirmed = 50>200 (institutions committed). Momentum = riding EMA21 (swing zone). Power = perfect stack, strongest names.',
  high52: 'Bases that matter form near highs. Within 10–15% = basing zone for breakout entries. Deep below the high = broken structure — not our setup.',
  sme: 'NSE EMERGE (SME) stocks trade in fixed lots with thin order books — position sizing is constrained and stop-losses are unreliable. Keep ON unless you specifically trade SME.',
  universe: 'Restrict the scan to an NSE index. Nifty 500 = the investable universe most funds track; Midcap 150 / Smallcap 250 are the sweet spot for swing moves — enough liquidity, more room to run. Membership refreshes weekly from niftyindices.com.',
  mcap: 'Size bucket derived from index membership (Nifty 100 = Large, Midcap 150 = Mid, Smallcap 250 = Small, Microcap 250 = Micro — SEBI-aligned). Mid + Small is where base-and-bounce works best: institutional interest exists but positions are still being built. Unclassified names fall outside all buckets.',
  sector: 'Trade with the sector tailwind (deck: theme development — infra, EV, AI...). Pick sectors showing leadership; a strong base in a leading sector has much better odds than the same base in a lagging one. Sector = NSE industry classification.',
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

export function Tip({ text }) {
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
  const [sectors, setSectors] = useState([]);
  useEffect(() => {
    getSectors().then((d) => setSectors(d.sectors || [])).catch(() => setSectors([]));
  }, []);
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
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 mt-3">
          <Sel label="Universe (index)" tip={TIPS.universe} options={UNIVERSE}
            value={filters.universe ?? null} onChange={(v) => set('universe', v)} />
          <div className="flex flex-col text-xs text-slate-300 gap-1">
            <span className="flex items-center gap-1.5">Market cap <Tip text={TIPS.mcap} /></span>
            <div className="flex gap-1.5 flex-wrap pt-1">
              {MCAPS.map(([val, lbl]) => {
                const on = (filters.mcapBuckets || []).includes(val);
                return (
                  <button key={val} type="button"
                    onClick={() => {
                      const cur = filters.mcapBuckets || [];
                      const next = on ? cur.filter((x) => x !== val) : [...cur, val];
                      set('mcapBuckets', next.length ? next : null);
                    }}
                    className={`px-2.5 py-1 rounded-full text-[11px] border ${on ? 'bg-blue-600 border-blue-500 text-white' : 'bg-slate-800 border-slate-600 text-slate-400'}`}>
                    {lbl}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="flex flex-col text-xs text-slate-300 gap-1 col-span-2">
            <span className="flex items-center gap-1.5">Sectors ({(filters.sectors || []).length ? (filters.sectors || []).length + ' selected' : 'all'}) <Tip text={TIPS.sector} /></span>
            <div className="flex gap-1.5 flex-wrap pt-1 max-h-20 overflow-y-auto">
              {sectors.map((sec) => {
                const on = (filters.sectors || []).includes(sec.name);
                return (
                  <button key={sec.name} type="button"
                    onClick={() => {
                      const cur = filters.sectors || [];
                      const next = on ? cur.filter((x) => x !== sec.name) : [...cur, sec.name];
                      set('sectors', next.length ? next : null);
                    }}
                    className={`px-2 py-0.5 rounded-full text-[11px] border ${on ? 'bg-purple-600 border-purple-500 text-white' : 'bg-slate-800 border-slate-600 text-slate-400'}`}>
                    {sec.name} <span className="opacity-60">{sec.count}</span>
                  </button>
                );
              })}
              {!sectors.length && <span className="text-slate-600 text-[11px]">sector data loading / not yet imported</span>}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4 mt-3">
          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input type="checkbox" checked={filters.excludeSme !== false}
              onChange={(e) => set('excludeSme', e.target.checked)} />
            Exclude SME / lot-traded <Tip text={TIPS.sme} />
          </label>
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
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Base quality</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              <Sel label="Base tightness (20d)" tip={TIPS.baseTight} options={BASE_RANGE} value={filters.baseRange20dMaxPct ?? null} onChange={(v) => set('baseRange20dMaxPct', v)} />
              <Sel label="Near 20d high" tip={TIPS.near20} options={NEAR_HIGH20} value={filters.within20dHighPct ?? null} onChange={(v) => set('within20dHighPct', v)} />
              <Sel label="Prior upmove" tip={TIPS.upmove} options={UPMOVE} value={filters.priorUpmoveMinPct ?? null} onChange={(v) => set('priorUpmoveMinPct', v)} />
              <Sel label="Giveback" tip={TIPS.giveback} options={GIVEBACK} value={filters.givebackMaxPct ?? null} onChange={(v) => set('givebackMaxPct', v)} />
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Volume &amp; IFP (defaults 100d/1.5×/0.60 — tune below results)</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              <Sel label="Vol dry-up" tip={TIPS.volDry} options={VOL_DRY} value={filters.volDryupMaxRatio ?? null} onChange={(v) => set('volDryupMaxRatio', v)} />
              <Sel label="Vol expansion (1d)" tip={TIPS.volExp} options={VOL_EXP} value={filters.volRatioMin ?? null} onChange={(v) => set('volRatioMin', v)} />
              <Sel label="ATR %" tip={TIPS.atr} options={ATR} value={filters.atrPct ?? null} onChange={(v) => set('atrPct', v)} />
              <Sel label="IFP score" tip={TIPS.ifp} options={IFP} value={filters.ifpScoreMin ?? null} onChange={(v) => set('ifpScoreMin', v)} />
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-300 mt-3">
              <input type="checkbox" checked={!!filters.obvSlopePositive}
                onChange={(e) => setFilters((f) => ({
                  ...f,
                  obvSlopePositive: e.target.checked ? true : null,
                  updownVolRatioMin: e.target.checked ? 1.2 : null,
                }))} />
              Flow confirm <Tip text="One-click volume-flow confirmation: requires up/down volume ratio >= 1.2 (buyers dominate over 50d) AND positive OBV slope (net accumulation). These are the two components behind the IFP score - as a single toggle instead of two dropdowns." />
            </label>
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
