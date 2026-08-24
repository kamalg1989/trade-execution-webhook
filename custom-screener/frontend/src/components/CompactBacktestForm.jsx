import React, { useState, useEffect, useCallback } from 'react';
import { createBacktestRun, listPresets, createPreset, getPreset, getPresetByName, deletePreset } from '../api/client.js';

const STRATEGIES = [
  { value: 'BREAKOUT', label: '📐 Breakout - Daily trend-following' },
  { value: 'POSITIONAL', label: '📊 Positional - Weekly momentum' },
  { value: 'PORTFOLIO', label: '💼 Portfolio - Multi-position book' },
  { value: 'WEEKLY_BREAKOUT', label: '📈 Weekly Breakout - Weekly consolidation' },
  { value: 'SQUEEZE_BREAKOUT', label: '⚡ Squeeze - Volatility breakout' },
  { value: 'RSI_REVERSION', label: '⭕ RSI Reversion - Mean reversion' },
  { value: 'INDEX_TF', label: '🧭 Index TF - Long/flat MA trend on index' },
];

const MOMENTUM_OPTIONS = [
  { value: 'pct_chg_3m', label: '3-month change' },
  { value: 'pct_chg_6m', label: '6-month change' },
  { value: 'pct_chg_1y', label: '1-year change' },
];

const IFP_OPTIONS = [50, 60, 70, 75, 80, 85, 90, 95];
const TURNOVER_OPTIONS = [2.5, 5, 7.5, 10, 15, 20, 25];
const BASE_RANGE_OPTIONS = [40, 50, 60, 70, 80, 90, 100];

