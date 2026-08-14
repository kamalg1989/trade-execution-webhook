import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createBacktestRun, listBacktestRuns, getBacktestRun, getBacktestSummary,
  getBacktestTrades, getBacktestDay, cancelBacktestRun, backtestTradeChartUrl,
} from '../api/client.js';
import { HELP, Info, LabelWithInfo, Pill, Stat, useIsMobile } from '../components/ui.jsx';

const fmtInr = (n) =>
  n == null ? '—' : `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const fmtR = (n) => (n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}R`);
const pnlColor = (n) => (n == null ? 'text-slate-400' : n > 0 ? 'text-emerald-400' : n < 0 ? 'text-red-400' : 'text-slate-300');

const STATUS_COLOR = {
  RUNNING: 'text-amber-300', COMPLETED: 'text-emerald-400', FAILED: 'text-red-400',
};
const TRADE_STATUS_COLOR = {
  PENDING: 'text-slate-400', OPEN: 'text-blue-300', CLOSED: 'text-slate-200', SUPERSEDED: 'text-slate-500',
};

// Light transparent row tint by track — quant (sky), AI (purple), both (indigo blend).
const trackRowClass = (t) => {
  const q = t.quantRank != null, a = t.aiRank != null;
  if (q && a) return 'bg-indigo-500/10';
  if (q) return 'bg-sky-500/10';
  if (a) return 'bg-purple-500/10';
  return '';
};

// ---------------- Run settings summary (for the run list "Settings" column) ----------------

const EXIT_LABELS = [
  ['ema10_trail', 'EMA10 trail'], ['ema21_trail', 'EMA21 trail'], ['ema50_trail', 'EMA50 trail'],
  ['chandelier_trail', 'Chandelier trail'], ['swing_trail', 'Swing trail'],
  ['macd_trail', 'MACD trail (weekly)'],
  ['failed_breakout_exit', 'Failed-breakout exit'], ['swing_break_exit', 'Swing-break exit'],
];
const GATE_LABELS = [
  ['gateMinTurnoverCr', (v) => `turnover≥${v}cr`],
  ['gateMaxBaseRangePct', (v) => `baseRange<${v}%`],
  ['gateMinVolMult', (v) => `volMult>${v}x`],
  ['gateMinPriorUpmovePct', (v) => `upmove≥${v}%`],
  ['gateMaxGivebackPct', (v) => `giveback≤${v}%`],
  ['gateMaxVolDryupRatio', (v) => `dryup≤${v}x`],
  ['gateMaxDistFromHighPct', (v) => `distFromHigh≥${v}%`],
  ['gateMinIfpScore', (v) => `ifp≥${v}`],
];

// The positional stop-loss choices, ordered the way they should be REASONED
// about rather than alphabetically: no stop, then the two price-distance stops,
// then the moving-average stops from fastest (EMA21, exits early and often) to
// slowest (SMA200, barely fires). Presenting the MA group as an ordered speed
// ladder is what lets a plateau across neighbours be read as a real effect.
const POS_SL_MODES = [
  { v: 'none', label: 'None — exit only at rebalance' },
  { v: 'fixed', label: 'Fixed % below entry', pct: true },
  { v: 'trail', label: 'Trailing % below peak', pct: true },
  { v: 'ema21', label: 'Structural — close < EMA21 (fastest)' },
  { v: 'sma50', label: 'Structural — close < SMA50' },
  { v: 'ema50', label: 'Structural — close < EMA50' },
  { v: 'sma200', label: 'Structural — close < SMA200 (slowest)' },
];
const POS_SL_NEEDS_PCT = new Set(POS_SL_MODES.filter((m) => m.pct).map((m) => m.v));

// A PORTFOLIO run spanning under ~2 years is a standalone simulation that starts
// fresh at the run's capital. Its CAGR annualises one short window and its P&L
// is not additive with the compounded continuous run.
function isShortWindow(run) {
  if (!run.startDate || !run.endDate) return false;
  const days = (new Date(run.endDate) - new Date(run.startDate)) / 86400000;
  return days < 730;
}

function slLabel(mode, pct) {
  if (!mode || mode === 'none') return null;
  if (POS_SL_NEEDS_PCT.has(mode)) return `SL ${mode} ${Number(pct)}%`;
  return `SL ${mode.toUpperCase()}`;
}

// Compact list of tags describing what actually differs from stock defaults
// on this run — shown as the "Settings" column in the run list, and as a
// fuller tooltip (title attr) on hover. Intentionally omits anything at its
// default value so the column stays scannable across many runs.
function summarizeRunSettings(run) {
  const tags = [];
  if (run.strategy === 'PORTFOLIO') {
    // A continuous compounding book — the knobs that describe it are the risk
    // controls, not the breakout gates, so summarise on its own terms.
    const t = ['PORTFOLIO', `${run.posMomentum?.replace('pct_chg_', '') ?? '6m'} mom`,
               `top${run.posTopN}`, `rebal ${run.posRebalanceDays}d`];
    // A window under ~2 years is a STANDALONE simulation that restarts at the
    // initial capital. Its CAGR annualises a single short period and its P&L
    // cannot be added to, or compared with, the compounded continuous run. That
    // warning has to be on the ROW — a reader scanning the list will not go
    // looking for it in the notes.
    if (isShortWindow(run)) t.unshift('⚠ 1-YR STANDALONE — DO NOT SUM');
    if (run.posSlPct > 0) t.push(`stop ${run.posSlPct}%`);
    if (run.pfVolMode && run.pfVolMode !== 'none') {
      t.push(`vol:${run.pfVolMode}${run.pfVolFloor ? ` floor${run.pfVolFloor}%` : ''}`);
    }
    if (run.pfDdThrottleAt > 0) t.push(`ddThrottle ${(run.pfDdThrottleAt * 100).toFixed(0)}%`);
    if (run.pfMaxStocksPerSector && run.pfMaxStocksPerSector < 99) {
      t.push(`${run.pfMaxStocksPerSector}/sector`);
    }
    if (run.pfRequireSector) t.push('⚠ sector-only universe');
    return t;
  }
  if (run.strategy === 'WEEKLY_BREAKOUT') {
    // Weekly consolidation-box breakout — an entirely different engine, so
    // summarise on its own terms (risk %, max picks/day, resting window)
    // rather than falling through to the daily-funnel gate tags below.
    const t = ['WEEKLY_BREAKOUT', `risk${run.weeklyRiskPct ?? 1.0}%`];
    if (run.maxPicksPerTrack != null && run.maxPicksPerTrack !== 3) t.push(`top${run.maxPicksPerTrack}/wk`);
    if (run.restingWindowDays != null) t.push(`rest:${run.restingWindowDays}wk`);
    if (run.stackingGuard) t.push(`stack:${run.stackingGuardMode}`);
    return t;
  }
  if (run.maxPicksPerTrack != null && run.maxPicksPerTrack !== 3) tags.push(`top${run.maxPicksPerTrack}/track`);
  if (run.strategy === 'POSITIONAL') {
    // Positional runs share almost none of the breakout knobs, so summarise
    // them on their own terms rather than showing a wall of "not set".
    return [`POSITIONAL`, `${run.posMomentum?.replace('pct_chg_', '') ?? '6m'} mom`,
            `top${run.posTopN}/buf${run.posBufferN}`,
            `rebal ${run.posRebalanceDays}d`,
            slLabel(run.posSlMode, run.posSlPct)].filter(Boolean);
  }
  if (run.quantFunnelVariant === 'v2') tags.push('rank:v2');
  // The validated edges — shown first-class so a run's identity is obvious
  if (run.stage2BaseStageMaxAllowed != null) tags.push(`baseStage≤${run.stage2BaseStageMaxAllowed}`);
  if (run.entryBreadthMaxPct != null) tags.push(`breadth<${run.entryBreadthMaxPct}%`);
  if (run.entryBreadthRequireRising) tags.push('breadth↑');
  if (run.maxContractionRatio != null) tags.push(`VCP≤${run.maxContractionRatio}`);
  if (run.requireWeeklyBoxBreakout) tags.push(`weekly-box≤${run.weeklyBoxLookbackDays ?? 10}d`);
  if (run.riskPerTradePct != null) tags.push(`risk${run.riskPerTradePct}%`);
  if (run.maxCapitalPerTradePct != null) tags.push(`cap${run.maxCapitalPerTradePct}%`);
  for (const [key, fmt] of GATE_LABELS) {
    if (run[key] != null) tags.push(fmt(run[key]));
  }
  const ec = run.exitConfig || {};
  for (const [key, label] of EXIT_LABELS) {
    if (ec[key]) tags.push(label);
  }
  if (ec.fixed_target === false) tags.push('no fixed target');
  if (ec.half_booking === false) tags.push('no half-book');
  if (ec.breakeven === false) tags.push('no breakeven');
  if (ec.trailing === false) tags.push('no trailing');
  if (run.safetySlPct != null) tags.push(`SL${run.safetySlPct}%`);
  if (run.stackingGuard) tags.push(`stack:${run.stackingGuardMode}`);
  if (run.restingWindowDays != null) tags.push(`rest:${run.restingWindowDays}d`);
  if (run.minPositionValue) tags.push(`min₹${run.minPositionValue}`);
  return tags;
}

function SettingsCell({ run }) {
  const tags = summarizeRunSettings(run);
  const notes = run.params?.notes;
  const full = [notes, ...tags].filter(Boolean).join(' · ');
  return (
    <td className="py-1.5 px-2 text-[11px] text-slate-400 min-w-[220px]" title={full || undefined}>
      {notes && <div className="text-slate-300 truncate max-w-[320px]">{notes}</div>}
      <div className="flex flex-wrap gap-1 mt-0.5">
        {tags.length ? tags.map((t, i) => (
          <span key={i} className="px-1 py-0.5 rounded bg-slate-800 border border-slate-700 whitespace-nowrap leading-tight">{t}</span>
        )) : !notes ? <span className="text-slate-600">defaults</span> : null}
      </div>
    </td>
  );
}

// ---------------- Run config form ----------------

const DEFAULT_FORM = {
  // The form opens on the FROZEN strategy over the full continuous window, so
  // the default action is "run the thing we concluded is right" rather than
  // "assemble a config from 47 blank fields". The breakout defaults below are
  // retained for the advanced panel but are no longer what loads first.
  strategy: 'PORTFOLIO',
  // Frozen values (BACKTEST_REPORT section 10), so the form is runnable as-is.
  pos_momentum: 'pct_chg_6m', pos_rebalance_days: 63, pos_top_n: 20,
  pos_buffer_n: 40, pos_min_turnover_cr: 5,
  pos_sl_mode: 'fixed', pos_sl_pct: 15,
  // PORTFOLIO risk controls. Every one defaults to INERT — a control that is on
  // by default cannot be measured against a baseline, which is exactly the bug
  // that made the first sector-cap run identical to the run it should have
  // differed from.
  pf_vol_mode: 'none', pf_vol_floor: 75,
  pf_max_per_stock_pct: 100, pf_max_per_sector_pct: 100,
  pf_max_stocks_per_sector: 99, pf_require_sector: false,
  pf_dd_throttle_at: 0,
  // Full continuous window by default — a portfolio run over a few months
  // annualises noise, and the whole point of this strategy is compounding.
  start_date: '2016-01-01', end_date: '2026-08-08',
  track_mode: 'QUANT', capital: 400000,
  restIndefinite: true, resting_window_days: 5,
  stacking_guard: true, stacking_guard_mode: 'OVERRIDE',
  max_picks_per_track: 2,
  // Validated edges (see PRESETS below / sql/011-014 migration comments)
  stage2_base_stage_max_allowed: 2,
  entry_breadth_max_pct: 40,
  entry_breadth_require_rising: true,
  max_contraction_ratio: '',
  risk_per_trade_pct: '',
  // Exits — defaults are the combination that won every sweep so far
  breakeven: true, half_booking: true, trailing: true, fixed_target: false,
  ema21_trail: true,
  ema10_trail: false, ema50_trail: false, chandelier_trail: false, swing_trail: false,
  failed_breakout_exit: false, swing_break_exit: false,
  // Untested experiments borrowed from WEEKLY_BREAKOUT — see run #589 note
  // in the BREAKOUT-strategy panel below.
  macd_trail: false, require_weekly_box_breakout: false, weekly_box_lookback_days: 10,
  // Production's sl_engine.py runs at 18:00 IST when Dhan rejects market
  // orders, so exits are forever orders that fill at the NEXT session's open,
  // not the trigger day's close. Defaulting this on is what production
  // actually does — close-fill modelling understates it by ~Rs.90k/decade.
  next_open_exit: true,
  safety_sl_pct: 10.0, slippage_pct: 0.10, brokerage_per_order: 0.0, chandelier_atr_mult: 3.0,
  max_capital_per_trade_pct: '', min_position_value: '',
  // Signal cadence — how often the funnel scans for new candidates. Daily is
  // production today; weekly is what the validated exit-ladder-fix preset uses.
  signal_cadence: 'daily', signal_scan_day: 'last',
  entry_v2_buy_points: false, base_stage_ladder: 'prod',
  // WEEKLY_BREAKOUT-only — account-risk % per trade for that strategy's own
  // position-sizing formula (see backtest/weekly_breakout.py size_position()).
  weekly_risk_pct: 1.0,
  notes: '',
};

