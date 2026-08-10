import React, { useEffect, useRef, useState } from 'react';
import {
  createBacktestRun, listBacktestRuns, getBacktestRun, getBacktestSummary,
  getBacktestTrades, getBacktestDay, cancelBacktestRun, backtestTradeChartUrl,
} from '../api/client.js';

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

// Compact list of tags describing what actually differs from stock defaults
// on this run — shown as the "Settings" column in the run list, and as a
// fuller tooltip (title attr) on hover. Intentionally omits anything at its
// default value so the column stays scannable across many runs.
function summarizeRunSettings(run) {
  const tags = [];
  if (run.maxPicksPerTrack != null && run.maxPicksPerTrack !== 3) tags.push(`top${run.maxPicksPerTrack}/track`);
  if (run.quantFunnelVariant === 'v2') tags.push('rank:v2');
  // The validated edges — shown first-class so a run's identity is obvious
  if (run.stage2BaseStageMaxAllowed != null) tags.push(`baseStage≤${run.stage2BaseStageMaxAllowed}`);
  if (run.entryBreadthMaxPct != null) tags.push(`breadth<${run.entryBreadthMaxPct}%`);
  if (run.entryBreadthRequireRising) tags.push('breadth↑');
  if (run.maxContractionRatio != null) tags.push(`VCP≤${run.maxContractionRatio}`);
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
  start_date: '', end_date: '', track_mode: 'QUANT', capital: 400000,
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
  safety_sl_pct: 10.0, slippage_pct: 0.10, brokerage_per_order: 0.0, chandelier_atr_mult: 3.0,
  max_capital_per_trade_pct: '', min_position_value: '',
  notes: '',
};

