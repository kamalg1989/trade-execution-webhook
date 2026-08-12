/**
 * Shared UI primitives for the screener app.
 *
 * WHY THIS FILE EXISTS. The app used the native `title` attribute for help
 * text. That does nothing on iOS — there is no hover on a touch screen — so
 * every explanation in the product was invisible on a phone, which is where
 * most of it gets read. `Info` below is a real popover: hover on a mouse, tap
 * on touch, and dismissible either way.
 *
 * The second job here is one source of truth for help text (`HELP`). Field
 * captions had drifted from the report's conclusions in a few places; keeping
 * every explanation in one dictionary means a finding is updated once rather
 * than hunted through 1,700 lines of JSX.
 */
import { useEffect, useRef, useState } from 'react';

/* ------------------------------------------------------------------ device */

/** True on phone-width viewports. Drives layout SWAPS (table -> cards), not
 *  just reflow — a 9-column table does not become usable by getting narrower. */
export function useIsMobile(breakpoint = 640) {
  const [m, setM] = useState(
    typeof window !== 'undefined' ? window.innerWidth < breakpoint : false);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [breakpoint]);
  return m;
}

/* ----------------------------------------------------------------- tooltip */

/**
 * Help popover. Opens on hover (mouse) or tap (touch), closes on outside tap,
 * Escape, or scroll.
 *
 * `align` controls which edge it hangs from so it cannot run off a narrow
 * screen — the whole point of replacing `title` was phone usability, and a
 * popover clipped by the viewport is no better than no popover.
 */