// ---- Quick presets (frozen, audit-validated configurations) --------------
// Each maps 1:1 to an engine-validated run so what the button launches is
// exactly what the research measured — not an approximate reconstruction.
const QUICK_PRESETS = {
  'POSITIONAL Momentum (recommended)': {
    hint: 'Run #909 · 16.9% CAGR / 25.4% MaxDD / Calmar 0.67 over 15yr · persist-2 exit',
    data: {
      strategy: 'POSITIONAL', capital: 400000,
      startDate: '2011-01-01', endDate: '2026-08-16',
      posMomentum: 'composite_rs', posRebalanceDays: 21,
      posTopN: 30, posBufferN: 60,
      posMinTurnoverCr: 8.0, posAtrMaxPct: 5.0, posAtrPersistDays: 2,
      posMinIfpScore: 0.38, posMinClose: 20.0, posBaseRangeScoreW: 1.0,
      posSizeMode: 'inverse_vol', posSlMode: 'none', posSlPct: 0,
      compoundingEnabled: true, compoundingMode: 'profit_only',
      compoundingMinCapital: 400000, compoundingMaxCapital: 20000000,
      slippagePct: 0.20, exitSlippagePct: '', advPositionCapPct: '',
      notes: 'UI quick preset: POSITIONAL momentum, ATR persist-2 (run #909)',
    },
  },
  'POSITIONAL relATR (aggressive)': {
    hint: 'Run #1062 \u00b7 19.9% CAGR / 25.5% MaxDD / Calmar 0.78 over 15yr \u00b7 relATR exit + breadth-smile sizing',
    data: {
      strategy: 'POSITIONAL', capital: 400000,
      startDate: '2011-01-01', endDate: '2026-08-16',
      posMomentum: 'composite_rs', posRebalanceDays: 21,
      posTopN: 30, posBufferN: 60,
      posMinTurnoverCr: 8.0, posAtrMaxPct: 5.0, posAtrPersistDays: 2,
      posAtrRelMult: 1.5, posAtrTrimPct: 33, posB200MidCut: 0.5,
      posMinIfpScore: 0.38, posMinClose: 20.0, posBaseRangeScoreW: 1.0,
      posSizeMode: 'inverse_vol', posSlMode: 'none', posSlPct: 0,
      compoundingEnabled: true, compoundingMode: 'profit_only',
      compoundingMinCapital: 400000, compoundingMaxCapital: 20000000,
      slippagePct: 0.20, exitSlippagePct: '', advPositionCapPct: '',
      notes: 'UI quick preset: POSITIONAL relATR 1.5/trim 33 + breadth-smile 45-60 cut 0.5 (run #1062, splits #1064-#1067)',
    },
  },
  'POSITIONAL Combo (balanced)': {
    hint: 'Run #1079 \u00b7 22.8% CAGR / 24.5% MaxDD / Calmar 0.93 over 15yr \u00b7 + 52wk-high factor, earnings gate, cash yield',
    data: {
      strategy: 'POSITIONAL', capital: 400000,
      startDate: '2011-01-01', endDate: '2026-08-16',
      posMomentum: 'composite_rs', posRebalanceDays: 21,
      posTopN: 30, posBufferN: 60,
      posMinTurnoverCr: 8.0, posAtrMaxPct: 5.0, posAtrPersistDays: 2,
      posAtrRelMult: 1.5, posAtrTrimPct: 33, posB200MidCut: 0.5,
      posW52wh: 1.0, posEarnGateDays: 14, posCashAnnualPct: 6.0,
      posMinIfpScore: 0.38, posMinClose: 20.0, posBaseRangeScoreW: 1.0,
      posSizeMode: 'inverse_vol', posSlMode: 'none', posSlPct: 0,
      compoundingEnabled: true, compoundingMode: 'profit_only',
      compoundingMinCapital: 400000, compoundingMaxCapital: 20000000,
      slippagePct: 0.20, exitSlippagePct: '', advPositionCapPct: '',
      notes: 'UI quick preset: POSITIONAL combo — relATR + smile + 52wh w1 + earn-gate 14d + cash 6% (run #1079, splits #1080-#1081)',
    },
  },
  'COMBO 80/20 + ETF Blend': {
    hint: 'Live allocation \u00b7 blend of runs #1117 \u00d7 #1440 (GOLDBEES): 21.0% CAGR / 19.9% MaxDD / Calmar 1.06 over the FULL 15.6yr \u00b7 launches the Combo equity sleeve; hold 20% in GOLDBEES (200-DMA long/flat) alongside',
    data: {
      strategy: 'POSITIONAL', capital: 320000,
      startDate: '2011-01-01', endDate: '2026-08-16',
      posMomentum: 'composite_rs', posRebalanceDays: 21,
      posTopN: 30, posBufferN: 60,
      posMinTurnoverCr: 8.0, posAtrMaxPct: 5.0, posAtrPersistDays: 2,
      posAtrRelMult: 1.5, posAtrTrimPct: 33, posB200MidCut: 0.5,
      posW52wh: 1.0, posEarnGateDays: 14, posCashAnnualPct: 6.0,
      posMinIfpScore: 0.38, posMinClose: 20.0, posBaseRangeScoreW: 1.0,
      posSizeMode: 'inverse_vol', posSlMode: 'none', posSlPct: 0,
      compoundingEnabled: true, compoundingMode: 'profit_only',
      compoundingMinCapital: 320000, compoundingMaxCapital: 16000000,
      slippagePct: 0.20, exitSlippagePct: '', advPositionCapPct: '',
      notes: 'UI quick preset: 80% Combo equity sleeve of the 80/20 ETF blend (pair with 20% GOLDBEES 200-DMA; 15.6yr sleeve bake-off runs #1440/#1601/#1762/#1923/#2084 after the 2011 ETF history backfill; blend via /backtest/blend run_a=1440 run_b=1117 w=0.2; the earlier 28.9% figure was a 7.6yr window and is superseded; paper book etf_blend)',
    },
  },
  'Preset #14 Static': {
    hint: 'Audited run #700 · CAGR 9.5% (haircut) · conservative',
    data: {
      strategy: 'WEEKLY_BREAKOUT', capital: 400000,
      startDate: '2011-08-17', endDate: '2026-08-16',
      weeklyRiskPct: 1.0, weeklyEntryCadence: 'biweekly', weeklyRankMode: 'composite',
      maxPicksPerTrack: 3, max_capital_per_trade_pct: 25,
      compoundingEnabled: false, safetySlPct: 10,
      exitSlippagePct: 0.30, advPositionCapPct: 2.0, compoundingMaxCapital: '',
      notes: 'UI preset #14 static (audited)',
    },
  },
  'Preset #15 Comp-Capped': {
    hint: 'Audited run #703 · CAGR 18.3% (haircut) · ₹20L cap',
    data: {
      strategy: 'WEEKLY_BREAKOUT', capital: 400000,
      startDate: '2011-08-17', endDate: '2026-08-16',
      weeklyRiskPct: 1.0, weeklyEntryCadence: 'weekly', weeklyRankMode: 'composite',
      maxPicksPerTrack: 8, max_capital_per_trade_pct: 25,
      compoundingEnabled: true, compoundingMode: 'profit_only',
      compoundingMinCapital: 400000, compoundingMaxCapital: 2000000,
      safetySlPct: 10, exitSlippagePct: 0.30, advPositionCapPct: 2.0,
      notes: 'UI preset #15 comp-capped (audited)',
    },
  },
  'INDEX_TF (JUNIORBEES)': {
    hint: 'Audited run #705 · the 30/70 blend’s diversifier leg',
    data: {
      strategy: 'INDEX_TF', capital: 400000,
      startDate: '2019-01-01', endDate: '2026-08-16',
      itfProxy: 'JUNIORBEES', itfMaDays: 200,
      compoundingEnabled: true, compoundingMode: 'profit_only',
      compoundingMinCapital: 400000, exitSlippagePct: 0.30,
      notes: 'UI preset INDEX_TF JUNIORBEES (audited)',
    },
  },
};