// One-click starting points. "Best known" is the configuration that came out
// ahead across BOTH validation windows (2025 + 2026) in the sweeps; "Production
// today" is what screen_gpt.py actually runs right now, for A/B comparison.
const PRESETS = {
  validated: {
    label: '⭐ Validated: exit-ladder fix', hint: 'Best return/drawdown found so far — '
        + 'weekly scans, EMA21 trail, no half-booking or R-ladder',
    values: {
      // The B5 finding (BACKTEST_REPORT §9.19): removing half-booking and the
      // R-ladder ratchet from production's exit ladder roughly TRIPLES return
      // at unchanged drawdown. Won BOTH halves of a FIT(2016-20)/TEST(2021-26)
      // split — the only candidate in this project's history to do that.
      // Full-window result: Rs.315,597 total P&L, Rs.51,203 maxDD, ret/DD
      // 6.16, win rate 34.1%, 1,287 trades over 2016-2026 at weekly cadence /
      // 3 picks per scan.
      strategy: 'BREAKOUT', track_mode: 'QUANT', capital: 400000,
      max_picks_per_track: 3, stage2_base_stage_max_allowed: 2,
      risk_per_trade_pct: 0.25, max_capital_per_trade_pct: 10.0,
      safety_sl_pct: 10.0,
      stacking_guard: true, stacking_guard_mode: 'OVERRIDE',
      signal_cadence: 'weekly', signal_scan_day: 'last',
      entry_v2_buy_points: false, base_stage_ladder: 'prod',
      // The exit ladder itself — this is the whole finding.
      breakeven: true, half_booking: false, trailing: false, fixed_target: false,
      ema21_trail: true, ema10_trail: false, ema50_trail: false,
      chandelier_trail: false, swing_trail: false,
      failed_breakout_exit: false, swing_break_exit: false,
      next_open_exit: true,
      start_date: '2016-01-01', end_date: '2026-08-08',
    },
  },
  best: {
    label: 'Best known', hint: 'Winner across both validation windows',
    values: {
      strategy: 'BREAKOUT',
      track_mode: 'QUANT', max_picks_per_track: 2, stage2_base_stage_max_allowed: 2,
      entry_breadth_max_pct: 40, entry_breadth_require_rising: true,
      max_contraction_ratio: 0.7, risk_per_trade_pct: 1.0,
      stacking_guard: true, stacking_guard_mode: 'OVERRIDE', safety_sl_pct: 10,
      breakeven: true, half_booking: true, trailing: true, fixed_target: false,
      ema21_trail: true, ema10_trail: false, ema50_trail: false,
      chandelier_trail: false, swing_trail: false,
      failed_breakout_exit: false, swing_break_exit: false,
    },
  },
  positional: {
    label: 'Positional momentum',
    hint: 'Low-turnover rotation, 11-window validated: 63d rebalance, top-20, '
        + 'fixed 15% stop. Cost drag ~1.6%/yr vs ~5.7% for breakout.',
    values: {
      // These are the plateau-supported settings from the 11-window sweep, not
      // the grid maximum: 63d/top-20 sit in the middle of a smooth region, and
      // the 15% stop was the one change that improved return AND drawdown at
      // once (969k -> 1034k total, 42% -> 33% maxDD).
      strategy: 'POSITIONAL', pos_momentum: 'pct_chg_6m', pos_rebalance_days: 63,
      pos_top_n: 20, pos_buffer_n: 40, pos_min_turnover_cr: 5, capital: 400000,
      pos_sl_mode: 'fixed', pos_sl_pct: 15,
    },
  },
  portfolio: {
    label: 'Portfolio (continuous)',
    hint: 'FROZEN CANDIDATE. ONE compounding run 2016→2026 — capital carried '
        + 'forward, positions held across year ends, daily mark-to-market. '
        + 'Reports CAGR / maxDD / ulcer, not summed annual P&L. top-20 is the '
        + 'frozen baseline: the pre-registered selection rule did not identify '
        + 'a better value, and a pre-registered rule cannot be discarded after '
        + 'seeing the data it was written to judge.',
    values: {
      // FROZEN (BACKTEST_REPORT section 10). top-20 is the baseline because the
      // pre-registered Martin criterion did NOT select any value in 30-50 - not
      // because 20 scored best (it did not; it was worst on TEST). A separate,
      // legitimate finding is that going 20 -> 45 reduced out-of-sample drawdown
      // by ~8pp at a cost of ~2-3pp CAGR. Substituting 35 or 45 is permitted
      // ONLY as an explicit pre-registered preference for lower drawdown over
      // return, recorded BEFORE paper trading - never chosen afterwards.
      // The stop is a supported RANGE of 15-20%; 15 is the midpoint, not a
      // proven optimum. Every risk control is off: vol scaling and the
      // drawdown throttle both measured NET NEGATIVE.
      strategy: 'PORTFOLIO', pos_momentum: 'pct_chg_6m', pos_rebalance_days: 63,
      pos_top_n: 20, pos_buffer_n: 40, pos_min_turnover_cr: 5, capital: 400000,
      pos_sl_pct: 15, pf_vol_mode: 'none', pf_dd_throttle_at: 0,
      pf_max_stocks_per_sector: 3, pf_max_per_sector_pct: 30,
      pf_require_sector: false,
      start_date: '2016-01-01', end_date: '2026-08-08',
    },
  },
  portfolioBaseline: {
    label: 'Portfolio (no controls)',
    hint: 'Same continuous book with NO stop and no risk controls — the true '
        + 'baseline every control must be measured against.',
    values: {
      strategy: 'PORTFOLIO', pos_momentum: 'pct_chg_6m', pos_rebalance_days: 63,
      pos_top_n: 20, pos_buffer_n: 40, pos_min_turnover_cr: 5, capital: 400000,
      pos_sl_pct: 0, pf_vol_mode: 'none', pf_dd_throttle_at: 0,
      pf_max_stocks_per_sector: 99, pf_max_per_sector_pct: 100,
      start_date: '2016-01-01', end_date: '2026-08-08',
    },
  },
  weeklyBreakout: {
    label: 'Weekly consolidation breakout',
    hint: 'Weekly-timeframe box-breakout strategy — 20W SMA trend filter, '
        + 'weekly MACD momentum + trailing exit, 6+ week consolidation box, '
        + '5-20% breakout above resistance. See weekly_breakout.py for the full spec.',
    values: {
      strategy: 'WEEKLY_BREAKOUT', capital: 400000, weekly_risk_pct: 1.0,
      max_picks_per_track: 3, resting_window_days: 2, restIndefinite: false,
      stacking_guard: true, stacking_guard_mode: 'SKIP',
      start_date: '2016-01-01', end_date: '2026-08-08',
    },
  },
  positionalNoSl: {
    label: 'Positional (no stop)',
    hint: 'Same rotation with exits only at rebalance — the original behaviour, '
        + 'kept as the reference point the stop is measured against.',
    values: {
      strategy: 'POSITIONAL', pos_momentum: 'pct_chg_6m', pos_rebalance_days: 63,
      pos_top_n: 20, pos_buffer_n: 40, pos_min_turnover_cr: 5, capital: 400000,
      pos_sl_mode: 'none', pos_sl_pct: 0,
    },
  },
  production: {
    label: 'Production today', hint: 'What screen_gpt.py currently runs',
    values: {
      strategy: 'BREAKOUT',
      track_mode: 'QUANT', max_picks_per_track: 3, stage2_base_stage_max_allowed: '',
      entry_breadth_max_pct: '', entry_breadth_require_rising: false,
      max_contraction_ratio: '', risk_per_trade_pct: '',
      stacking_guard: true, stacking_guard_mode: 'OVERRIDE', safety_sl_pct: 8,
      breakeven: true, half_booking: true, trailing: true, fixed_target: true,
      ema21_trail: false, ema10_trail: false, ema50_trail: false,
      chandelier_trail: false, swing_trail: false,
      failed_breakout_exit: false, swing_break_exit: false,
    },
  },
};

function Toggle({ label, checked, onChange, hint }) {
  return (
    <label className="flex items-start gap-2 cursor-pointer select-none">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-emerald-500" />
      <span>
        <span className="text-sm text-slate-200">{label}</span>
        {hint && <span className="block text-[11px] text-slate-500">{hint}</span>}
      </span>
    </label>
  );
}

function Field({ label, hint, help, value, onChange, type = 'text', ...rest }) {
  return (
    <label className="text-xs text-slate-400 flex flex-col gap-1">
      <LabelWithInfo help={help}>{label}</LabelWithInfo>
      {/* text-base on mobile: iOS Safari zooms the page when focusing an input
          under 16px, and that zoom does not undo itself on blur. */}
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)}
        className="bg-slate-800 border border-slate-600 rounded px-2.5 py-2 text-slate-100
          text-base sm:text-sm focus:border-sky-500 focus:outline-none focus:ring-1
          focus:ring-sky-500/40 transition-colors" {...rest} />
      {hint && <span className="text-[11px] text-slate-500 leading-snug">{hint}</span>}
    </label>
  );
}

/** Select with the same label / help / touch treatment as Field. */
function SelectField({ label, hint, help, value, onChange, children }) {
  return (
    <label className="text-xs text-slate-400 flex flex-col gap-1">
      <LabelWithInfo help={help}>{label}</LabelWithInfo>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="bg-slate-800 border border-slate-600 rounded px-2.5 py-2 text-slate-100
          text-base sm:text-sm focus:border-sky-500 focus:outline-none focus:ring-1
          focus:ring-sky-500/40 transition-colors">
        {children}
      </select>
      {hint && <span className="text-[11px] text-slate-500 leading-snug">{hint}</span>}
    </label>
  );
}