export function Info({ text, label = 'field', align = 'left', className = '' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('touchstart', away);
    document.addEventListener('keydown', esc);
    window.addEventListener('scroll', () => setOpen(false), { once: true, passive: true });
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('touchstart', away);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);

  if (!text) return null;
  const pos = align === 'right' ? 'right-0' : align === 'center'
    ? 'left-1/2 -translate-x-1/2' : 'left-0';

  return (
    <span ref={ref} className={`relative inline-flex align-middle ${className}`}>
      <button
        type="button"
        aria-label={`What is ${label}?`}
        aria-expanded={open}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((o) => !o); }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className={`w-4 h-4 shrink-0 rounded-full border text-[10px] leading-none
          flex items-center justify-center transition-colors
          ${open ? 'border-sky-400 bg-sky-500/25 text-sky-200'
                 : 'border-slate-600 bg-slate-800/80 text-slate-400 hover:border-sky-500 hover:text-sky-300'}`}
      >
        i
      </button>
      {open && (
        <span
          role="tooltip"
          onClick={(e) => e.stopPropagation()}
          className={`absolute top-6 ${pos} z-50 w-64 max-w-[78vw] rounded-lg border
            border-slate-600 bg-slate-950/98 px-3 py-2 text-[11px] leading-relaxed
            text-slate-200 shadow-2xl shadow-black/60 font-normal normal-case tracking-normal`}
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** Label + optional Info, used by form fields and table headers alike so the
 *  affordance is identical everywhere. */
export function LabelWithInfo({ children, help, align, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <span>{children}</span>
      <Info text={help} label={typeof children === 'string' ? children : 'field'} align={align} />
    </span>
  );
}

/* -------------------------------------------------------------- containers */

export function Section({ title, subtitle, help, tone = 'slate', children, right }) {
  const tones = {
    slate: 'border-slate-800',
    emerald: 'border-emerald-800/60',
    amber: 'border-amber-800/60',
  };
  const heads = {
    slate: 'text-slate-300',
    emerald: 'text-emerald-300',
    amber: 'text-amber-300',
  };
  return (
    <section className={`pt-4 mt-4 border-t ${tones[tone] || tones.slate}`}>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <h3 className={`text-xs font-semibold uppercase tracking-wide ${heads[tone] || heads.slate}`}>
            <LabelWithInfo help={help}>{title}</LabelWithInfo>
          </h3>
          {subtitle && (
            <p className="text-[11px] text-slate-500 leading-snug mt-1 max-w-2xl">{subtitle}</p>
          )}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

/** A labelled metric. `hint` is the one-line meaning; `help` the full story. */
export function Stat({ label, value, hint, help, tone = 'slate', size = 'md' }) {
  const tones = {
    slate: 'text-slate-100', good: 'text-emerald-300', bad: 'text-rose-300',
    warn: 'text-amber-300', info: 'text-sky-300',
  };
  return (
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/50 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-400">
        <LabelWithInfo help={help}>{label}</LabelWithInfo>
      </div>
      <div className={`${size === 'lg' ? 'text-xl' : 'text-base'} font-semibold tabular-nums mt-0.5 ${tones[tone]}`}>
        {value}
      </div>
      {hint && <div className="text-[10px] text-slate-500 leading-snug mt-0.5">{hint}</div>}
    </div>
  );
}

/** Small status/《meaning》pill. */
export function Pill({ children, tone = 'slate', title }) {
  const tones = {
    slate: 'border-slate-700 bg-slate-800 text-slate-300',
    good: 'border-emerald-700 bg-emerald-900/40 text-emerald-200',
    bad: 'border-rose-700 bg-rose-900/40 text-rose-200',
    warn: 'border-amber-700 bg-amber-900/40 text-amber-200',
    info: 'border-sky-700 bg-sky-900/40 text-sky-200',
  };
  return (
    <span title={title}
      className={`inline-block px-1.5 py-0.5 rounded border text-[10px] leading-tight
        whitespace-nowrap ${tones[tone] || tones.slate}`}>
      {children}
    </span>
  );
}

/* ------------------------------------------------------------- help corpus */

/**
 * Every explanation in the product, in one place.
 *
 * These are written to say what the EVIDENCE supports, not merely what the
 * field does — "15% stop" is useless without "supported range is 15-20% and
 * 10% failed out of sample". A user reading only the tooltips should end up
 * with the report's conclusions rather than needing to have read the report.
 */
export const HELP = {
  // ---- run window / capital
  startDate: 'First session of the simulation. For the portfolio strategy use the full 2016-01-01 start: a short window annualises noise and the whole point of this book is compounding.',
  endDate: 'Last session. Data currently ends 2026-08-08.',
  capital: 'Starting capital. Position sizes are recomputed from CURRENT equity at every rebalance, so the book compounds rather than sizing off this number forever.',
  strategy: 'PORTFOLIO is one continuous compounding simulation and is the frozen candidate. POSITIONAL runs the same momentum logic but resets capital each window. BREAKOUT is the original swing screener — measured at ₹202k profit against ₹298k of costs over 11 years, and not allocated.',
  notes: 'Free text stored with the run. Useful for labelling a batch so you can find it later in the list.',

  // ---- portfolio core
  momentum: 'The ranking signal: total price change over this lookback. 6 months is frozen. 3m trades more and 12m less; neither beat 6m on risk-adjusted return out of sample.',
  rebalance: 'Sessions between rebalances. 63 ≈ quarterly. Lower means more turnover, and turnover is what killed the breakout book — costs consumed ~74% of its gross edge.',
  topN: 'How many names to hold, equal weighted. 20 is frozen. Going to 30-45 consistently LOWERED out-of-sample drawdown (38% → 30%) at a cost of ~2-3pp CAGR — a real risk trade-off, but no specific value passed the pre-registered selection test.',
  bufferN: 'A held name is only sold once it falls below this rank. The gap between hold and buffer is anti-churn hysteresis: without it a name oscillating around rank 20 is bought and sold every rebalance. Kept at 2× top-N automatically.',
  minTurnover: 'Liquidity floor on 1-month average traded value. Below this, the modelled 0.10% slippage is optimistic.',
  slPct: 'Fixed stop below the entry fill, checked every session. Supported range is 15-20%; 10% was the worst performer out of sample. The stop costs ~0.4pp of CAGR and removes ~18pp of max drawdown — the best single trade available in this strategy.',

  // ---- portfolio risk overlays (all tested, mostly rejected)
  volMode: 'Scales exposure by the book\'s own recent volatility, cutting the number of positions held and raising cash. TESTED AND REJECTED: the aggressive version cost 5.4pp of CAGR for ZERO drawdown reduction. Only a mild 75% floor helped, and only marginally.',
  volFloor: 'Lowest exposure the scaler may cut to. Only mild floors (75% and above) ever improved risk-adjusted return.',
  ddThrottle: 'Halves new exposure once the book is this far below its high-water mark. TESTED AND REJECTED at every threshold: it turns 2017 from +91.6% into +43.0% and makes the ulcer index WORSE, because it cuts exposure after the loss and restores it after the recovery.',
  sectorCap: 'Maximum holdings from one sector. A mid-2018 top-20 held NINE names in a single sector, so this is not theoretical. But sector data covers only ~55% of traded names, so the cap binds on roughly half the book. 2 per sector cost more than it bought; 3 was about neutral.',
  sectorPct: 'Maximum share of equity in one sector. Same coverage caveat as the count cap.',
  perStock: 'Maximum share of equity in one name. Near-inert at top-20 or wider, where a slot is already 5% or less.',
  requireSector: 'DO NOT TRUST RESULTS FROM THIS. Sector data exists only for CURRENT NSE index members, so restricting to it filters the 2016 universe by "was in an index in 2026" — picking winners with a decade of hindsight. It posts the best numbers in the project and they are survivorship artifacts.',

  // ---- results: rupee columns
  realized: 'Banked profit and loss from closed trades, after all costs. Often NEGATIVE in a good year for this strategy: it banks stop losses while the gains sit in open winners.',
  unrealized: 'Open positions marked to the last close, net of the charges paid at entry. On portfolio runs this is derived as (total − realized) so the three figures reconcile exactly.',
  total: 'On PORTFOLIO runs this is the engine\'s own final equity minus starting capital — the authoritative figure. On other runs it is realized + unrealized.',

  // ---- results: path metrics
  cagr: 'Compound annual growth rate. Only meaningful on a continuous run: on a one-year window it annualises a single short period and is not comparable.',
  maxDD: 'Largest peak-to-trough fall in account equity. The number that decides how much capital you can allocate — observed 39.3%, and a model-conditional stress p95 of 43-48%.',
  worst12m: 'Worst return over any rolling 252-session window. Tells you what a bad YEAR feels like regardless of where the calendar boundaries fall.',
  ulcer: 'Root-mean-square drawdown. Unlike max drawdown it accounts for how LONG the book stays underwater, not just the depth of the single worst hole.',
  martin: 'CAGR divided by ulcer index: return per unit of time-weighted pain. Used instead of CAGR/maxDD because a book that spends three years underwater is worse than one that dips once, even at the same depth.',
  turnover: 'Round-trip trading volume per year as a multiple of average equity. The single best predictor of failure in this project — every high-turnover variant lost to costs.',
  trades: 'Closed trades plus positions still open at the end of the window.',
  winRate: 'Share of closed trades that made money. Deliberately not a headline: momentum books are usually right less than half the time and profitable anyway, because winners are held far longer than losers.',

  // ---- run list
  window: 'Simulation start and end. A PORTFOLIO run under two years is a standalone simulation that restarts at the initial capital — its P&L cannot be added to, or compared with, the compounded continuous run.',
  status: 'QUEUED, RUNNING, COMPLETED or FAILED. Only one run executes at a time.',
  settings: 'What differs from the defaults on this run. Settings at their default value are omitted so the column stays scannable.',
  equityChart: 'Green is account equity (cash + marked holdings). The dashed line is the running high-water mark, and the shaded band between them is the drawdown. Sampled weekly.',
};