const TOOLTIPS = {
  strategy: 'Trading strategy. WEEKLY_BREAKOUT + composite ranking and INDEX_TF are the audited production pair',
  capital: 'Starting capital in ₹. Position sizing scales with this',
  startDate: 'Backtest start. 10+ years recommended for robust validation',
  endDate: 'Backtest end date',
  cadence: 'WEEKLY_BREAKOUT: weekly evaluates new signals every week-end, biweekly every other',
  rankMode: 'composite = validated factor ranking (low turnover + 3m momentum + dist-200SMA). box_weeks = legacy',
  advCap: 'RISK GUARD: no position may exceed this % of the stock’s 1-month ADV. Audited value: 2%',
  compCap: 'RISK GUARD: compounding sizing stops scaling at this equity. Audited value: ₹20,00,000',
  exitSlip: 'Stressed-exit slippage on SELL legs. Audit value 0.30% (buys stay at Slippage %)',
  riskPct: 'Account risk % per trade (weekly strategies). Audited configs use 1.0%',
  maxCapPct: 'Max position size as % of capital',
  maxPicks: 'Max new picks per entry period',
  ifp: 'Min IFP score gate (Stage 1). Blank = production default',
  turnover: 'Min turnover gate ₹cr. Blank = production default. NOTE: composite ranking already prefers LOW turnover; a floor here fights it',
  baseRange: 'Max base consolidation range %. Blank = production default',
  itfProxy: 'Index proxy. JUNIORBEES/NIFTYBEES = tradeable ETFs (2019+). SYNTH_EQW = 15yr synthetic (research only)',
  itfMa: 'MA length for long/flat switch. Audited: 200',
  sttPct: 'Securities transaction tax. Fixed 0.1% — do not change',
  stampDutyPct: 'Stamp duty. Fixed 0.015% — do not change',
  exchangeChargesPct: 'Exchange charges. Fixed 0.003% — do not change',
  dpCharge: 'DP charge per sell, ₹. Dhan: 14.75 — do not change',
  slippagePct: 'Base slippage per order. NSE equity: ~0.10%',
  safetySlPct: 'Hard intraday stop % from entry',
};

const DEFAULTS = {
  strategy: 'WEEKLY_BREAKOUT',
  capital: 400000,
  startDate: '2016-01-01',
  endDate: new Date().toISOString().split('T')[0],
  // core weekly knobs
  weeklyRiskPct: 1.0,
  weeklyEntryCadence: 'weekly',
  weeklyRankMode: 'composite',
  maxPicksPerTrack: 3,
  signalCadence: 'daily',
  signalScanDay: 'last',
  // positional
  posMomentum: 'pct_chg_6m', posRebalanceDays: 21, posTopN: 10, posBufferN: 20,
  posMinTurnoverCr: 5.0, posSlMode: 'none', posSlPct: 0,
  // 2026-08-19: these existed in the engine but the form never tracked or sent
  // them, so a saved POSITIONAL preset could not actually configure a run --
  // it silently fell back to engine defaults (pct_chg_6m, N=10, no ATR ceiling,
  // no IFP gate, equal weight). That is what produced run #922's 54.6% MaxDD.
  posAtrMaxPct: '', posMinIfpScore: '', posMinClose: '',
  posBaseRangeScoreW: '', posSizeMode: '', posAtrPersistDays: '',
  posAtrRelMult: '', posAtrTrimPct: '', posB200MidCut: '',
  posW52wh: '', posEarnGateDays: '', posCashAnnualPct: '',
  // index tf
  itfProxy: 'JUNIORBEES', itfMaDays: 200,
  // risk guards (audit defaults ON for new runs)
  advPositionCapPct: 2.0,
  compoundingMaxCapital: '',
  exitSlippagePct: 0.30,
  risk_per_trade_pct: '', max_capital_per_trade_pct: '',
  // entry filters / exit structure — preset-loadable (run #752 post-mortem)
  minRiskPctOfPrice: '', requireWeeklyBox: false, weeklyBoxLookback: 10,
  stackingGuard: false, stackingGuardMode: 'SKIP',
  exitConfig: null,   // null -> API default (production ladder)
  // costs
  safetySlPct: 10.0, slippagePct: 0.1, sttPct: 0.1, stampDutyPct: 0.015,
  exchangeChargesPct: 0.003, dpCharge: 14.75,
  // model filters
  gate_min_ifp_score: '', gate_min_turnover_cr: '', gate_max_base_range_pct: '',
  // compounding
  compoundingEnabled: false, compoundingMinCapital: 400000, compoundingMode: 'profit_only',
  notes: null,
};