// One-click starting points. "Best known" is the configuration that came out
// ahead across BOTH validation windows (2025 + 2026) in the sweeps; "Production
// today" is what screen_gpt.py actually runs right now, for A/B comparison.
const PRESETS = {
  best: {
    label: 'Best known', hint: 'Winner across both validation windows',
    values: {
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
  production: {
    label: 'Production today', hint: 'What screen_gpt.py currently runs',
    values: {
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

function Field({ label, hint, value, onChange, type = 'text', ...rest }) {
  return (
    <label className="text-xs text-slate-400 flex flex-col gap-1">
      <span>{label}</span>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)}
        className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" {...rest} />
      {hint && <span className="text-[11px] text-slate-500 leading-snug">{hint}</span>}
    </label>
  );
}

function RunConfigForm({ onCreated, blocked, blockedReason, open, onToggleOpen }) {
  const [f, setF] = useState(DEFAULT_FORM);
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
        exit_config: {
          breakeven: f.breakeven, half_booking: f.half_booking,
          trailing: f.trailing, fixed_target: f.fixed_target,
          ema10_trail: f.ema10_trail, ema21_trail: f.ema21_trail, ema50_trail: f.ema50_trail,
          chandelier_trail: f.chandelier_trail, swing_trail: f.swing_trail,
          failed_breakout_exit: f.failed_breakout_exit, swing_break_exit: f.swing_break_exit,
        },
        safety_sl_pct: Number(f.safety_sl_pct) || 10.0,
        slippage_pct: Number(f.slippage_pct) || 0,
        brokerage_per_order: Number(f.brokerage_per_order) || 0,
        chandelier_atr_mult: Number(f.chandelier_atr_mult) || 3.0,
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
          Track mode
          <select value={f.track_mode} onChange={(e) => set('track_mode')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm">
            <option value="QUANT">Quant only</option>
            <option value="AI">AI only</option>
            <option value="BOTH">Both</option>
          </select>
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Capital (₹)
          <input type="number" value={f.capital} onChange={(e) => set('capital')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" min="10000" step="10000" />
        </label>
      </div>

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
            Picks per day
            <input type="number" min="1" max="10" value={f.max_picks_per_track}
              onChange={(e) => set('max_picks_per_track')(e.target.value)}
              className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
            <span className="text-slate-500">(2 tested best)</span>
          </label>
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

      <label className="text-xs text-slate-400 flex flex-col gap-1">
        Notes (optional)
        <input type="text" value={f.notes} onChange={(e) => set('notes')(e.target.value)}
          placeholder="e.g. baseline 6-month run"
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

// Drop the year for the compact run-list column — "2026-01-01" -> "01-01".
const fmtWindowShort = (s, e) => `${s?.slice(5) ?? ''}→${e?.slice(5) ?? ''}`;

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

function RunList({ runs, selectedId, onSelect, onCancel, cancellingId }) {
  const rowRefs = useRef({});
  if (!runs.length) return <div className="text-sm text-slate-400 px-1">No backtest runs yet — configure one above.</div>;

  // Arrow-key navigation between rows — preventDefault stops the browser's
  // default behavior of scrolling this (overflow-y-auto) container instead
  // of moving the selection, then we move focus to the newly-selected row
  // so repeated presses keep working and it scrolls into view.
  const move = (idx, delta) => {
    const target = runs[idx + delta];
    if (!target) return;
    onSelect(target.id);
    rowRefs.current[target.id]?.focus();
  };
  const handleKeyDown = (e, idx) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(idx, 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(idx, -1); }
  };

  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg overflow-x-auto max-h-[480px] overflow-y-auto">
      <table className="w-full table-auto">
        <thead className="sticky top-0 bg-slate-900 z-10">
          <tr className="text-left text-[10px] text-slate-500 uppercase tracking-wide">
            <th className="py-1.5 px-2">Run</th>
            <th className="py-1.5 px-2">Window</th>
            <th className="py-1.5 px-2">Track</th>
            <th className="py-1.5 px-2">Capital</th>
            <th className="py-1.5 px-2">Status</th>
            <th className="py-1.5 px-2">Trades</th>
            <th className="py-1.5 px-2">Settings</th>
            <th className="py-1.5 px-2"></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r, idx) => (
            <RunRow key={r.id} run={r} selected={r.id === selectedId} onSelect={onSelect}
              onCancel={onCancel} cancelling={cancellingId === r.id}
              rowRef={(el) => { rowRefs.current[r.id] = el; }}
              onKeyDown={(e) => handleKeyDown(e, idx)} />
          ))}
        </tbody>
      </table>
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
        <div><div className="text-lg font-bold text-amber-300">{fmtInr(stats.maxDrawdown)}</div><div className="text-[10px] text-slate-400 uppercase">Max drawdown</div></div>
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

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <KpiCard title="📐 Quant track" stats={summary.quant} color="text-sky-300" capital={summary.capital} />
        <KpiCard title="🤖 AI track" stats={summary.ai} color="text-purple-300" capital={summary.capital} />
      </div>
      <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4">
        <div className="text-sm font-semibold text-slate-200 mb-2">Equity curve</div>
        <EquityCurve points={summary.equityCurve} capital={summary.capital} />
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

function TradeLog({ runId }) {
  const [track, setTrack] = useState('');
  const [status, setStatus] = useState('');
  const [trades, setTrades] = useState([]);
  const [error, setError] = useState('');
  const [chartTrade, setChartTrade] = useState(null);

  useEffect(() => {
    let alive = true;
    getBacktestTrades(runId, track || undefined, status || undefined)
      .then((t) => alive && setTrades(t)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [runId, track, status]);

  return (
    <div className="space-y-3">
      <div className="flex gap-3">
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
      </div>

      {error && <div className="text-sm text-red-300">{error}</div>}

      <div className="bg-slate-900/60 border border-slate-700 rounded-lg overflow-x-auto">
        <table className="w-full min-w-[1120px]">
          <thead>
            <tr className="text-left text-[11px] text-slate-500 uppercase tracking-wide">
              <th className="py-2 px-3">Symbol</th>
              <th className="py-2 px-3">Rank</th>
              <th className="py-2 px-3">Signal</th>
              <th className="py-2 px-3">Entry</th>
              <th className="py-2 px-3">Fill</th>
              <th className="py-2 px-3">Exit</th>
              <th className="py-2 px-3">Reason</th>
              <th className="py-2 px-3">Allocation</th>
              <th className="py-2 px-3">Realized P&amp;L</th>
              <th className="py-2 px-3">Unrealized P&amp;L</th>
              <th className="py-2 px-3">R</th>
              <th className="py-2 px-3">Status</th>
              <th className="py-2 px-3"></th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className={`border-t border-slate-800 ${trackRowClass(t)}`}>
                <td className="py-1.5 px-3 text-sm text-slate-200">{t.symbol}</td>
                <td className="py-1.5 px-3 text-xs text-slate-400">
                  {t.quantRank && <span className="text-sky-300 mr-1">Q{t.quantRank}</span>}
                  {t.aiRank && <span className="text-purple-300">AI{t.aiRank}</span>}
                </td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{t.signalDate}</td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{fmtInr(t.entryTriggerPrice)}</td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{t.entryFillDate ? `${fmtInr(t.entryFillPrice)} (${t.entryFillDate})` : '—'}</td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{t.exitDate ? `${fmtInr(t.exitPrice)} (${t.exitDate})` : '—'}</td>
                <td className="py-1.5 px-3 text-xs text-slate-400">{t.exitReason || '—'}</td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{fmtInr(t.allocation)}</td>
                <td className={`py-1.5 px-3 text-sm font-semibold ${pnlColor(t.realizedPnl)}`}>{fmtInr(t.realizedPnl)}</td>
                <td className={`py-1.5 px-3 text-sm font-semibold ${pnlColor(t.unrealizedPnl)}`}>{t.status === 'OPEN' ? fmtInr(t.unrealizedPnl) : '—'}</td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{fmtR(t.rMultiple)}</td>
                <td className={`py-1.5 px-3 text-sm ${TRADE_STATUS_COLOR[t.status]}`}>{t.status}</td>
                <td className="py-1.5 px-3 text-sm">
                  <button onClick={() => setChartTrade(t)} title="View chart"
                    className="px-2 py-1 text-xs rounded bg-slate-800 border border-slate-600 text-slate-300 hover:text-white hover:bg-slate-700">
                    📈 Chart
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!trades.length && <div className="text-sm text-slate-500 px-3 py-4">No trades match these filters.</div>}
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