function RunConfigForm({ onCreated, blocked, blockedReason, open, onToggleOpen }) {
  const [f, setF] = useState(DEFAULT_FORM);
  // Collapsed by default. The frozen config is the intended path; the other 40+
  // fields are research surface for re-verifying findings, not a starting point.
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const set = (k) => (v) => setF((s) => ({ ...s, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    if (!f.start_date || !f.end_date) { setError('Pick a start and end date.'); return; }
    setSubmitting(true);
    setError('');
    try {
      const numOrNull = (v) => (v === '' || v == null ? null : Number(v));
      const payload = {
        strategy: f.strategy,
        pos_momentum: f.pos_momentum,
        pos_rebalance_days: Number(f.pos_rebalance_days) || 21,
        pos_top_n: Number(f.pos_top_n) || 10,
        pos_buffer_n: Number(f.pos_buffer_n) || 20,
        pos_min_turnover_cr: Number(f.pos_min_turnover_cr) || 5,
        pos_sl_mode: f.pos_sl_mode || 'none',
        // The MA stops ignore a percentage; send 0 rather than a stale value so
        // the run row can't imply a threshold that was never applied.
        // PORTFOLIO always uses a fixed stop (the only stop type that survived
        // testing), so it reads pos_sl_pct directly rather than via pos_sl_mode.
        pos_sl_pct: f.strategy === 'PORTFOLIO'
          ? Number(f.pos_sl_pct) || 0
          : (POS_SL_NEEDS_PCT.has(f.pos_sl_mode) ? Number(f.pos_sl_pct) || 0 : 0),
        pf_vol_mode: f.pf_vol_mode || 'none',
        pf_vol_floor: f.pf_vol_mode === 'none' ? null : Number(f.pf_vol_floor) || 75,
        pf_max_per_stock_pct: Number(f.pf_max_per_stock_pct) || 100,
        pf_max_per_sector_pct: Number(f.pf_max_per_sector_pct) || 100,
        pf_max_stocks_per_sector: Number(f.pf_max_stocks_per_sector) || 99,
        pf_require_sector: !!f.pf_require_sector,
        pf_dd_throttle_at: Number(f.pf_dd_throttle_at) || 0,
        start_date: f.start_date, end_date: f.end_date, track_mode: f.track_mode,
        capital: Number(f.capital) || 400000,
        resting_window_days: f.restIndefinite ? null : Number(f.resting_window_days) || null,
        stacking_guard: f.stacking_guard,
        stacking_guard_mode: f.stacking_guard ? f.stacking_guard_mode : null,
        max_picks_per_track: Number(f.max_picks_per_track) || 2,
        stage2_base_stage_max_allowed: numOrNull(f.stage2_base_stage_max_allowed),
        entry_breadth_max_pct: numOrNull(f.entry_breadth_max_pct),
        entry_breadth_require_rising: f.entry_breadth_require_rising,
        max_contraction_ratio: numOrNull(f.max_contraction_ratio),
        risk_per_trade_pct: numOrNull(f.risk_per_trade_pct),
        max_capital_per_trade_pct: numOrNull(f.max_capital_per_trade_pct),
        min_position_value: Number(f.min_position_value) || 0,
        signal_cadence: f.signal_cadence || 'daily',
        signal_scan_day: f.signal_scan_day || 'last',
        entry_v2_buy_points: !!f.entry_v2_buy_points,
        base_stage_ladder: f.base_stage_ladder || 'prod',
        exit_config: {
          breakeven: f.breakeven, half_booking: f.half_booking,
          trailing: f.trailing, fixed_target: f.fixed_target,
          ema10_trail: f.ema10_trail, ema21_trail: f.ema21_trail, ema50_trail: f.ema50_trail,
          chandelier_trail: f.chandelier_trail, swing_trail: f.swing_trail,
          macd_trail: !!f.macd_trail,
          failed_breakout_exit: f.failed_breakout_exit, swing_break_exit: f.swing_break_exit,
          next_open_exit: !!f.next_open_exit,
        },
        safety_sl_pct: Number(f.safety_sl_pct) || 10.0,
        slippage_pct: Number(f.slippage_pct) || 0,
        brokerage_per_order: Number(f.brokerage_per_order) || 0,
        chandelier_atr_mult: Number(f.chandelier_atr_mult) || 3.0,
        weekly_risk_pct: Number(f.weekly_risk_pct) || 1.0,
        require_weekly_box_breakout: !!f.require_weekly_box_breakout,
        weekly_box_lookback_days: Number(f.weekly_box_lookback_days) || 10,
        notes: f.notes || null,
      };
      const res = await createBacktestRun(payload);
      onCreated(res.id);
      onToggleOpen(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) {
    return (
      <button onClick={() => onToggleOpen(true)}
        className="w-full flex items-center justify-between bg-slate-900/60 border border-slate-700 rounded-lg px-4 py-2.5 text-left hover:bg-slate-900">
        <span className="text-sm font-semibold text-slate-200">+ New Backtest Run</span>
        {blocked && <span className="text-xs text-amber-300">{blockedReason}</span>}
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="bg-slate-900/60 border border-slate-700 rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-slate-200">New Backtest Run</div>
        <button type="button" onClick={() => onToggleOpen(false)}
          className="text-xs text-slate-400 hover:text-white px-2 py-1">Collapse ✕</button>
      </div>

      {/* ---- FROZEN QUICK START ------------------------------------------
          The default path. One click loads the configuration that survived
          validation (BACKTEST_REPORT section 10) over the full continuous
          window. Everything else on this form exists for research and is
          hidden until asked for — 47 fields, most of which belong to a
          strategy the evidence says not to trade, is not a starting point. */}
      {!advanced && (
        <div className="rounded-lg border border-emerald-800/60 bg-emerald-950/20 p-3">
          <div className="flex items-start justify-between gap-3 mb-2">
            <div>
              <div className="text-sm font-semibold text-emerald-300">Frozen strategy</div>
              <div className="text-[11px] text-slate-400 leading-snug mt-0.5">
                6-month momentum · 63-session rebalance · top 20 · buffer 40 ·
                15% stop · no overlays. Observed 19.5% CAGR / 39.3% max drawdown
                over 2016–2026.
              </div>
            </div>
            <button type="button"
              onClick={() => setF((s0) => ({ ...s0, ...PRESETS.portfolio.values }))}
              className="shrink-0 text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white font-medium">
              Load frozen config
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
            <Field label="Start date" help={HELP.startDate} type="date"
              value={f.start_date} onChange={set('start_date')} required />
            <Field label="End date" help={HELP.endDate} type="date"
              value={f.end_date} onChange={set('end_date')} required />
            <Field label="Capital (₹)" help={HELP.capital} type="number"
              value={f.capital} onChange={set('capital')} min="10000" step="10000" />
            <Field label="Hold top N" help={HELP.topN}
              hint="20 frozen · 30–45 lowers drawdown, costs ~2–3pp CAGR"
              value={f.pos_top_n}
              onChange={(v) => setF((s0) => ({ ...s0, pos_top_n: v, pos_buffer_n: Number(v) * 2 }))}
              type="number" min="1" max="60" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 mt-3">
            <Field label="Fixed stop (%)" help={HELP.slPct}
              hint="Range 15–20% · 10% rejected"
              value={f.pos_sl_pct} onChange={set('pos_sl_pct')} type="number" min="0" max="50" step="0.5" />
            <Field label="Rebalance (sessions)" help={HELP.rebalance} hint="63 ≈ quarterly"
              value={f.pos_rebalance_days} onChange={set('pos_rebalance_days')} type="number" min="1" max="250" />
            <SelectField label="Momentum lookback" help={HELP.momentum}
              value={f.pos_momentum} onChange={set('pos_momentum')}>
              <option value="pct_chg_3m">3 months</option>
              <option value="pct_chg_6m">6 months (frozen)</option>
              <option value="pct_chg_1y">12 months</option>
            </SelectField>
            <Field label="Min turnover (₹ cr)" help={HELP.minTurnover} hint="Liquidity floor"
              value={f.pos_min_turnover_cr} onChange={set('pos_min_turnover_cr')} type="number" min="0" step="0.5" />
          </div>
          {f.strategy !== 'PORTFOLIO' && (
            <div className="text-[11px] text-amber-300/90 mt-2">
              ⚠ Strategy is <b>{f.strategy}</b>, so these fields may not all apply.
              Click <b>Load frozen config</b> to switch to the continuous portfolio.
            </div>
          )}
        </div>
      )}

      {/* ---- VALIDATED EXIT-LADDER-FIX QUICK START ------------------------
          The B5 finding (BACKTEST_REPORT §9.19): dropping half-booking and the
          R-ladder ratchet from production's exit ladder ~triples return at
          unchanged drawdown, and is the first candidate to win BOTH halves of
          a FIT/TEST split. Surfaced here as its own one-click config, same as
          the frozen portfolio above, rather than buried in advanced toggles. */}
      {!advanced && (
        <div className="rounded-lg border border-sky-800/60 bg-sky-950/20 p-3">
          <div className="flex items-start justify-between gap-3 mb-2">
            <div>
              <div className="text-sm font-semibold text-sky-300">
                ⭐ Validated: exit-ladder fix
              </div>
              <div className="text-[11px] text-slate-400 leading-snug mt-0.5">
                Breakout strategy, weekly scans (3 picks/scan), EMA21 trail,
                <b> no</b> half-booking, <b>no</b> R-ladder ratchet — just breakeven
                at +1R then trail. Best return/drawdown found so far and the only
                config to win both the FIT(2016–20) and TEST(2021–26) halves.
                Full window (2016–2026): ₹315,597 total P&amp;L on ₹4L capital,
                ₹51,203 max drawdown, ret/DD 6.16, 34.1% win rate, 1,287 trades.
              </div>
            </div>
            <button type="button"
              onClick={() => { setF((s0) => ({ ...s0, ...PRESETS.validated.values })); setAdvanced(true); }}
              className="shrink-0 text-xs px-3 py-1.5 rounded bg-sky-700 hover:bg-sky-600 text-white font-medium">
              Load validated config
            </button>
          </div>
          <div className="text-[11px] text-amber-300/80 leading-snug">
            ⚠ Not yet live-traded and not yet re-tested on production's actual
            daily×3 cadence (this was validated on weekly×3). Magnitude may be
            smaller than 6.16 in practice — see BACKTEST_REPORT §9.19 &ldquo;What
            this does NOT establish.&rdquo; Loading it opens the advanced panel
            below so every field is visible and editable before you run it.
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 pt-1">
        <button type="button" onClick={() => setAdvanced((v) => !v)}
          className="text-xs px-2.5 py-1 rounded border border-slate-600 bg-slate-800 hover:bg-slate-700 text-slate-200">
          {advanced ? '▾ Hide advanced / research settings' : '▸ Advanced / research settings'}
        </button>
        {!advanced && (
          <span className="text-[11px] text-slate-500">
            Other strategies, rejected overlays and cost model live in here
          </span>
        )}
      </div>

      {advanced && (<>
      <div className="flex flex-wrap items-center gap-2 pb-1">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">Start from</span>
        {Object.entries(PRESETS).map(([key, p]) => (
          <button key={key} type="button" title={p.hint}
            onClick={() => setF((s0) => ({ ...s0, ...p.values }))}
            className="text-xs px-2.5 py-1 rounded border border-slate-600 bg-slate-800 hover:bg-slate-700 text-slate-200">
            {p.label}
          </button>
        ))}
        <span className="text-[11px] text-slate-500">then tweak below</span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Start date
          <input type="date" value={f.start_date} onChange={(e) => set('start_date')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" required />
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          End date
          <input type="date" value={f.end_date} onChange={(e) => set('end_date')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" required />
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Strategy
          <select value={f.strategy} onChange={(e) => set('strategy')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
            <option value="BREAKOUT">Breakout (swing) — not allocated</option>
            <option value="POSITIONAL">Positional momentum (annual-reset)</option>
            <option value="PORTFOLIO">Portfolio (continuous, compounding)</option>
            <option value="WEEKLY_BREAKOUT">Weekly consolidation breakout</option>
          </select>
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Capital (₹)
          <input type="number" value={f.capital} onChange={(e) => set('capital')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" min="10000" step="10000" />
        </label>
      </div>

      {f.strategy === 'PORTFOLIO' && (
        <div className="pt-3 border-t border-slate-800">
          <div className="text-xs font-semibold text-emerald-400 uppercase tracking-wide mb-1">
            Continuous portfolio
          </div>
          <p className="text-[11px] text-slate-500 mb-2 leading-snug">
            ONE simulation start to finish: capital compounds, positions are held
            across year ends, the book is marked to market daily. This is not the
            same measurement as the other strategies, which run a separate backtest
            per year and add the P&amp;L — that framing resets capital every January
            and cannot show a drawdown that spans a year boundary.
            Results are reported as <b>CAGR, max drawdown, ulcer index and worst
            rolling 12-month return</b>, not as a P&amp;L total.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
            <label className="text-xs text-slate-400 flex flex-col gap-1">
              <span>Momentum lookback</span>
              <select value={f.pos_momentum} onChange={(e) => set('pos_momentum')(e.target.value)}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
                <option value="pct_chg_3m">3 months</option>
                <option value="pct_chg_6m">6 months (tested)</option>
                <option value="pct_chg_1y">12 months</option>
              </select>
            </label>
            <Field label="Rebalance every (sessions)" hint="63 ≈ quarterly."
              value={f.pos_rebalance_days} onChange={set('pos_rebalance_days')}
              type="number" min="1" max="250" />
            <Field label="Hold top N"
              hint="Provisional 30–40. The direction (more names → less drawdown) held out of sample; no specific value did. top-30 was best in-sample and ranked 6th of 10 out of sample."
              value={f.pos_top_n} onChange={set('pos_top_n')} type="number" min="1" max="60" />
            <Field label="Sell below rank (buffer)" hint="Anti-churn hysteresis; 2x top-N."
              value={f.pos_buffer_n} onChange={set('pos_buffer_n')} type="number" min="1" max="120" />
            <Field label="Fixed stop (%)"
              hint="Supported RANGE is 15–20%. 10% was rejected out-of-sample. 0 disables."
              value={f.pos_sl_pct} onChange={set('pos_sl_pct')}
              type="number" min="0" max="50" step="0.5" />
            <Field label="Min turnover (₹ cr)" hint="Liquidity floor."
              value={f.pos_min_turnover_cr} onChange={set('pos_min_turnover_cr')}
              type="number" min="0" step="0.5" />
          </div>

          <div className="mt-4 pt-3 border-t border-slate-800">
            <div className="text-xs font-semibold text-amber-400 uppercase tracking-wide mb-1">
              Risk controls — all measured, most rejected
            </div>
            <p className="text-[11px] text-slate-500 mb-2 leading-snug">
              These default to OFF because that is what testing supported, not as a
              placeholder. Volatility scaling cost 5.4pp of CAGR for <i>zero</i>
              {' '}drawdown reduction; the drawdown throttle scored worse than doing
              nothing at every threshold and made the ulcer index <i>worse</i> — it
              cuts exposure after the loss and restores it after the recovery.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
              <label className="text-xs text-slate-400 flex flex-col gap-1">
                <span>Volatility scaling</span>
                <select value={f.pf_vol_mode} onChange={(e) => set('pf_vol_mode')(e.target.value)}
                  className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
                  <option value="none">Off (tested best)</option>
                  <option value="pct">On — percentile of the book&apos;s own past vol</option>
                  <option value="abs">On — absolute vol bands</option>
                </select>
                <span className="text-[11px] text-slate-500 leading-snug">
                  Cuts the number of slots held, so reducing exposure raises cash
                  rather than concentrating the book.
                </span>
              </label>
              {f.pf_vol_mode !== 'none' && (
                <Field label="Exposure floor (%)"
                  hint="Lowest exposure allowed. Only mild floors (≥75%) ever helped."
                  value={f.pf_vol_floor} onChange={set('pf_vol_floor')}
                  type="number" min="10" max="100" step="5" />
              )}
              <Field label="Drawdown throttle at (0 = off)"
                hint="e.g. 0.10 halves new exposure past −10%. Net negative at every value tested."
                value={f.pf_dd_throttle_at} onChange={set('pf_dd_throttle_at')}
                type="number" min="0" max="0.5" step="0.01" />
              <Field label="Max stocks per sector (99 = off)"
                hint="2 costs more than it buys; 3 is roughly neutral."
                value={f.pf_max_stocks_per_sector} onChange={set('pf_max_stocks_per_sector')}
                type="number" min="1" max="99" />
              <Field label="Max % per sector"
                value={f.pf_max_per_sector_pct} onChange={set('pf_max_per_sector_pct')}
                type="number" min="1" max="100" step="5" />
              <Field label="Max % per stock"
                hint="Near-inert at top-20+ where a slot is already ≤5%."
                value={f.pf_max_per_stock_pct} onChange={set('pf_max_per_stock_pct')}
                type="number" min="1" max="100" step="1" />
            </div>
            <label className="flex items-start gap-2 mt-3 text-xs text-slate-400">
              <input type="checkbox" checked={!!f.pf_require_sector}
                onChange={(e) => set('pf_require_sector')(e.target.checked)}
                className="mt-0.5" />
              <span>
                Restrict universe to stocks with a known sector
                <span className="block text-[11px] text-rose-400/90 leading-snug mt-0.5">
                  ⚠ Do not trust results from this. Sector data exists only for
                  <i> current</i> NSE index members, so this filters the 2016
                  universe by &ldquo;was in an index in 2026&rdquo; — selecting
                  winners with a decade of hindsight. It posts the best numbers in
                  the whole project and they are survivorship artifacts.
                </span>
              </span>
            </label>
          </div>
          <div className="text-[11px] text-amber-300/80 mt-3 leading-snug">
            Survivorship bias across the whole dataset is still unquantified, so
            treat every figure here as <b>observed on this data</b>, not as expected
            forward performance.
          </div>
        </div>
      )}

      {f.strategy === 'WEEKLY_BREAKOUT' && (
        <div className="pt-3 border-t border-slate-800">
          <div className="text-xs font-semibold text-emerald-400 uppercase tracking-wide mb-1">
            Weekly consolidation breakout
          </div>
          <p className="text-[11px] text-slate-500 mb-2 leading-snug">
            Weekly candles only. Entry needs: price above a RISING 20-week SMA,
            weekly MACD(12,26,9) line above signal, a 6-26 week consolidation
            box (body-based, depth ≤35%) breaking out 5-20% above resistance on
            expanded volume, upper wick &lt;50%, and a fresh 10-week closing
            high. Exit trails via MACD bearish-crossover (stop ratchets to the
            crossover week&apos;s low, closes on a later breach). Market-cap
            filter is substituted with a liquidity floor — no ₹ market-cap data
            exists for a 2016-2026 backtest without look-ahead bias (see
            weekly_breakout.py docstring).
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
            <Field label="Account risk % per trade"
              hint="Position size = Equity × risk% / stop-distance%. Spec suggests 1-1.5%."
              value={f.weekly_risk_pct} onChange={set('weekly_risk_pct')}
              type="number" min="0.1" max="10" step="0.1" />
            <Field label="Max new picks per week"
              value={f.max_picks_per_track} onChange={set('max_picks_per_track')}
              type="number" min="1" max="10" />
            <Field label="Entry order resting window (weeks)"
              hint="Buy-stop above breakout week's close expires unfilled after this many weeks."
              value={f.resting_window_days} onChange={set('resting_window_days')}
              type="number" min="1" max="12" />
          </div>
          <label className="flex items-start gap-2 mt-3 text-xs text-slate-400">
            <input type="checkbox" checked={!!f.stacking_guard}
              onChange={(e) => set('stacking_guard')(e.target.checked)}
              className="mt-0.5" />
            <span>Block a new signal in a symbol already held (stacking guard)</span>
          </label>
        </div>
      )}

      {f.strategy === 'POSITIONAL' && (
        <div className="pt-3 border-t border-slate-800">
          <div className="text-xs font-semibold text-emerald-400 uppercase tracking-wide mb-1">
            Positional momentum
          </div>
          <p className="text-[11px] text-slate-500 mb-2 leading-snug">
            Holds the top-ranked momentum names above their 200SMA, rebalanced monthly.
            Sells only when a name drops outside the buffer rank — the gap between
            &ldquo;hold&rdquo; and &ldquo;buffer&rdquo; is deliberate hysteresis that stops a name
            oscillating around the cutoff from churning every rebalance.
            Measured: ~43 trades/yr held ~66 days, vs ~118 held ~14 for breakout.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
            <label className="text-xs text-slate-400 flex flex-col gap-1">
              <span>Momentum lookback</span>
              <select value={f.pos_momentum} onChange={(e) => set('pos_momentum')(e.target.value)}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
                <option value="pct_chg_3m">3 months</option>
                <option value="pct_chg_6m">6 months (tested)</option>
                <option value="pct_chg_1y">12 months</option>
              </select>
            </label>
            <Field label="Rebalance every (sessions)"
              hint="21 ≈ monthly. Lower = more turnover = more cost drag."
              value={f.pos_rebalance_days} onChange={set('pos_rebalance_days')}
              type="number" min="1" max="250" />
            <Field label="Hold top N" hint="Equal-weighted; each position is capital/N."
              value={f.pos_top_n} onChange={set('pos_top_n')} type="number" min="1" max="50" />
            <Field label="Sell below rank (buffer)"
              hint="Must exceed 'hold top N'. This gap is the anti-churn hysteresis."
              value={f.pos_buffer_n} onChange={set('pos_buffer_n')} type="number" min="1" max="100" />
            <Field label="Min turnover (₹ cr)" hint="Liquidity floor."
              value={f.pos_min_turnover_cr} onChange={set('pos_min_turnover_cr')}
              type="number" min="0" step="0.5" />
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800">
            <div className="text-xs font-semibold text-emerald-400 uppercase tracking-wide mb-1">
              Stop-loss
            </div>
            <p className="text-[11px] text-slate-500 mb-2 leading-snug">
              Checked on <b>every session</b>, not just rebalance days. A stopped slot
              stays in cash until the next rebalance rather than refilling immediately —
              refilling would re-buy from the same ranking that just stopped a name out.
              With no stop this book drew down ~42%; a fixed 15% stop cut that to ~33%
              while <i>raising</i> total return, which is why it is the preset default.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
              <label className="text-xs text-slate-400 flex flex-col gap-1">
                <span>Stop type</span>
                <select value={f.pos_sl_mode} onChange={(e) => set('pos_sl_mode')(e.target.value)}
                  className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
                  {POS_SL_MODES.map((m) => (
                    <option key={m.v} value={m.v}>{m.label}</option>
                  ))}
                </select>
                <span className="text-[11px] text-slate-500 leading-snug">
                  The four structural stops are one mechanism at four speeds. EMA21 exits
                  earliest and most often; SMA200 is so slow it barely differs from no stop.
                </span>
              </label>
              {POS_SL_NEEDS_PCT.has(f.pos_sl_mode) && (
                <Field label="Stop distance (%)"
                  hint={f.pos_sl_mode === 'trail'
                    ? 'Below the highest close since entry — ratchets up, never down.'
                    : 'Below the entry fill price — fixed for the life of the trade.'}
                  value={f.pos_sl_pct} onChange={set('pos_sl_pct')}
                  type="number" min="1" max="50" step="0.5" />
              )}
            </div>
          </div>
          <div className="text-[11px] text-amber-300/80 mt-3 leading-snug">
            Note: this runs ~100% deployed, so its swings are much larger than the
            breakout book&rsquo;s — measured calendar years include +95% and −34%.
          </div>
        </div>
      )}

      {f.strategy === 'BREAKOUT' && (<>
      {/* ---- The four settings that actually moved the needle in testing ---- */}
      <div className="pt-3 border-t border-slate-800">
        <div className="text-xs font-semibold text-emerald-400 uppercase tracking-wide mb-2">
          Edges — validated on both 2025 &amp; 2026 windows
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
          <Field label="Max base stage" hint="2 = only fresh 1st/2nd bases. Best on both windows; 4 = production."
            value={f.stage2_base_stage_max_allowed} onChange={set('stage2_base_stage_max_allowed')}
            type="number" min="1" max="6" placeholder="blank = production (4)" />
          <Field label="Skip entries above breadth %" hint="Don't buy when this much of the market is already above its 200SMA. 40 tested best."
            value={f.entry_breadth_max_pct} onChange={set('entry_breadth_max_pct')}
            type="number" min="5" max="100" step="1" placeholder="blank = no filter" />
          <Field label="Max contraction ratio (VCP)" hint="range(last 10 bars)/range(prior 15). ≤0.7 = base is tightening into the pivot."
            value={f.max_contraction_ratio} onChange={set('max_contraction_ratio')}
            type="number" min="0.2" max="2" step="0.05" placeholder="blank = no filter" />
          <Field label="Risk per trade %" hint="Production is 0.25% (very conservative). 1.0% scaled returns more than drawdown."
            value={f.risk_per_trade_pct} onChange={set('risk_per_trade_pct')}
            type="number" min="0.05" max="3" step="0.05" placeholder="blank = production (0.25)" />
          <div className="sm:col-span-2">
            <Toggle label="Only enter while breadth is rising" checked={f.entry_breadth_require_rising}
              onChange={set('entry_breadth_require_rising')}
              hint="Breadth ≥ its own 20-session average — buy early in a recovery, not into a decline. Biggest single drawdown reducer found." />
          </div>
        </div>
      </div>

      {/* ---- Core, frequently-changed knobs ---- */}
      <div className="pt-3 border-t border-slate-800 grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Position &amp; entry</div>
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Scan cadence
            <select value={f.signal_cadence} onChange={(e) => set('signal_cadence')(e.target.value)}
              className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm">
              <option value="daily">Daily (production)</option>
              <option value="weekly">Weekly (validated exit-ladder-fix preset)</option>
              <option value="monthly">Monthly</option>
            </select>
          </label>
          {f.signal_cadence !== 'daily' && (
            <label className="text-xs text-slate-400 flex items-center gap-2 ml-6">
              Scan on
              <select value={f.signal_scan_day} onChange={(e) => set('signal_scan_day')(e.target.value)}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm">
                <option value="last">Last session of period</option>
                <option value="first">First session of period</option>
              </select>
            </label>
          )}
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Picks per scan
            <input type="number" min="1" max="10" value={f.max_picks_per_track}
              onChange={(e) => set('max_picks_per_track')(e.target.value)}
              className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
            <span className="text-slate-500">(2 daily / 3 weekly tested best)</span>
          </label>
          <Toggle label="Fill at next session's open" checked={f.next_open_exit}
            onChange={set('next_open_exit')}
            hint="What production actually does — sl_engine.py runs at 18:00 IST after Dhan stops accepting market orders. Close-fill modelling understates production by ~₹90k/decade." />
          <label className="text-xs text-slate-400 flex items-center gap-2">
            Safety SL floor
            <input type="number" min="1" max="30" step="0.5" value={f.safety_sl_pct}
              onChange={(e) => set('safety_sl_pct')(e.target.value)}
              className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
            % below entry
          </label>
          <Toggle label="Rest indefinitely until window ends" checked={f.restIndefinite}
            onChange={set('restIndefinite')} />
          {!f.restIndefinite && (
            <label className="text-xs text-slate-400 flex items-center gap-2 ml-6">
              Expire after
              <input type="number" min="1" value={f.resting_window_days}
                onChange={(e) => set('resting_window_days')(e.target.value)}
                className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
              days unfilled
            </label>
          )}
          <Toggle label="Position-stacking guard" checked={f.stacking_guard}
            onChange={set('stacking_guard')}
            hint="Never stack a second buy into a symbol already held." />
          {f.stacking_guard && (
            <label className="text-xs text-slate-400 flex items-center gap-2 ml-6">
              If already PENDING
              <select value={f.stacking_guard_mode} onChange={(e) => set('stacking_guard_mode')(e.target.value)}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm">
                <option value="SKIP">Skip new pick</option>
                <option value="OVERRIDE">Override with new order</option>
              </select>
            </label>
          )}
        </div>

        <div className="space-y-2">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Exits</div>
          <Toggle label="Breakeven move at +1R" checked={f.breakeven} onChange={set('breakeven')} />
          <Toggle label="Half-book + trail rest at +2R" checked={f.half_booking} onChange={set('half_booking')} />
          <Toggle label="Trailing stop ladder (R-based)" checked={f.trailing} onChange={set('trailing')} />
          <Toggle label="EMA21 trail" checked={f.ema21_trail} onChange={set('ema21_trail')}
            hint="Beat the pure R-ladder on both windows — recommended on." />
          <Toggle label="Fixed target exit (2R)" checked={f.fixed_target} onChange={set('fixed_target')}
            hint="Caps winners; testing favoured leaving this off." />
        </div>
      </div>

      {/* ---- New, not yet A/B tested — borrowed from WEEKLY_BREAKOUT (run #589 analysis) ---- */}
      <div className="pt-3 border-t border-slate-800">
        <div className="text-xs font-semibold text-amber-400 uppercase tracking-wide mb-2">
          New — untested, from the weekly-strategy comparison
        </div>
        <p className="text-[11px] text-slate-500 mb-2 leading-snug">
          Run #589 found the weekly strategy's MACD-crossover trail let winners
          run to +2.09R on average vs +1.15R for the daily EMA21 trail, and its
          box+volume-expansion breakout definition is coarser/less noisy than
          the daily funnel's own gates. Both borrowed here as opt-in toggles —
          not yet measured against the validated baseline above.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
          <Toggle label="MACD trail (weekly)" checked={f.macd_trail} onChange={set('macd_trail')}
            hint="SL ratchets to the low of the most recent weekly bearish-MACD-crossover — slower/coarser than EMA trails." />
          <Toggle label="Require recent weekly box breakout" checked={f.require_weekly_box_breakout}
            onChange={set('require_weekly_box_breakout')}
            hint="Only enter if the symbol also had a WEEKLY_BREAKOUT-style box breakout recently." />
          {f.require_weekly_box_breakout && (
            <Field label="Lookback window (days)"
              value={f.weekly_box_lookback_days} onChange={set('weekly_box_lookback_days')}
              type="number" min="1" max="60" />
          )}
        </div>
      </div>

      {/* ---- Everything that tested neutral-or-worse, tucked away ---- */}
      <details className="pt-2 border-t border-slate-800">
        <summary className="cursor-pointer text-xs font-semibold text-slate-400 uppercase tracking-wide py-2 hover:text-slate-200">
          Advanced / experimental — tested neutral or worse
        </summary>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
          <div className="space-y-2">
            <div className="text-[11px] text-slate-500">Alternative trails (EMA21 above outperformed these)</div>
            <Toggle label="EMA10 trail" checked={f.ema10_trail} onChange={set('ema10_trail')} />
            <Toggle label="EMA50 trail" checked={f.ema50_trail} onChange={set('ema50_trail')} />
            <Toggle label="Chandelier trail (ATR)" checked={f.chandelier_trail} onChange={set('chandelier_trail')}
              hint="Tested identical to EMA21 — no measurable gain." />
            {f.chandelier_trail && (
              <label className="text-xs text-slate-400 flex items-center gap-2 ml-6">
                ATR multiple
                <input type="number" min="1" max="8" step="0.5" value={f.chandelier_atr_mult}
                  onChange={(e) => set('chandelier_atr_mult')(e.target.value)}
                  className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
              </label>
            )}
            <Toggle label="Swing-low trail" checked={f.swing_trail} onChange={set('swing_trail')} />
            <Toggle label="Failed-breakout exit" checked={f.failed_breakout_exit} onChange={set('failed_breakout_exit')}
              hint="Helps a bad regime, but gave up ~65% of the good regime's gains." />
            <Toggle label="Swing-low break exit" checked={f.swing_break_exit} onChange={set('swing_break_exit')} />
          </div>
          <div className="space-y-2">
            <div className="text-[11px] text-slate-500">Entry gate — REJECTED, kept for A/B reference only</div>
            <Toggle label="Buy-point gate (pullback/H&S/breakout/retest)" checked={f.entry_v2_buy_points}
              onChange={set('entry_v2_buy_points')}
              hint="Tested and rejected: daily ₹194k→₹102k, weekly ₹99k→₹37k, worse win rate and drawdown both times. The gate selects stocks already at breakout points, which fills MORE trades at worse prices." />
            <label className="text-xs text-slate-400 flex items-center gap-2">
              Base-stage sizing ladder
              <select value={f.base_stage_ladder} onChange={(e) => set('base_stage_ladder')(e.target.value)}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm">
                <option value="prod">Production (base-stage multiplier only)</option>
                <option value="v2">v2 ladder (1.00/0.75/0.50/0.25×)</option>
              </select>
              <span className="text-[11px] text-slate-500 leading-snug ml-1">Cost 18% of return for 9% less drawdown — worse than proportional.</span>
            </label>
          </div>
          <div className="space-y-2">
            <div className="text-[11px] text-slate-500">Sizing &amp; cost realism</div>
            <Field label="Max capital per trade %" hint="Production 10%."
              value={f.max_capital_per_trade_pct} onChange={set('max_capital_per_trade_pct')}
              type="number" min="1" max="50" step="1" placeholder="blank = production (10)" />
            <Field label="Min position value ₹" hint="Skip positions too small to absorb flat costs. Tested: hurt returns."
              value={f.min_position_value} onChange={set('min_position_value')}
              type="number" min="0" step="1000" placeholder="0 = off" />
            <label className="text-xs text-slate-400 flex items-center gap-2">
              Slippage
              <input type="number" min="0" max="2" step="0.01" value={f.slippage_pct}
                onChange={(e) => set('slippage_pct')(e.target.value)}
                className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
              % per fill
            </label>
            <label className="text-xs text-slate-400 flex items-center gap-2">
              Brokerage override
              <input type="number" min="0" step="1" value={f.brokerage_per_order}
                onChange={(e) => set('brokerage_per_order')(e.target.value)}
                className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
              ₹/order
            </label>
            <div className="text-[11px] text-slate-500">
              STT, stamp duty, exchange/SEBI charges and the ₹14.75 DP charge are always
              applied automatically (Dhan delivery: ₹0 brokerage).
            </div>
          </div>
        </div>
      </details>

      </>)}
      </>)}

      {/* Notes and the error/blocked banners sit OUTSIDE the advanced block on
          purpose: a validation error hidden inside a collapsed section is an
          error the user never sees. */}
      <label className="text-xs text-slate-400 flex flex-col gap-1">
        Notes (optional)
        <input type="text" value={f.notes} onChange={(e) => set('notes')(e.target.value)}
          placeholder="e.g. frozen config, full window"
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" />
      </label>

      {error && <div className="bg-red-900/40 border border-red-700 text-red-200 text-sm rounded px-3 py-2">{error}</div>}
      {blocked && !error && (
        <div className="bg-amber-900/30 border border-amber-700 text-amber-200 text-sm rounded px-3 py-2">{blockedReason}</div>
      )}

      <button type="submit" disabled={submitting || blocked}
        className="px-4 py-2 text-sm rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-semibold">
        {submitting ? 'Starting…' : blocked ? 'Run in progress…' : 'Run backtest'}
      </button>
    </form>
  );
}

// ---------------- Run list ----------------

// Run-list window column. Keeps the year (runs now span 2024/2025/2026, so
// dropping it made rows ambiguous) but trims the century: "2026-01-01" ->
// "26-01-01".
const fmtWindowShort = (s, e) => `${s?.slice(2) ?? ''} → ${e?.slice(2) ?? ''}`;

/** One label/value pair inside a mobile run card. */
function MobileStat({ label, value, tone }) {
  const cls = tone == null ? 'text-slate-200'
    : tone > 0 ? 'text-emerald-300' : tone < 0 ? 'text-rose-300' : 'text-slate-200';
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${cls}`}>{value}</div>
    </div>
  );
}

// Compact signed rupee for the P&L columns: 38856.71 -> "+38.9k", -3200 -> "-3.2k"
const fmtPnl = (v) => {
  if (v == null) return '—';
  const a = Math.abs(v);
  const s0 = v < 0 ? '-' : '+';
  if (a >= 1e5) return `${s0}${(a / 1e5).toFixed(2)}L`;
  if (a >= 1e3) return `${s0}${(a / 1e3).toFixed(1)}k`;
  return `${s0}${Math.round(a)}`;
};

function RunRow({ run, selected, onSelect, onCancel, cancelling, rowRef, onKeyDown }) {
  const pct = run.progressTotalDays ? Math.round((run.progressDay / run.progressTotalDays) * 100) : null;
  return (
    <tr ref={rowRef} tabIndex={0} onKeyDown={onKeyDown} onClick={() => onSelect(run.id)}
      className={`cursor-pointer border-t border-slate-800 hover:bg-slate-800/40 outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-emerald-500 ${selected ? 'bg-slate-800/60' : ''}`}>
      <td className="py-1.5 px-2 text-xs text-slate-200 whitespace-nowrap">#{run.id}</td>
      <td className="py-1.5 px-2 text-xs text-slate-300 whitespace-nowrap" title={`${run.startDate} → ${run.endDate}`}>
        {fmtWindowShort(run.startDate, run.endDate)}
      </td>
      <td className="py-1.5 px-2 text-xs text-slate-300 whitespace-nowrap">{run.trackMode}</td>
      <td className="py-1.5 px-2 text-xs text-slate-300 whitespace-nowrap">{fmtInrCompact(run.capital)}</td>
      <td className={`py-1.5 px-2 text-xs font-semibold whitespace-nowrap ${STATUS_COLOR[run.status] || 'text-slate-300'}`}>
        {run.status}
        {run.status === 'RUNNING' && pct != null && <span className="text-slate-400 font-normal"> · {pct}%</span>}
      </td>
      <td className="py-1.5 px-2 text-xs text-slate-300 whitespace-nowrap">{run.tradeCount ?? '—'}</td>
      {/* Realized / unrealized / total apply to EVERY strategy — a portfolio run
          still has banked P&L and open positions, and hiding them (as an earlier
          version did, substituting the path metrics in their place) meant the
          rupee outcome of a portfolio run could not be seen at all. */}
      <td className={`py-1.5 px-2 text-xs font-medium whitespace-nowrap tabular-nums ${pnlColor(run.realizedPnl)}`}
        title={run.realizedPnl != null ? `Realized ₹${run.realizedPnl.toLocaleString('en-IN')}` : undefined}>
        {fmtPnl(run.realizedPnl)}
      </td>
      <td className={`py-1.5 px-2 text-xs whitespace-nowrap tabular-nums ${pnlColor(run.unrealizedPnl)}`}
        title={run.unrealizedPnl != null ? `Unrealized (open positions marked to run end) ₹${run.unrealizedPnl.toLocaleString('en-IN')}` : undefined}>
        {fmtPnl(run.unrealizedPnl)}
      </td>
      <td className={`py-1.5 px-2 text-xs font-semibold whitespace-nowrap tabular-nums ${pnlColor(run.totalPnl)}`}
        title={run.totalPnl != null ? `Total ₹${run.totalPnl.toLocaleString('en-IN')}` : undefined}>
        {fmtPnl(run.totalPnl)}
      </td>
      {/* Path metrics — NULL on non-PORTFOLIO runs, shown as em-dash rather than
          0 so an empty cell is never mistaken for a measured zero. */}
      <td className={`py-1.5 px-2 text-xs font-medium whitespace-nowrap tabular-nums ${
            isShortWindow(run) ? 'text-slate-500 italic' : 'text-emerald-300'}`}
        title={isShortWindow(run)
          ? 'Annualised from a window under 2 years — not comparable with a continuous run'
          : 'Compound annual growth rate'}>
        {run.pfCagrPct != null
          ? `${run.pfCagrPct.toFixed(1)}%${isShortWindow(run) ? '*' : ''}`
          : '—'}
      </td>
      <td className="py-1.5 px-2 text-xs whitespace-nowrap tabular-nums text-rose-300"
        title="Peak-to-trough drawdown on the daily equity curve">
        {run.pfMaxDDPct != null ? `−${run.pfMaxDDPct.toFixed(1)}%` : '—'}
      </td>
      <td className="py-1.5 px-2 text-xs whitespace-nowrap tabular-nums text-amber-300/90"
        title="Worst return over any rolling 252-session window">
        {run.pfWorst12mPct != null ? `${run.pfWorst12mPct.toFixed(1)}%` : '—'}
      </td>
      <td className="py-1.5 px-2 text-xs font-semibold whitespace-nowrap tabular-nums text-slate-200"
        title="Martin ratio = CAGR / ulcer index. Return per unit of time-weighted pain.">
        {run.pfMartin != null ? run.pfMartin.toFixed(2) : '—'}
      </td>
      <SettingsCell run={run} />
      <td className="py-1.5 px-2 text-xs">
        {run.status === 'RUNNING' && (
          <button onClick={(e) => { e.stopPropagation(); onCancel(run.id); }} disabled={cancelling}
            className="px-1.5 py-0.5 text-[11px] rounded bg-red-900/60 border border-red-700 text-red-200 hover:bg-red-900 disabled:opacity-50">
            {cancelling ? '…' : 'Stop'}
          </button>
        )}
      </td>
    </tr>
  );
}

const SORTS = {
  newest:    { label: 'Newest first',   fn: (a, b) => b.id - a.id },
  oldest:    { label: 'Oldest first',   fn: (a, b) => a.id - b.id },
  totalDesc: { label: 'Total P&L ↓',    fn: (a, b) => (b.totalPnl ?? -Infinity) - (a.totalPnl ?? -Infinity) },
  totalAsc:  { label: 'Total P&L ↑',    fn: (a, b) => (a.totalPnl ?? Infinity) - (b.totalPnl ?? Infinity) },
  realDesc:  { label: 'Realized P&L ↓', fn: (a, b) => (b.realizedPnl ?? -Infinity) - (a.realizedPnl ?? -Infinity) },
  tradesDesc:{ label: 'Trades ↓',       fn: (a, b) => (b.tradeCount ?? 0) - (a.tradeCount ?? 0) },
};

function RunList({ runs, selectedId, onSelect, onCancel, cancellingId }) {
  const isMobile = useIsMobile();
  const rowRefs = useRef({});
  const [sortKey, setSortKey] = useState('newest');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [trackFilter, setTrackFilter] = useState('ALL');
  const [query, setQuery] = useState('');
  const [winnersOnly, setWinnersOnly] = useState(false);

  // Filter first, then sort — so the arrow-key index below always refers to
  // the list actually rendered.
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return runs
      .filter((r) => statusFilter === 'ALL' || r.status === statusFilter)
      .filter((r) => trackFilter === 'ALL' || r.trackMode === trackFilter)
      .filter((r) => !winnersOnly || (r.totalPnl ?? 0) > 0)
      .filter((r) => {
        if (!q) return true;
        const hay = [r.params?.notes, `#${r.id}`, r.startDate, r.endDate,
          ...summarizeRunSettings(r)].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
      })
      .sort(SORTS[sortKey].fn);
  }, [runs, sortKey, statusFilter, trackFilter, query, winnersOnly]);

  const move = (idx, delta) => {
    const target = visible[idx + delta];
    if (!target) return;
    onSelect(target.id);
    rowRefs.current[target.id]?.focus();
  };
  const handleKeyDown = (e, idx) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(idx, 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(idx, -1); }
  };

  const selCls = 'bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-200 text-xs';

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search notes / settings / #id…"
          className="flex-1 min-w-[180px] bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-xs" />
        <select value={sortKey} onChange={(e) => setSortKey(e.target.value)} className={selCls} title="Sort">
          {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={selCls} title="Status">
          <option value="ALL">All status</option>
          <option value="COMPLETED">Completed</option>
          <option value="RUNNING">Running</option>
          <option value="FAILED">Failed</option>
        </select>
        <select value={trackFilter} onChange={(e) => setTrackFilter(e.target.value)} className={selCls} title="Track">
          <option value="ALL">All tracks</option>
          <option value="QUANT">Quant</option>
          <option value="AI">AI</option>
          <option value="BOTH">Both</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
          <input type="checkbox" checked={winnersOnly} onChange={(e) => setWinnersOnly(e.target.checked)}
            className="accent-emerald-500" />
          Profitable only
        </label>
        {visible.length !== runs.length && (
          <span className="text-[11px] text-slate-500">{visible.length}/{runs.length}</span>
        )}
      </div>

      {!runs.length ? (
        <div className="text-sm text-slate-400 px-1">No backtest runs yet — configure one above.</div>
      ) : !visible.length ? (
        <div className="text-sm text-slate-400 px-1">No runs match these filters.</div>
      ) : isMobile ? (
        /* A 15-column table does not become usable on a phone by scrolling
           sideways — the columns you need are always the ones off-screen. Each
           run becomes a card with the four numbers that decide whether to open
           it, and the rest lives in the detail view. */
        <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-0.5">
          {visible.map((r) => {
            const sel = r.id === selectedId;
            const isPf = r.strategy === 'PORTFOLIO';
            return (
              <button key={r.id} type="button" onClick={() => onSelect(r.id)}
                className={`w-full text-left rounded-lg border p-3 transition-colors
                  ${sel ? 'border-sky-500 bg-sky-950/30' : 'border-slate-700 bg-slate-900/60 active:bg-slate-800'}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-slate-100">
                      #{r.id}
                      <span className="ml-2 text-[11px] font-normal text-slate-400">
                        {fmtWindowShort(r.startDate, r.endDate)}
                      </span>
                    </div>
                    {r.params?.notes && (
                      <div className="text-[11px] text-slate-400 truncate mt-0.5">{r.params.notes}</div>
                    )}
                  </div>
                  <Pill tone={r.status === 'COMPLETED' ? 'good' : r.status === 'FAILED' ? 'bad'
                    : r.status === 'RUNNING' ? 'info' : 'slate'}>
                    {r.status}
                  </Pill>
                </div>

                <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 mt-2.5">
                  <MobileStat label="Total P&L" value={fmtPnl(r.totalPnl)} tone={r.totalPnl} />
                  <MobileStat label="Trades" value={r.tradeCount ?? '—'} />
                  {isPf ? (<>
                    <MobileStat label="CAGR"
                      value={r.pfCagrPct != null ? `${r.pfCagrPct.toFixed(1)}%${isShortWindow(r) ? '*' : ''}` : '—'}
                      tone={r.pfCagrPct} />
                    <MobileStat label="Max drawdown"
                      value={r.pfMaxDDPct != null ? `−${r.pfMaxDDPct.toFixed(1)}%` : '—'} tone={-1} />
                  </>) : (<>
                    <MobileStat label="Realized" value={fmtPnl(r.realizedPnl)} tone={r.realizedPnl} />
                    <MobileStat label="Unrealized" value={fmtPnl(r.unrealizedPnl)} tone={r.unrealizedPnl} />
                  </>)}
                </div>

                <div className="flex flex-wrap gap-1 mt-2">
                  {summarizeRunSettings(r).slice(0, 4).map((t, i) => (
                    <Pill key={i} tone={t.startsWith('⚠') ? 'warn' : 'slate'}>{t}</Pill>
                  ))}
                </div>
              </button>
            );
          })}
        </div>
      ) : (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg overflow-x-auto max-h-[480px] overflow-y-auto">
      <table className="w-full table-auto">
        <thead className="sticky top-0 bg-slate-900 z-10">
          <tr className="text-left text-[10px] text-slate-500 uppercase tracking-wide">
            <th className="py-2 px-2">Run</th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.window}>Window</LabelWithInfo></th>
            <th className="py-2 px-2">Track</th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.capital}>Capital</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.status}>Status</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.trades}>Trades</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.realized}>Realized</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.unrealized}>Unreal.</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.total}>Total</LabelWithInfo></th>
            <th className="py-2 px-2 text-emerald-400/80"><LabelWithInfo help={HELP.cagr}>CAGR</LabelWithInfo></th>
            <th className="py-2 px-2 text-rose-400/80"><LabelWithInfo help={HELP.maxDD}>maxDD</LabelWithInfo></th>
            <th className="py-2 px-2 text-amber-400/80"><LabelWithInfo help={HELP.worst12m}>w12m</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.martin} align="right">Martin</LabelWithInfo></th>
            <th className="py-2 px-2"><LabelWithInfo help={HELP.settings} align="right">Settings</LabelWithInfo></th>
            <th className="py-2 px-2"></th>
          </tr>
        </thead>
        <tbody>
          {visible.map((r, idx) => (
            <RunRow key={r.id} run={r} selected={r.id === selectedId} onSelect={onSelect}
              onCancel={onCancel} cancelling={cancellingId === r.id}
              rowRef={(el) => { rowRefs.current[r.id] = el; }}
              onKeyDown={(e) => handleKeyDown(e, idx)} />
          ))}
        </tbody>
      </table>
    </div>
      )}
    </div>
  );
}

// ---------------- Equity curve (lightweight inline SVG, no chart lib) ----------------

// Compact ₹ formatting for axis ticks — e.g. ₹1.2L, ₹45k, -₹2.1L.
const fmtInrCompact = (n) => {
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  if (abs >= 100000) return `${sign}₹${(abs / 100000).toFixed(abs >= 1000000 ? 0 : 1)}L`;
  if (abs >= 1000) return `${sign}₹${(abs / 1000).toFixed(1)}k`;
  return `${sign}₹${Math.round(abs)}`;
};
const fmtAxisDate = (d) => (d ? `${d.slice(8, 10)}-${d.slice(5, 7)}` : '');

// "Nice numbers for graph labels" (Heckbert) — picks a round step (1/2/5 x
// 10^n) instead of naively interpolating min..max, so ticks read as e.g.
// ₹0 / ₹20k / ₹40k rather than arbitrary fractions of whatever the data's
// min/max happened to be.
function _niceNum(range, round) {
  if (range <= 0) return 1;
  const exp = Math.floor(Math.log10(range));
  const frac = range / 10 ** exp;
  let niceFrac;
  if (round) {
    niceFrac = frac < 1.5 ? 1 : frac < 3 ? 2 : frac < 7 ? 5 : 10;
  } else {
    niceFrac = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  }
  return niceFrac * 10 ** exp;
}
function niceTicks(dataMin, dataMax, maxTicks = 5) {
  if (dataMin === dataMax) { dataMin -= 1; dataMax += 1; }
  const range = _niceNum(dataMax - dataMin, false);
  const step = _niceNum(range / (maxTicks - 1), true);
  const niceMin = Math.floor(dataMin / step) * step;
  const niceMax = Math.ceil(dataMax / step) * step;
  const ticks = [];
  for (let v = niceMin; v <= niceMax + step / 2; v += step) ticks.push(Math.round(v * 100) / 100);
  return { ticks, min: niceMin, max: niceMax };
}

const SERIES = [
  { key: 'quantRealizedCumPnl', label: 'Quant realized', color: '#38bdf8', dash: '0', group: 'realized' },
  { key: 'aiRealizedCumPnl', label: 'AI realized', color: '#c084fc', dash: '0', group: 'realized' },
  { key: 'quantUnrealizedPnl', label: 'Quant unrealized', color: '#38bdf8', dash: '4,3', group: 'unrealized' },
  { key: 'aiUnrealizedPnl', label: 'AI unrealized', color: '#c084fc', dash: '4,3', group: 'unrealized' },
];

// The account LEVEL over time, for continuous compounding runs, drawn from the
// engine's own daily mark-to-market rather than reconstructed from trade rows.
//
// This is a different quantity from the chart below it. EquityCurve plots
// cumulative realized P&L and unrealized as separate series against zero —
// flows. For a compounding book what matters is the level of the account and how
// far below its own high-water mark it goes, so this shades the drawdown.
function PortfolioEquity({ points, capital }) {
  if (!points?.length) {
    return <div className="text-sm text-slate-500 py-8 text-center">No equity data for this run.</div>;
  }
  const W = 760, H = 260;
  const M = { top: 28, right: 16, bottom: 30, left: 74 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;

  const vals = points.map((p) => p.equity);
  const { ticks: yTicks, min: yMin, max: yMax } =
    niceTicks(Math.min(capital, ...vals), Math.max(capital, ...vals), 5);
  const yRange = yMax - yMin || 1;
  const x = (i) => M.left + (i / Math.max(points.length - 1, 1)) * plotW;
  const y = (v) => M.top + plotH - ((v - yMin) / yRange) * plotH;

  // Running high-water mark, so the shaded band is the actual underwater period
  // rather than a guess from the single worst point.
  let peak = -Infinity;
  const peaks = vals.map((v) => (peak = Math.max(peak, v)));
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p.equity)}`).join(' ');
  const peakLine = peaks.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(v)}`).join(' ');
  const band = `${peakLine} L ${x(points.length - 1)} ${y(vals[vals.length - 1])} `
    + points.map((p, i) => `L ${x(points.length - 1 - i)} ${y(vals[points.length - 1 - i])}`).join(' ') + ' Z';

  const nTicks = Math.min(7, points.length);
  const tickIdx = Array.from({ length: nTicks }, (_, i) =>
    Math.round((i / Math.max(nTicks - 1, 1)) * (points.length - 1)));

  const final = vals[vals.length - 1];
  const maxDD = Math.max(...vals.map((v, i) => (peaks[i] - v) / peaks[i])) * 100;

  return (
    <div>
      <div className="flex flex-wrap gap-4 text-xs mb-2">
        <span className="text-slate-400">Start <b className="text-slate-200">{fmtInrCompact(capital)}</b></span>
        <span className="text-slate-400">End <b className="text-emerald-300">{fmtInrCompact(final)}</b></span>
        <span className="text-slate-400">Peak <b className="text-slate-200">{fmtInrCompact(Math.max(...vals))}</b></span>
        <span className="text-slate-400">Max drawdown <b className="text-rose-300">−{maxDD.toFixed(1)}%</b></span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-64">
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={M.left} y1={y(v)} x2={W - M.right} y2={y(v)} stroke="#334155" strokeWidth="1" strokeDasharray="3,3" />
            <text x={M.left - 8} y={y(v) + 3} fontSize="10" fill="#94a3b8" textAnchor="end">{fmtInrCompact(v)}</text>
          </g>
        ))}
        {/* starting capital — the line the book must stay above to have made money */}
        <line x1={M.left} y1={y(capital)} x2={W - M.right} y2={y(capital)} stroke="#64748b" strokeWidth="1.5" />
        <text x={M.left + 4} y={y(capital) - 4} fontSize="9" fill="#94a3b8">start</text>
        <path d={band} fill="#f43f5e" opacity="0.13" />
        <path d={peakLine} fill="none" stroke="#475569" strokeWidth="1" strokeDasharray="4,3" />
        <path d={line} fill="none" stroke="#34d399" strokeWidth="1.8" />
        {tickIdx.map((idx, i) => (
          <text key={i} x={x(idx)} y={H - 10} fontSize="9" fill="#94a3b8" textAnchor="middle">
            {String(points[idx].date).slice(0, 7)}
          </text>
        ))}
      </svg>
      <div className="text-[11px] text-slate-500 mt-1">
        Green = account equity. Dashed = running high-water mark. Shaded = drawdown.
        Sampled weekly.
      </div>
    </div>
  );
}

function EquityCurve({ points, capital }) {
  const [showRealized, setShowRealized] = useState(true);
  const [showUnrealized, setShowUnrealized] = useState(true);

  if (!points.length) return <div className="text-sm text-slate-500 py-8 text-center">No trade activity yet.</div>;

  const visible = SERIES.filter((s) =>
    (s.group === 'realized' && showRealized) || (s.group === 'unrealized' && showUnrealized));

  const W = 760, H = 260;
  const M = { top: 28, right: 16, bottom: 30, left: 68 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;

  const all = points.flatMap((p) => visible.map((s) => p[s.key])).filter((v) => v != null);
  const dataMin = Math.min(0, ...all, 0), dataMax = Math.max(0, ...all, 0);
  const { ticks: yTicks, min: yMin, max: yMax } = niceTicks(dataMin, dataMax, 5);
  const yRange = yMax - yMin || 1;

  const x = (i) => M.left + (i / Math.max(points.length - 1, 1)) * plotW;
  const y = (v) => M.top + plotH - ((v - yMin) / yRange) * plotH;
  const path = (key) => points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p[key] ?? 0)}`).join(' ');

  const xTickCount = Math.min(6, points.length);
  const xTickIdx = Array.from({ length: xTickCount }, (_, i) =>
    Math.round((i / Math.max(xTickCount - 1, 1)) * (points.length - 1)));

  return (
    <div>
      <div className="flex flex-wrap items-center gap-4 mb-2">
        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer select-none">
          <input type="checkbox" checked={showRealized} onChange={(e) => setShowRealized(e.target.checked)}
            className="accent-emerald-500" />
          Realized
        </label>
        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer select-none">
          <input type="checkbox" checked={showUnrealized} onChange={(e) => setShowUnrealized(e.target.checked)}
            className="accent-emerald-500" />
          Unrealized
        </label>
      </div>

      {!visible.length ? (
        <div className="text-sm text-slate-500 py-8 text-center">Toggle Realized or Unrealized to see the curve.</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-64">
          {/* Y gridlines + ₹ / % labels — values are "nice" round steps, not raw min/max fractions */}
          {yTicks.map((v, i) => (
            <g key={i}>
              <line x1={M.left} y1={y(v)} x2={W - M.right} y2={y(v)}
                stroke="#334155" strokeWidth="1" strokeDasharray={Math.abs(v) < 1e-6 ? '0' : '3,3'} />
              <text x={M.left - 8} y={y(v) + 3} fontSize="10" fill="#94a3b8" textAnchor="end">
                {fmtInrCompact(v)}
              </text>
              {capital ? (
                <text x={W - M.right + 4} y={y(v) + 3} fontSize="9" fill="#64748b" textAnchor="start">
                  {((v / capital) * 100).toFixed(1)}%
                </text>
              ) : null}
            </g>
          ))}
          {/* X date labels */}
          {xTickIdx.map((idx) => (
            <text key={idx} x={x(idx)} y={H - M.bottom + 16} fontSize="10" fill="#94a3b8" textAnchor="middle">
              {fmtAxisDate(points[idx].date)}
            </text>
          ))}
          {visible.map((s) => (
            <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2"
              strokeDasharray={s.dash} />
          ))}
          {visible.map((s, i) => (
            <text key={s.key} x={M.left + i * 110} y={16} fontSize="10" fill={s.color}>
              {s.dash === '0' ? '●' : '- -'} {s.label}
            </text>
          ))}
        </svg>
      )}
    </div>
  );
}

const fmtPct = (n) => (n == null ? '' : ` (${n > 0 ? '+' : ''}${n.toFixed(1)}%)`);
const round1 = (n) => Math.round(n * 10) / 10;

function KpiCard({ title, stats, color, capital }) {
  const totalWithOpen = (stats.totalPnl || 0) + (stats.unrealizedPnl || 0);
  const totalPct = capital ? round1((totalWithOpen / capital) * 100) : null;
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4">
      <div className={`text-sm font-semibold mb-2 ${color}`}>{title}</div>
      <div className="grid grid-cols-2 gap-3">
        <div><div className="text-lg font-bold text-slate-100">{stats.count}</div><div className="text-[10px] text-slate-400 uppercase">Closed trades</div></div>
        <div><div className="text-lg font-bold text-slate-100">{stats.winRate}%</div><div className="text-[10px] text-slate-400 uppercase">Win rate</div></div>
        <div>
          <div className={`text-lg font-bold ${pnlColor(stats.totalPnl)}`}>{fmtInr(stats.totalPnl)}<span className="text-sm">{fmtPct(stats.totalPnlPct)}</span></div>
          <div className="text-[10px] text-slate-400 uppercase">Realized P&amp;L (net)</div>
          {!!stats.costDrag && (
            <div className="text-[10px] text-slate-500">gross {fmtInr(stats.totalGrossPnl)} · costs −{fmtInr(stats.costDrag)}</div>
          )}
        </div>
        <div><div className="text-lg font-bold text-slate-100">{fmtR(stats.avgR)}</div><div className="text-[10px] text-slate-400 uppercase">Avg R</div></div>
        <div>
          <div className={`text-lg font-bold ${pnlColor(stats.unrealizedPnl)}`}>{fmtInr(stats.unrealizedPnl)}<span className="text-sm">{fmtPct(stats.unrealizedPnlPct)}</span></div>
          <div className="text-[10px] text-slate-400 uppercase">Unrealized ({stats.openPositionCount ?? 0} open)</div>
        </div>
        <div>
          <div className="text-lg font-bold text-amber-300">{fmtInr(stats.maxDrawdown)}</div>
          <div className="text-[10px] text-slate-400 uppercase">
            Max drawdown{stats.maxDrawdownPct != null ? ` (−${stats.maxDrawdownPct.toFixed(1)}%)` : ''}
          </div>
        </div>
        <div>
          <div className={`text-lg font-bold ${stats.cagrPct == null ? 'text-slate-500' : stats.cagrPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {stats.cagrPct == null ? '—' : `${stats.cagrPct.toFixed(1)}%`}
          </div>
          <div className="text-[10px] text-slate-400 uppercase">CAGR</div>
        </div>
        <div className="col-span-2">
          <div className="text-lg font-bold text-slate-100">{fmtInr(stats.deployed)}</div>
          <div className="text-[10px] text-slate-400 uppercase">Capital deployed (open positions)</div>
        </div>
        <div className="col-span-2 pt-1 border-t border-slate-800">
          <div className={`text-lg font-bold ${pnlColor(totalWithOpen)}`}>{fmtInr(totalWithOpen)}<span className="text-sm">{fmtPct(totalPct)}</span></div>
          <div className="text-[10px] text-slate-400 uppercase">Total P&amp;L (realized + unrealized)</div>
        </div>
      </div>
    </div>
  );
}

function RunSummary({ runId, status }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    // Refetch on `status` transitions too (e.g. RUNNING -> COMPLETED), not
    // just when runId itself changes — otherwise a summary fetched while a
    // run was still RUNNING (mostly blank/zero) sits stale in state forever
    // once the run finishes, since the runId prop never changed. Previously
    // this only refreshed on remount (switching tabs away and back).
    getBacktestSummary(runId).then((s) => alive && setSummary(s)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [runId, status]);

  if (error) return <div className="text-sm text-red-300">{error}</div>;
  if (!summary) return <div className="text-sm text-slate-400">Loading summary…</div>;

  const pf = summary.portfolio;

  return (
    <div className="space-y-4">
      {pf ? (
        /* A continuous book is judged on the PATH, so lead with CAGR and
           drawdown. The quant/AI split below does not apply to it — it is one
           undivided book — and showing an always-empty "AI track" card next to
           it was pure noise. */
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
            <Stat label="CAGR" size="lg" tone={pf.cagrPct >= 0 ? 'good' : 'bad'} help={HELP.cagr}
              value={`${pf.cagrPct.toFixed(1)}%${pf.shortWindow ? '*' : ''}`}
              hint={pf.shortWindow ? 'annualised from <2 yrs — not comparable' : undefined} />
            <Stat label="Max drawdown" size="lg" tone="bad" help={HELP.maxDD}
              value={`−${pf.maxDDPct.toFixed(1)}%`}
              hint="what you must sit through" />
            <Stat label="Total P&L" size="lg" tone={pf.totalPnl >= 0 ? 'good' : 'bad'} help={HELP.total}
              value={fmtInr(pf.totalPnl)}
              hint={`ends at ${fmtInr(pf.finalEquity)}`} />
            <Stat label="Martin ratio" size="lg" help={HELP.martin}
              value={pf.martin.toFixed(2)} hint="return ÷ time-weighted pain" />
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
            <Stat label="Worst 12 months" tone="warn" help={HELP.worst12m}
              value={`${pf.worst12mPct.toFixed(1)}%`} />
            <Stat label="Ulcer index" help={HELP.ulcer} value={pf.ulcer.toFixed(2)} />
            <Stat label="Turnover / yr" help={HELP.turnover} value={`${pf.turnoverPerYr.toFixed(2)}×`} />
            <Stat label="Closed trades" help={HELP.trades}
              value={summary.quant?.count ?? '—'}
              hint={summary.quant?.winRate != null ? `${summary.quant.winRate}% won` : undefined} />
          </div>
          {pf.shortWindow && (
            <div className="text-[11px] text-amber-300/90 bg-amber-950/25 border border-amber-800/50 rounded px-3 py-2">
              ⚠ This run covers under two years and restarts at the initial capital.
              Its CAGR annualises a single short window, and its P&amp;L cannot be
              added to — or compared with — the compounded continuous run.
            </div>
          )}
        </>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <KpiCard title="📐 Quant track" stats={summary.quant} color="text-sky-300" capital={summary.capital} />
          <KpiCard title="🤖 AI track" stats={summary.ai} color="text-purple-300" capital={summary.capital} />
        </div>
      )}
      <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-3 sm:p-4">
        <div className="text-sm font-semibold text-slate-200 mb-2">
          <LabelWithInfo help={summary.portfolioEquity ? HELP.equityChart : undefined}>
            {summary.portfolioEquity ? 'Account equity' : 'Equity curve'}
          </LabelWithInfo>
        </div>
        {summary.portfolioEquity
          ? <PortfolioEquity points={summary.portfolioEquity} capital={summary.capital} />
          : <EquityCurve points={summary.equityCurve} capital={summary.capital} />}
      </div>
      <div className="flex flex-wrap gap-4 text-sm text-slate-300">
        <span>Open positions: <b className="text-blue-300">{summary.openCount}</b></span>
        <span>Pending orders: <b className="text-slate-400">{summary.pendingCount}</b></span>
        <span>Capital deployed: <b className="text-slate-100">{fmtInr(summary.totalDeployed)}</b> of {fmtInr(summary.capital)}</span>
      </div>
    </div>
  );
}

// ---------------- Day drill-down ----------------

function TradeMiniRow({ t, extra }) {
  return (
    <tr className="border-t border-slate-800">
      <td className="py-1.5 px-3 text-sm text-slate-200">{t.symbol}</td>
      <td className="py-1.5 px-3 text-xs text-slate-400">
        {t.quantRank && <span className="text-sky-300 mr-1">Q{t.quantRank}</span>}
        {t.aiRank && <span className="text-purple-300">AI{t.aiRank}</span>}
      </td>
      <td className={`py-1.5 px-3 text-sm ${TRADE_STATUS_COLOR[t.status]}`}>{t.status}</td>
      <td className="py-1.5 px-3 text-sm text-slate-300">{extra}</td>
    </tr>
  );
}

const addDays = (dateStr, delta) => {
  const dt = new Date(`${dateStr}T00:00:00Z`);
  dt.setUTCDate(dt.getUTCDate() + delta);
  return dt.toISOString().slice(0, 10);
};

function DayDrilldown({ runId, minDate, maxDate }) {
  const [d, setD] = useState(minDate || '');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => { setD(minDate || ''); }, [runId, minDate]);

  const load = async (day) => {
    if (!day) return;
    setError('');
    try {
      setData(await getBacktestDay(runId, day));
    } catch (e) {
      setData(null);
      setError(e.message);
    }
  };
  useEffect(() => { load(d); }, [d, runId]);

  const step = (delta) => {
    if (!d) return;
    const next = addDays(d, delta);
    if (minDate && next < minDate) return;
    if (maxDate && next > maxDate) return;
    setD(next);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-xs text-slate-400 flex items-center gap-2">
          Date
          <input type="date" value={d} min={minDate} max={maxDate}
            onChange={(e) => setD(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" />
        </label>
        <button onClick={() => step(-1)} disabled={!d || (minDate && d <= minDate)}
          title="Previous day"
          className="px-2.5 py-1.5 text-sm rounded bg-slate-800 border border-slate-600 text-slate-300 hover:text-white disabled:opacity-40">
          ← Prev
        </button>
        <button onClick={() => step(1)} disabled={!d || (maxDate && d >= maxDate)}
          title="Next day"
          className="px-2.5 py-1.5 text-sm rounded bg-slate-800 border border-slate-600 text-slate-300 hover:text-white disabled:opacity-40">
          Next →
        </button>
      </div>

      {error && <div className="text-sm text-red-300">{error}</div>}

      {data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <Section title={`Picks (${data.picks.length})`}>
            {data.picks.map((t) => <TradeMiniRow key={t.id} t={t} extra={fmtInr(t.entryTriggerPrice)} />)}
          </Section>
          <Section title={`Orders filled (${data.ordersFilled.length})`}>
            {data.ordersFilled.map((t) => <TradeMiniRow key={t.id} t={t} extra={fmtInr(t.entryFillPrice)} />)}
          </Section>
          <Section title={`Closed today (${data.closedToday.length})`}>
            {data.closedToday.map((t) => (
              <TradeMiniRow key={t.id} t={t} extra={
                <span className={pnlColor(t.realizedPnl)}>{fmtInr(t.realizedPnl)} · {t.exitReason}</span>
              } />
            ))}
          </Section>
          <Section title={`Open positions (${data.openPositions.length})`}>
            {data.openPositions.map((t) => (
              <TradeMiniRow key={t.id} t={t} extra={
                t.status === 'OPEN'
                  ? <span className={pnlColor(t.unrealizedPnl)}>{fmtInr(t.unrealizedPnl)} unrealized</span>
                  : 'resting'
              } />
            ))}
          </Section>
          <div className="lg:col-span-2 bg-slate-900/60 border border-slate-700 rounded-lg p-4 flex items-center justify-between">
            <span className="text-sm text-slate-300">Realized P&amp;L to date</span>
            <span className={`text-lg font-bold ${pnlColor(data.realizedPnlToDate)}`}>{fmtInr(data.realizedPnlToDate)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }) {
  const empty = React.Children.count(children) === 0;
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg overflow-hidden">
      <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide px-3 py-2 border-b border-slate-800">{title}</div>
      {empty ? (
        <div className="text-sm text-slate-500 px-3 py-3">Nothing here.</div>
      ) : (
        <table className="w-full"><tbody>{children}</tbody></table>
      )}
    </div>
  );
}

// ---------------- Trade chart modal ----------------

function TradeChartModal({ runId, trade, onClose }) {
  if (!trade) return null;
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-lg max-w-[95vw] w-full max-h-[95vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800">
          <div className="text-sm font-semibold text-slate-200">
            {trade.symbol} · trade #{trade.id} · {trade.status}
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-sm px-2">✕</button>
        </div>
        <img src={backtestTradeChartUrl(runId, trade.id)} alt={`${trade.symbol} chart`}
          className="w-full h-auto" />
      </div>
    </div>
  );
}

// ---------------- Trade log ----------------

// Numeric columns the trade log can be sorted by, plus how to read the
// value off a trade row. Missing values sort as 0 rather than dropping to
// the bottom regardless of direction (a PENDING trade's null realizedPnl
// isn't "worse" than a Rs.1 loss, it's just not applicable yet).
const TRADE_SORT_KEYS = {
  realizedPnl: (t) => t.realizedPnl ?? 0,
  unrealizedPnl: (t) => t.unrealizedPnl ?? 0,
};

function SortableTh({ label, sortKey, active, dir, onClick, className = '' }) {
  return (
    <th className={`py-1.5 px-2 cursor-pointer select-none whitespace-nowrap hover:text-slate-300 ${className}`}
      onClick={() => onClick(sortKey)} title="Click to sort">
      {label}{active ? (dir === 'desc' ? ' ▼' : ' ▲') : ''}
    </th>
  );
}

function TradeLog({ runId }) {
  const [track, setTrack] = useState('');
  const [status, setStatus] = useState('');
  const [exitReason, setExitReason] = useState('');
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState('desc');
  const [trades, setTrades] = useState([]);
  const [error, setError] = useState('');
  const [chartTrade, setChartTrade] = useState(null);

  useEffect(() => {
    let alive = true;
    getBacktestTrades(runId, track || undefined, status || undefined)
      .then((t) => alive && setTrades(t)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [runId, track, status]);

  const exitReasons = useMemo(
    () => Array.from(new Set(trades.map((t) => t.exitReason).filter(Boolean))).sort(),
    [trades],
  );

  const rows = useMemo(() => {
    let out = exitReason ? trades.filter((t) => t.exitReason === exitReason) : trades;
    if (sortKey) {
      const get = TRADE_SORT_KEYS[sortKey];
      out = [...out].sort((a, b) => (sortDir === 'asc' ? get(a) - get(b) : get(b) - get(a)));
    }
    return out;
  }, [trades, exitReason, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <select value={track} onChange={(e) => setTrack(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
          <option value="">All tracks</option>
          <option value="quant">Quant</option>
          <option value="ai">AI</option>
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
          <option value="">All statuses</option>
          <option value="PENDING">Pending</option>
          <option value="OPEN">Open</option>
          <option value="CLOSED">Closed</option>
          <option value="SUPERSEDED">Superseded</option>
        </select>
        <select value={exitReason} onChange={(e) => setExitReason(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
          <option value="">All exit reasons</option>
          {exitReasons.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        {sortKey && (
          <button onClick={() => { setSortKey(null); setSortDir('desc'); }}
            className="px-2 py-1.5 text-xs rounded bg-slate-800 border border-slate-600 text-slate-400 hover:text-white">
            Clear sort
          </button>
        )}
      </div>

      {error && <div className="text-sm text-red-300">{error}</div>}

      <div className="bg-slate-900/60 border border-slate-700 rounded-lg overflow-x-auto">
        <table className="w-full min-w-[980px] text-[11px]">
          <thead>
            <tr className="text-left text-[10px] text-slate-500 uppercase tracking-wide">
              <th className="py-1.5 px-2"></th>
              <th className="py-1.5 px-2">Symbol</th>
              <th className="py-1.5 px-2">Rank</th>
              <th className="py-1.5 px-2">Signal</th>
              <th className="py-1.5 px-2">Entry</th>
              <th className="py-1.5 px-2">Fill</th>
              <th className="py-1.5 px-2">Exit</th>
              <th className="py-1.5 px-2">Reason</th>
              <th className="py-1.5 px-2">Trail SL</th>
              <th className="py-1.5 px-2">Alloc</th>
              <SortableTh label="Realized" sortKey="realizedPnl" active={sortKey === 'realizedPnl'} dir={sortDir} onClick={toggleSort} />
              <SortableTh label="Unrealized" sortKey="unrealizedPnl" active={sortKey === 'unrealizedPnl'} dir={sortDir} onClick={toggleSort} />
              <th className="py-1.5 px-2">R</th>
              <th className="py-1.5 px-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className={`border-t border-slate-800 ${trackRowClass(t)}`}>
                <td className="py-1 px-2">
                  <button onClick={() => setChartTrade(t)} title="View chart"
                    className="px-1.5 py-0.5 text-[10px] rounded bg-slate-800 border border-slate-600 text-slate-300 hover:text-white hover:bg-slate-700 whitespace-nowrap">
                    📈 Chart
                  </button>
                </td>
                <td className="py-1 px-2 text-slate-200 font-medium whitespace-nowrap">{t.symbol}</td>
                <td className="py-1 px-2 text-slate-400 whitespace-nowrap">
                  {t.quantRank && <span className="text-sky-300 mr-1">Q{t.quantRank}</span>}
                  {t.aiRank && <span className="text-purple-300">AI{t.aiRank}</span>}
                </td>
                <td className="py-1 px-2 text-slate-300 whitespace-nowrap">{t.signalDate}</td>
                <td className="py-1 px-2 text-slate-300 whitespace-nowrap">{fmtInr(t.entryTriggerPrice)}</td>
                <td className="py-1 px-2 text-slate-300 whitespace-nowrap">{t.entryFillDate ? `${fmtInr(t.entryFillPrice)} (${t.entryFillDate})` : '—'}</td>
                <td className="py-1 px-2 text-slate-300 whitespace-nowrap">{t.exitDate ? `${fmtInr(t.exitPrice)} (${t.exitDate})` : '—'}</td>
                <td className="py-1 px-2 text-slate-400 whitespace-nowrap">{t.exitReason || '—'}</td>
                <td className="py-1 px-2 text-amber-300 whitespace-nowrap">
                  {t.trailSl != null ? fmtInr(t.trailSl) : '—'}
                  {t.trailSl != null && t.structuralSl != null && Math.abs(t.trailSl - t.structuralSl) > 0.01 && (
                    <span className="text-slate-500 ml-1">(moved)</span>
                  )}
                </td>
                <td className="py-1 px-2 text-slate-300 whitespace-nowrap">{fmtInr(t.allocation)}</td>
                <td className={`py-1 px-2 font-semibold whitespace-nowrap ${pnlColor(t.realizedPnl)}`}>{fmtInr(t.realizedPnl)}</td>
                <td className={`py-1 px-2 font-semibold whitespace-nowrap ${pnlColor(t.unrealizedPnl)}`}>{t.status === 'OPEN' ? fmtInr(t.unrealizedPnl) : '—'}</td>
                <td className="py-1 px-2 text-slate-300 whitespace-nowrap">{fmtR(t.rMultiple)}</td>
                <td className={`py-1 px-2 whitespace-nowrap ${TRADE_STATUS_COLOR[t.status]}`}>{t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <div className="text-sm text-slate-500 px-3 py-4">No trades match these filters.</div>}
      </div>

      <TradeChartModal runId={runId} trade={chartTrade} onClose={() => setChartTrade(null)} />
    </div>
  );
}

// ---------------- Page ----------------

const DETAIL_TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'day', label: 'Day drill-down' },
  { id: 'trades', label: 'Trade log' },
];

export default function Backtest() {
  const [runs, setRuns] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detailTab, setDetailTab] = useState('summary');
  const [error, setError] = useState('');
  const [cancellingId, setCancellingId] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [selectionNonce, setSelectionNonce] = useState(0);
  const pollRef = useRef(null);

  const refresh = async () => {
    try {
      const rs = await listBacktestRuns();
      setRuns(rs);
      // Functional form — the 5s poll interval below is only ever set up
      // once (empty deps), so this closure's `selectedId` would otherwise
      // always be its initial `null` and keep snapping selection back to
      // the newest run on every poll, overriding whatever the user clicked.
      setSelectedId((prev) => (prev == null && rs.length ? rs[0].id : prev));
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
    pollRef.current = setInterval(refresh, 5000);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cancel = async (id) => {
    if (!confirm(`Stop run #${id}? This can't be resumed — you'd need to start a new run.`)) return;
    setCancellingId(id);
    setError('');
    try {
      await cancelBacktestRun(id);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setCancellingId(null);
    }
  };

  const running = runs.find((r) => r.status === 'RUNNING');
  const selected = runs.find((r) => r.id === selectedId);

  // Selecting a run always jumps to the Summary tab and forces a fresh fetch
  // (via the RunSummary `key` below, which remounts it) — even re-clicking
  // the already-selected run refreshes it, rather than relying on the 5s poll.
  const selectRun = (id) => {
    setSelectedId(id);
    setDetailTab('summary');
    setSelectionNonce((n) => n + 1);
    refresh();
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[760px_1fr] gap-4 items-start">
      {/* Left: new-run form + run list — pinned so it stays visible while
          the right-hand detail panel is what scrolls. */}
      <div className="space-y-3 lg:sticky lg:top-4">
        <RunConfigForm
          open={formOpen}
          onToggleOpen={setFormOpen}
          onCreated={(id) => { selectRun(id); refresh(); }}
          blocked={!!running}
          blockedReason={running ? `Run #${running.id} is currently in progress — only one run at a time. Stuck? Use the Stop button on it below.` : ''}
        />

        {error && <div className="bg-red-900/40 border border-red-700 text-red-200 text-sm rounded px-3 py-2">{error}</div>}

        <RunList runs={runs} selectedId={selectedId} onSelect={selectRun} onCancel={cancel} cancellingId={cancellingId} />
      </div>

      {/* Right: selected run's details, side by side with the list instead
          of stacked below it. */}
      <div className="space-y-3 min-w-0">
        {!selected ? (
          <div className="text-sm text-slate-400 px-1 py-8 text-center border border-dashed border-slate-700 rounded-lg">
            Select a run on the left to see its summary.
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div className="text-sm text-slate-300">
                Run #{selected.id} · {selected.startDate} → {selected.endDate} · {selected.trackMode}
                {selected.status === 'RUNNING' && selected.progressTotalDays
                  ? ` · day ${selected.progressDay}/${selected.progressTotalDays}`
                  : ''}
                {selected.error && <span className="text-red-300"> · {selected.error}</span>}
              </div>
              <div className="flex items-center gap-1">
                {selected.status === 'RUNNING' && (
                  <button onClick={() => cancel(selected.id)} disabled={cancellingId === selected.id}
                    className="px-3 py-1.5 text-sm rounded bg-red-900/60 border border-red-700 text-red-200 hover:bg-red-900 disabled:opacity-50 mr-2">
                    {cancellingId === selected.id ? 'Stopping…' : 'Stop run'}
                  </button>
                )}
                {DETAIL_TABS.map((t) => (
                  <button key={t.id} onClick={() => setDetailTab(t.id)}
                    className={`px-3 py-1.5 text-sm rounded ${detailTab === t.id
                      ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-300 hover:text-white'}`}>
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {selected.status === 'RUNNING' && (
              <div className="text-sm text-amber-300">Run in progress — results below will update once trades are simulated. If progress hasn't moved in a while, use Stop to unblock the next run.</div>
            )}

            {detailTab === 'summary' && <RunSummary key={`${selected.id}-${selectionNonce}`} runId={selected.id} status={selected.status} />}
            {detailTab === 'day' && <DayDrilldown runId={selected.id} minDate={selected.startDate} maxDate={selected.endDate} />}
            {detailTab === 'trades' && <TradeLog runId={selected.id} />}
          </>
        )}
      </div>
    </div>
  );
}