export default function CompactBacktestForm({ onCreated, blocked, blockedReason, open, onToggleOpen }) {
  const [formData, setFormData] = useState(DEFAULTS);
  const [presets, setPresets] = useState([]);
  const [selectedPreset, setSelectedPreset] = useState('');
  const [newPresetName, setNewPresetName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const set = (patch) => setFormData((prev) => ({ ...prev, ...patch }));

  useEffect(() => { if (open) loadPresets(); }, [open]);

  const loadPresets = async () => {
    try { setPresets((await listPresets()) || []); }
    catch (e) { console.error('Failed to load presets:', e); }
  };

  const applyQuickPreset = (name) => {
    const qp = QUICK_PRESETS[name];
    if (!qp) return;
    setFormData({ ...DEFAULTS, ...qp.data });
    setSelectedPreset(name);
    setError('');
  };

  const loadPreset = async (nameOrId) => {
    try {
      const preset = !isNaN(nameOrId)
        ? await getPreset(parseInt(nameOrId)) : await getPresetByName(nameOrId);
      let cfg = preset.config;
      if (typeof cfg === 'string') cfg = JSON.parse(cfg);
      // Saved presets store snake_case API fields; map the ones the form
      // tracks in camelCase so a loaded preset actually fills the form.
      const mapped = { ...cfg };
      const M = {
        start_date: 'startDate', end_date: 'endDate',
        weekly_risk_pct: 'weeklyRiskPct', weekly_entry_cadence: 'weeklyEntryCadence',
        weekly_rank_mode: 'weeklyRankMode', max_picks_per_track: 'maxPicksPerTrack',
        compounding_enabled: 'compoundingEnabled', compounding_mode: 'compoundingMode',
        compounding_min_capital: 'compoundingMinCapital',
        compounding_max_capital: 'compoundingMaxCapital',
        adv_position_cap_pct: 'advPositionCapPct', exit_slippage_pct: 'exitSlippagePct',
        itf_proxy: 'itfProxy', itf_ma_days: 'itfMaDays',
        safety_sl_pct: 'safetySlPct', slippage_pct: 'slippagePct',
        stt_pct: 'sttPct', stamp_duty_pct: 'stampDutyPct',
        exchange_charges_pct: 'exchangeChargesPct', dp_charge: 'dpCharge',
        // 2026-08-18 fix (run #752 post-mortem): these were silently DROPPED
        // when loading a preset, so a saved weekly-cadence config launched as
        // a daily-cadence run with the production exit ladder — a different
        // strategy than the preset's name promised. exit_config passes
        // through whole; risk_per_trade_pct covers the daily engine's sizing.
        signal_cadence: 'signalCadence', signal_scan_day: 'signalScanDay',
        min_risk_pct_of_price: 'minRiskPctOfPrice',
        require_weekly_box_breakout: 'requireWeeklyBox',
        weekly_box_lookback_days: 'weeklyBoxLookback',
        risk_per_trade_pct: 'risk_per_trade_pct',
        max_capital_per_trade_pct: 'max_capital_per_trade_pct',
        exit_config: 'exitConfig', stacking_guard: 'stackingGuard',
        stacking_guard_mode: 'stackingGuardMode',
        // 2026-08-19 fix (run #922 post-mortem): the POSITIONAL fields were
        // absent from this map, so loading a positional preset left every one
        // of them at the form default and launched a completely different
        // strategy from the one the preset name promised.
        pos_momentum: 'posMomentum', pos_rebalance_days: 'posRebalanceDays',
        pos_top_n: 'posTopN', pos_buffer_n: 'posBufferN',
        pos_min_turnover_cr: 'posMinTurnoverCr',
        pos_sl_mode: 'posSlMode', pos_sl_pct: 'posSlPct',
        pos_atr_max_pct: 'posAtrMaxPct', pos_min_ifp_score: 'posMinIfpScore',
        pos_min_close: 'posMinClose',
        pos_base_range_score_w: 'posBaseRangeScoreW',
        pos_size_mode: 'posSizeMode', pos_atr_persist_days: 'posAtrPersistDays',
        pos_atr_rel_mult: 'posAtrRelMult', pos_atr_trim_pct: 'posAtrTrimPct',
        pos_b200_mid_cut: 'posB200MidCut',
        pos_w_52wh: 'posW52wh', pos_earn_gate_days: 'posEarnGateDays',
        pos_cash_annual_pct: 'posCashAnnualPct',
      };
      Object.entries(M).forEach(([snake, camel]) => {
        if (cfg[snake] !== undefined) { mapped[camel] = cfg[snake]; delete mapped[snake]; }
      });
      setFormData((prev) => ({ ...prev, ...mapped }));
      setSelectedPreset(preset.name);
      setError('');
    } catch (e) {
      setError(`Failed to load preset: ${e.message}`);
    }
  };

  const savePreset = async () => {
    if (!newPresetName.trim()) { setError('Preset name cannot be empty'); return; }
    try {
      await createPreset(newPresetName, formData.strategy, formData);
      setNewPresetName(''); await loadPresets(); setError('');
    } catch (e) { setError(e.message); }
  };

  const submit = useCallback(async (e) => {
    e?.preventDefault?.();
    if (submitting || blocked) return;
    setSubmitting(true);
    setError('');
    try {
      const num = (v) => (v === '' || v == null ? undefined : Number(v));
      const payload = {
        strategy: formData.strategy,
        capital: Number(formData.capital),
        start_date: formData.startDate,
        end_date: formData.endDate,
        track_mode: 'BOTH',
        // core
        max_picks_per_track: num(formData.maxPicksPerTrack),
        weekly_risk_pct: num(formData.weeklyRiskPct),
        weekly_entry_cadence: formData.weeklyEntryCadence,
        weekly_rank_mode: formData.weeklyRankMode,
        signal_cadence: formData.signalCadence,
        signal_scan_day: formData.signalScanDay,
        // risk guards
        adv_position_cap_pct: num(formData.advPositionCapPct),
        compounding_max_capital: num(formData.compoundingMaxCapital),
        exit_slippage_pct: num(formData.exitSlippagePct),
        risk_per_trade_pct: num(formData.risk_per_trade_pct),
        max_capital_per_trade_pct: num(formData.max_capital_per_trade_pct),
        // entry filters / exit structure (preset passthrough — #752 fix)
        min_risk_pct_of_price: num(formData.minRiskPctOfPrice),
        ...(formData.requireWeeklyBox && {
          require_weekly_box_breakout: true,
          weekly_box_lookback_days: num(formData.weeklyBoxLookback) ?? 10,
        }),
        ...(formData.stackingGuard && {
          stacking_guard: true,
          stacking_guard_mode: formData.stackingGuardMode || 'SKIP',
        }),
        ...(formData.exitConfig && { exit_config: formData.exitConfig }),
        // compounding
        compounding_enabled: !!formData.compoundingEnabled,
        compounding_min_capital: num(formData.compoundingMinCapital) ?? 400000,
        compounding_mode: formData.compoundingMode || 'profit_only',
        // costs
        safety_sl_pct: num(formData.safetySlPct),
        slippage_pct: num(formData.slippagePct),
        stt_pct: num(formData.sttPct),
        stamp_duty_pct: num(formData.stampDutyPct),
        exchange_charges_pct: num(formData.exchangeChargesPct),
        dp_charge: num(formData.dpCharge),
        // model filters (only when set — IFP normalized to 0-1)
        ...(formData.gate_min_ifp_score !== '' && formData.gate_min_ifp_score != null
          && { gate_min_ifp_score: parseFloat(formData.gate_min_ifp_score) / 100 }),
        ...(formData.gate_min_turnover_cr !== '' && formData.gate_min_turnover_cr != null
          && { gate_min_turnover_cr: parseFloat(formData.gate_min_turnover_cr) }),
        ...(formData.gate_max_base_range_pct !== '' && formData.gate_max_base_range_pct != null
          && { gate_max_base_range_pct: parseFloat(formData.gate_max_base_range_pct) }),
        // strategy-specific
        ...(formData.strategy === 'INDEX_TF' && {
          itf_proxy: formData.itfProxy, itf_ma_days: num(formData.itfMaDays),
        }),
        ...((formData.strategy === 'POSITIONAL' || formData.strategy === 'PORTFOLIO') && {
          pos_momentum: formData.posMomentum,
          pos_rebalance_days: num(formData.posRebalanceDays),
          pos_top_n: num(formData.posTopN), pos_buffer_n: num(formData.posBufferN),
          pos_min_turnover_cr: num(formData.posMinTurnoverCr),
          pos_sl_mode: formData.posSlMode, pos_sl_pct: num(formData.posSlPct),
          // these were never sent before, so no UI run could reproduce the
          // validated configuration (see run #922)
          pos_atr_max_pct: num(formData.posAtrMaxPct),
          pos_min_ifp_score: num(formData.posMinIfpScore),
          pos_min_close: num(formData.posMinClose),
          pos_base_range_score_w: num(formData.posBaseRangeScoreW),
          pos_size_mode: formData.posSizeMode || undefined,
          pos_atr_persist_days: num(formData.posAtrPersistDays),
          pos_atr_rel_mult: num(formData.posAtrRelMult),
          pos_atr_trim_pct: num(formData.posAtrTrimPct),
          pos_b200_mid_cut: num(formData.posB200MidCut),
          pos_w_52wh: num(formData.posW52wh),
          pos_earn_gate_days: num(formData.posEarnGateDays),
          pos_cash_annual_pct: num(formData.posCashAnnualPct),
        }),
        ...(formData.notes && { notes: formData.notes }),
      };
      Object.keys(payload).forEach((k) => {
        if (payload[k] === undefined || payload[k] === '' || payload[k] === null) delete payload[k];
      });
      const res = await createBacktestRun(payload);
      onCreated(res.id);
      onToggleOpen(false);
    } catch (err) {
      let msg = err.response?.detail
        ? (typeof err.response.detail === 'string' ? err.response.detail : JSON.stringify(err.response.detail))
        : err.message || 'Failed to create backtest';
      setError(`Error: ${msg}`);
    } finally {
      setSubmitting(false);
    }
  }, [formData, submitting, blocked, onCreated, onToggleOpen]);

  // Ctrl+Enter / Cmd+Enter launches the run from anywhere in the form.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, submit]);

  if (!open) {
    return (
      <button onClick={() => onToggleOpen(true)}
        className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-semibold py-2 rounded-lg">
        + New Backtest Run <span className="font-normal text-emerald-200 text-xs">(⌘/Ctrl+Enter to launch)</span>
      </button>
    );
  }

  const inputCls = 'w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-100';
  const isWeekly = formData.strategy === 'WEEKLY_BREAKOUT';
  const isItf = formData.strategy === 'INDEX_TF';

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 space-y-3 max-h-[80vh] overflow-y-auto">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-bold text-white">New Backtest</h3>
        <button type="button" onClick={() => onToggleOpen(false)} className="text-slate-400 hover:text-white text-xl">✕</button>
      </div>

      {blocked && <div className="bg-yellow-900/40 border border-yellow-700 text-yellow-200 text-xs rounded px-3 py-2">{blockedReason}</div>}

      {/* Quick presets — one click to the audited configurations */}
      <div className="flex flex-wrap gap-2">
        {Object.entries(QUICK_PRESETS).map(([name, qp]) => (
          <button key={name} type="button" onClick={() => applyQuickPreset(name)} title={qp.hint}
            className={`px-2.5 py-1.5 text-[11px] rounded-lg border font-semibold ${selectedPreset === name
              ? 'bg-emerald-700/60 border-emerald-500 text-white'
              : 'bg-slate-700/60 border-slate-600 text-slate-200 hover:border-emerald-600'}`}>
            ⭐ {name}
          </button>
        ))}
      </div>

      {/* Saved presets */}
      <div className="bg-slate-700/50 rounded p-3 space-y-2">
        <div className="text-xs font-semibold text-slate-300 uppercase">Saved presets</div>
        <select value={selectedPreset} onChange={(e) => e.target.value && loadPreset(e.target.value)}
          className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm text-slate-100">
          <option value="">Load saved preset…</option>
          {presets.map((p) => <option key={p.id} value={p.id.toString()}>{p.name}</option>)}
        </select>
        <div className="flex gap-2">
          <input type="text" placeholder="Save current as…" value={newPresetName} onChange={(e) => setNewPresetName(e.target.value)}
            className="flex-1 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-100" />
          <button type="button" onClick={savePreset}
            className="bg-slate-600 hover:bg-slate-500 text-white px-3 py-1 rounded text-xs font-semibold">Save</button>
        </div>
      </div>

      <form onSubmit={submit} className="space-y-3">
        {error && <div className="bg-red-900/40 border border-red-700 text-red-200 text-xs rounded px-3 py-2">{error}</div>}

        {/* CARD 1 — Core Execution */}
        <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-3 space-y-2">
          <div className="text-[11px] font-bold text-emerald-300 uppercase tracking-wide">1 · Core Execution</div>
          <div className="grid grid-cols-2 gap-2">
            <FormField label="Strategy" help={TOOLTIPS.strategy} required>
              <select value={formData.strategy} onChange={(e) => set({ strategy: e.target.value })} className={inputCls}>
                {STRATEGIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </FormField>
            <FormField label="Capital (₹)" help={TOOLTIPS.capital}>
              <input type="number" value={formData.capital} onChange={(e) => set({ capital: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="Start Date" help={TOOLTIPS.startDate}>
              <input type="date" value={formData.startDate} onChange={(e) => set({ startDate: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="End Date" help={TOOLTIPS.endDate}>
              <input type="date" value={formData.endDate} onChange={(e) => set({ endDate: e.target.value })} className={inputCls} />
            </FormField>
            {isWeekly && (<>
              <FormField label="Entry Cadence" help={TOOLTIPS.cadence}>
                <select value={formData.weeklyEntryCadence} onChange={(e) => set({ weeklyEntryCadence: e.target.value })} className={inputCls}>
                  <option value="weekly">weekly</option><option value="biweekly">biweekly</option>
                </select>
              </FormField>
              <FormField label="Ranking" help={TOOLTIPS.rankMode}>
                <select value={formData.weeklyRankMode} onChange={(e) => set({ weeklyRankMode: e.target.value })} className={inputCls}>
                  <option value="composite">composite (validated)</option>
                  <option value="box_weeks">box_weeks (legacy)</option>
                </select>
              </FormField>
            </>)}
            {isItf && (<>
              <FormField label="Index Proxy" help={TOOLTIPS.itfProxy}>
                <select value={formData.itfProxy} onChange={(e) => set({ itfProxy: e.target.value })} className={inputCls}>
                  {['JUNIORBEES', 'NIFTYBEES', 'SETFNIF50', 'SYNTH_EQW'].map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </FormField>
              <FormField label="MA Days" help={TOOLTIPS.itfMa}>
                <input type="number" value={formData.itfMaDays} onChange={(e) => set({ itfMaDays: e.target.value })} className={inputCls} />
              </FormField>
            </>)}
            <FormField label="Max Picks / period" help={TOOLTIPS.maxPicks}>
              <input type="number" value={formData.maxPicksPerTrack} onChange={(e) => set({ maxPicksPerTrack: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="Notes">
              <input type="text" value={formData.notes || ''} onChange={(e) => set({ notes: e.target.value })}
                placeholder="optional" className={inputCls} />
            </FormField>
          </div>
        </div>

        {/* CARD 2 — Risk & Portfolio Guards */}
        <div className="bg-slate-900/50 border border-sky-900 rounded-lg p-3 space-y-2">
          <div className="text-[11px] font-bold text-sky-300 uppercase tracking-wide">2 · Risk &amp; Portfolio Guards</div>
          <div className="grid grid-cols-2 gap-2">
            <FormField label="ADV Cap % (liquidity guard)" help={TOOLTIPS.advCap}>
              <input type="number" step="0.5" value={formData.advPositionCapPct}
                onChange={(e) => set({ advPositionCapPct: e.target.value })} placeholder="blank = off" className={inputCls} />
            </FormField>
            <FormField label="Compounding Cap (₹)" help={TOOLTIPS.compCap}>
              <input type="number" step="100000" value={formData.compoundingMaxCapital}
                onChange={(e) => set({ compoundingMaxCapital: e.target.value })} placeholder="blank = uncapped" className={inputCls} />
            </FormField>
            <FormField label="Stressed Exit Slippage %" help={TOOLTIPS.exitSlip}>
              <input type="number" step="0.05" value={formData.exitSlippagePct}
                onChange={(e) => set({ exitSlippagePct: e.target.value })} placeholder="blank = base slippage" className={inputCls} />
            </FormField>
            {isWeekly && (
              <FormField label="Risk % / trade" help={TOOLTIPS.riskPct}>
                <input type="number" step="0.25" value={formData.weeklyRiskPct}
                  onChange={(e) => set({ weeklyRiskPct: e.target.value })} className={inputCls} />
              </FormField>
            )}
            <FormField label="Max Capital / trade %" help={TOOLTIPS.maxCapPct}>
              <input type="number" step="2.5" value={formData.max_capital_per_trade_pct}
                onChange={(e) => set({ max_capital_per_trade_pct: e.target.value })} placeholder="blank = default" className={inputCls} />
            </FormField>
            <div className="col-span-2 flex items-center gap-2 pt-1">
              <input type="checkbox" id="compEnabled" checked={formData.compoundingEnabled}
                onChange={(e) => set({ compoundingEnabled: e.target.checked })} className="w-4 h-4 rounded" />
              <label htmlFor="compEnabled" className="text-xs text-slate-200">
                📈 Compound sizing off running equity
                {formData.compoundingEnabled && !formData.compoundingMaxCapital && (
                  <span className="text-amber-300 ml-1">⚠ uncapped — audit recommends ₹20L cap</span>
                )}
              </label>
            </div>
          </div>
        </div>

        {/* CARD 3 — Model Filters */}
        <div className="bg-slate-900/50 border border-purple-900 rounded-lg p-3 space-y-2">
          <div className="text-[11px] font-bold text-purple-300 uppercase tracking-wide">3 · Model Filters</div>
          <div className="grid grid-cols-3 gap-2">
            <FormField label="Min IFP Score" help={TOOLTIPS.ifp}>
              <select value={formData.gate_min_ifp_score ?? ''} onChange={(e) => set({ gate_min_ifp_score: e.target.value })} className={inputCls}>
                <option value="">— default —</option>
                {IFP_OPTIONS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </FormField>
            <FormField label="Min Turnover ₹cr" help={TOOLTIPS.turnover}>
              <select value={formData.gate_min_turnover_cr ?? ''} onChange={(e) => set({ gate_min_turnover_cr: e.target.value })} className={inputCls}>
                <option value="">— default —</option>
                {TURNOVER_OPTIONS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </FormField>
            <FormField label="Max Base Range %" help={TOOLTIPS.baseRange}>
              <select value={formData.gate_max_base_range_pct ?? ''} onChange={(e) => set({ gate_max_base_range_pct: e.target.value })} className={inputCls}>
                <option value="">— default —</option>
                {BASE_RANGE_OPTIONS.map((v) => <option key={v} value={v}>{v}%</option>)}
              </select>
            </FormField>
          </div>
        </div>

        {/* Positional-specific card (only when relevant) */}
        {(formData.strategy === 'POSITIONAL' || formData.strategy === 'PORTFOLIO') && (
          <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-3 space-y-2">
            <div className="text-[11px] font-bold text-slate-300 uppercase">Positional Settings</div>
            <div className="grid grid-cols-2 gap-2">
              <FormField label="Momentum">
                <select value={formData.posMomentum} onChange={(e) => set({ posMomentum: e.target.value })} className={inputCls}>
                  {MOMENTUM_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </FormField>
              <FormField label="Rebalance Days">
                <input type="number" value={formData.posRebalanceDays} onChange={(e) => set({ posRebalanceDays: e.target.value })} className={inputCls} />
              </FormField>
              <FormField label="Top N">
                <input type="number" value={formData.posTopN} onChange={(e) => set({ posTopN: e.target.value })} className={inputCls} />
              </FormField>
              <FormField label="Buffer N">
                <input type="number" value={formData.posBufferN} onChange={(e) => set({ posBufferN: e.target.value })} className={inputCls} />
              </FormField>
            </div>
          </div>
        )}

        {/* Advanced Transaction Costs — collapsed accordion. These four are
            statutory/broker constants that should essentially never change;
            they earned their demotion from the main grid. */}
        <details className="bg-slate-700/40 rounded p-3">
          <summary className="cursor-pointer text-[11px] font-semibold text-slate-400 uppercase">
            Advanced Transaction Costs (STT · Stamp · DP · Exchange)
          </summary>
          <div className="grid grid-cols-3 gap-2 mt-3">
            <FormField label="Base Slippage %" help={TOOLTIPS.slippagePct}>
              <input type="number" step="0.01" value={formData.slippagePct} onChange={(e) => set({ slippagePct: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="Safety SL %" help={TOOLTIPS.safetySlPct}>
              <input type="number" step="0.5" value={formData.safetySlPct} onChange={(e) => set({ safetySlPct: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="STT %" help={TOOLTIPS.sttPct}>
              <input type="number" step="0.01" value={formData.sttPct} onChange={(e) => set({ sttPct: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="Stamp Duty %" help={TOOLTIPS.stampDutyPct}>
              <input type="number" step="0.001" value={formData.stampDutyPct} onChange={(e) => set({ stampDutyPct: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="Exchange %" help={TOOLTIPS.exchangeChargesPct}>
              <input type="number" step="0.001" value={formData.exchangeChargesPct} onChange={(e) => set({ exchangeChargesPct: e.target.value })} className={inputCls} />
            </FormField>
            <FormField label="DP Charge (₹)" help={TOOLTIPS.dpCharge}>
              <input type="number" step="0.01" value={formData.dpCharge} onChange={(e) => set({ dpCharge: e.target.value })} className={inputCls} />
            </FormField>
          </div>
        </details>

        <button type="submit" disabled={submitting || blocked}
          className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-semibold py-2 rounded-lg">
          {submitting ? 'Creating…' : 'Run Backtest'}
          <span className="font-normal text-emerald-200 text-xs ml-2">⌘/Ctrl+Enter</span>
        </button>
      </form>
    </div>
  );
}

function FormField({ label, help, children, required }) {
  const [showTooltip, setShowTooltip] = React.useState(false);
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-semibold text-slate-300 flex items-center gap-1">
        {label}
        {required && <span className="text-red-400">*</span>}
        {help && (
          <div className="relative inline-block">
            <span onMouseEnter={() => setShowTooltip(true)} onMouseLeave={() => setShowTooltip(false)}
              className="text-slate-400 hover:text-slate-200 cursor-help font-bold">?</span>
            {showTooltip && (
              <div className="absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 px-2 py-1 bg-slate-900 text-slate-100 text-xs rounded z-50 border border-slate-600 shadow-lg w-56 whitespace-normal">
                {help}
                <div className="absolute top-full left-1/2 transform -translate-x-1/2 border-4 border-transparent border-t-slate-900"></div>
              </div>
            )}
          </div>
        )}
      </label>
      {children}
    </div>
  );
}
