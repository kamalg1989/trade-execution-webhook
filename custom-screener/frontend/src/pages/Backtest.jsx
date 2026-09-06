import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  listBacktestRuns, getBacktestSummary, getBacktestTrades, getBacktestDay,
  cancelBacktestRun, backtestTradeChartUrl,
} from '../api/client.js';
import CompactBacktestForm from '../components/CompactBacktestForm.jsx';
import { HELP, useIsMobile } from '../components/ui.jsx';
import {
  getCagr, getMaxDD, getWorst12m, getMartin, getMaxUwDays,
  fmtCagr, fmtMaxDD, fmtW12m, fmtRatio, fmtUwMonths, runBadges,
} from '../utils/runMetrics.js';
import '../styles/backtest.css';

/* ═══════════════════════════════════════════════════════════════════════
   Formatters
   ═══════════════════════════════════════════════════════════════════ */
const fmtInr = (n) =>
  n == null ? '—' : `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const fmtPx = (n) =>
  n == null ? '—' : Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const fmtInrCompact = (n) => {
  if (n == null) return '—';
  const sign = n < 0 ? '−' : '';
  const abs = Math.abs(n);
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(1)}L`;
  if (abs >= 1e3) return `${sign}₹${(abs / 1e3).toFixed(1)}k`;
  return `${sign}₹${Math.round(abs)}`;
};
const fmtR = (n) => (n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}R`);
const fmtPctS = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
const pnlCls = (n) => (n == null ? 'tert' : n > 0 ? 'g' : n < 0 ? 'l' : 'muted');
const fmtWindowShort = (s, e) => `${s?.slice(2) ?? ''} → ${e?.slice(2) ?? ''}`;
const shortDate = (d) => {
  if (!d) return '—';
  const M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${d.slice(8, 10)} ${M[+d.slice(5, 7) - 1]} '${d.slice(2, 4)}`;
};
const addDays = (dateStr, delta) => {
  const dt = new Date(`${dateStr}T00:00:00Z`);
  dt.setUTCDate(dt.getUTCDate() + delta);
  return dt.toISOString().slice(0, 10);
};

const RUN_BADGE = {
  COMPLETED: ['b-done', 'Completed'], RUNNING: ['b-run', 'Running'],
  FAILED: ['b-fail', 'Failed'], QUEUED: ['b-void', 'Queued'], CANCELLED: ['b-void', 'Cancelled'],
};
const runBadgeFor = (s) => RUN_BADGE[s] || ['b-void', s || '—'];
const TRADE_DOT = {
  PENDING: ['var(--ink-tert)', 'tert'], OPEN: ['var(--info)', 'i'],
  CLOSED: ['var(--ink-subtle)', 'muted'], SUPERSEDED: ['var(--hair-strong)', 'tert'],
};
const trackRowClass = (t) => {
  const q = t.quantRank != null, a = t.aiRank != null;
  if (q && a) return 'tk-b';
  if (q) return 'tk-q';
  if (a) return 'tk-a';
  return '';
};

// Audit badges next to the run id — driven by the run's own config columns
// via runBadges(), never by notes text.
function AuditBadges({ run }) {
  const badges = runBadges(run);
  if (!badges.length) return null;
  return (
    <>
      {badges.map((b) => (
        <span key={b.label} className={`badge b-${b.tone}`} title={b.title}>{b.label}</span>
      ))}
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Run settings summary (unchanged logic — this is what the Settings column
   and the rail tags are built from)
   ═══════════════════════════════════════════════════════════════════ */
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
const POS_SL_NEEDS_PCT = new Set(['fixed', 'trail']);

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
function summarizeRunSettings(run) {
  const tags = [];
  if (run.strategy === 'PORTFOLIO') {
    const t = ['PORTFOLIO', `${run.posMomentum?.replace('pct_chg_', '') ?? '6m'} mom`,
               `top${run.posTopN}`, `rebal ${run.posRebalanceDays}d`];
    if (isShortWindow(run)) t.unshift('⚠ 1-YR STANDALONE — DO NOT SUM');
    if (run.posTranches > 1) t.push(`${run.posTranches} tranches`);
    if (run.posPhaseDays != null && run.posPhaseDays !== 0) t.push(`phase ${run.posPhaseDays}`);
    if (run.posSlPct > 0) t.push(`stop ${run.posSlPct}%`);
    if (run.pfVolMode && run.pfVolMode !== 'none') {
      t.push(`vol:${run.pfVolMode}${run.pfVolFloor ? ` floor${run.pfVolFloor}%` : ''}`);
    }
    if (run.pfDdThrottleAt > 0) t.push(`ddThrottle ${(run.pfDdThrottleAt * 100).toFixed(0)}%`);
    if (run.pfMaxStocksPerSector && run.pfMaxStocksPerSector < 99) t.push(`${run.pfMaxStocksPerSector}/sector`);
    if (run.pfRequireSector) t.push('⚠ sector-only universe');
    return t;
  }
  if (run.strategy === 'WEEKLY_BREAKOUT') {
    const t = ['WEEKLY_BREAKOUT', `risk${run.weeklyRiskPct ?? 1.0}%`];
    if (run.maxPicksPerTrack != null && run.maxPicksPerTrack !== 3) t.push(`top${run.maxPicksPerTrack}/wk`);
    if (run.restingWindowDays != null) t.push(`rest:${run.restingWindowDays}wk`);
    if (run.stackingGuard) t.push(`stack:${run.stackingGuardMode}`);
    if (run.weeklyDailyExitCheck) t.push('daily exit-check');
    if (run.maxCapitalPerTradePct != null) t.push(`cap${run.maxCapitalPerTradePct}%`);
    if (run.weeklyCompoundingSizing) t.push('compounding');
    if (run.gateMinTurnoverCr != null) t.push(`turnover≥${run.gateMinTurnoverCr}cr`);
    if (run.gateMinIfpScore != null) t.push(`IFP≥${run.gateMinIfpScore}`);
    if (run.gateMaxBaseRangePct != null) t.push(`base<${run.gateMaxBaseRangePct}%`);
    if (run.gateMinVolMult != null) t.push(`vol>${run.gateMinVolMult}x`);
    if (run.gateMinPriorUpmovePct != null) t.push(`upmove≥${run.gateMinPriorUpmovePct}%`);
    if (run.gateMaxGivebackPct != null) t.push(`giveback≤${run.gateMaxGivebackPct}%`);
    if (run.gateMaxVolDryupRatio != null) t.push(`dryup≤${run.gateMaxVolDryupRatio}`);
    if (run.gateMaxDistFromHighPct != null) t.push(`distHigh≤${run.gateMaxDistFromHighPct}%`);
    if (run.stage2BaseStageMaxAllowed != null) t.push(`baseStage≤${run.stage2BaseStageMaxAllowed}`);
    if (run.maxContractionRatio != null) t.push(`VCP≤${run.maxContractionRatio}`);
    return t;
  }
  if (run.strategy === 'SQUEEZE_BREAKOUT') {
    const t = ['SQUEEZE_BREAKOUT', `vol≥${run.squeezeVolumeMultiplier ?? 1.5}x`];
    if (run.maxHoldingDays != null) t.push(`hold≤${run.maxHoldingDays}d`);
    if (run.riskPerTradePct != null) t.push(`risk${run.riskPerTradePct}%`);
    if (run.stackingGuard) t.push(`stack:${run.stackingGuardMode}`);
    return t;
  }
  if (run.strategy === 'RSI_REVERSION') {
    const t = ['RSI_REVERSION', `RSI<${run.rsiEntryThreshold ?? 35}`,
               `stop${run.rsiStopPct ?? 4.5}%`, `tgt${run.rsiTargetPct ?? 5}%`];
    if (run.maxHoldingDays != null) t.push(`hold≤${run.maxHoldingDays}d`);
    if (run.stackingGuard) t.push(`stack:${run.stackingGuardMode}`);
    return t;
  }
  if (run.maxPicksPerTrack != null && run.maxPicksPerTrack !== 3) tags.push(`top${run.maxPicksPerTrack}/track`);
  if (run.strategy === 'POSITIONAL') {
    return ['POSITIONAL', `${run.posMomentum?.replace('pct_chg_', '') ?? '6m'} mom`,
            `top${run.posTopN}/buf${run.posBufferN}`, `rebal ${run.posRebalanceDays}d`,
            slLabel(run.posSlMode, run.posSlPct)].filter(Boolean);
  }
  if (run.quantFunnelVariant === 'v2') tags.push('rank:v2');
  if (run.stage2BaseStageMaxAllowed != null) tags.push(`baseStage≤${run.stage2BaseStageMaxAllowed}`);
  if (run.entryBreadthMaxPct != null) tags.push(`breadth<${run.entryBreadthMaxPct}%`);
  if (run.entryBreadthRequireRising) tags.push('breadth↑');
  if (run.maxContractionRatio != null) tags.push(`VCP≤${run.maxContractionRatio}`);
  if (run.requireWeeklyBoxBreakout) tags.push(`weekly-box≤${run.weeklyBoxLookbackDays ?? 10}d`);
  if (run.riskPerTradePct != null) tags.push(`risk${run.riskPerTradePct}%`);
  if (run.maxCapitalPerTradePct != null) tags.push(`cap${run.maxCapitalPerTradePct}%`);
  for (const [key, fmt] of GATE_LABELS) if (run[key] != null) tags.push(fmt(run[key]));
  const ec = run.exitConfig || {};
  for (const [key, label] of EXIT_LABELS) if (ec[key]) tags.push(label);
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

/* ═══════════════════════════════════════════════════════════════════════
   Run rail
   ═══════════════════════════════════════════════════════════════════ */
const SORTS = {
  newest:     { label: 'Newest first',    fn: (a, b) => b.id - a.id },
  oldest:     { label: 'Oldest first',    fn: (a, b) => a.id - b.id },
  calmarDesc: { label: 'Calmar ↓',        fn: (a, b) => calmar(b) - calmar(a) },
  cagrDesc:   { label: 'CAGR ↓',          fn: (a, b) => (getCagr(b) ?? -99) - (getCagr(a) ?? -99) },
  ddAsc:      { label: 'Max DD ↑',        fn: (a, b) => Math.abs(getMaxDD(a) ?? 99) - Math.abs(getMaxDD(b) ?? 99) },
  totalDesc:  { label: 'Total P&L ↓',     fn: (a, b) => (b.totalPnl ?? -Infinity) - (a.totalPnl ?? -Infinity) },
  totalAsc:   { label: 'Total P&L ↑',     fn: (a, b) => (a.totalPnl ?? Infinity) - (b.totalPnl ?? Infinity) },
  totalPctD:  { label: 'Total P&L % ↓',   fn: (a, b) => pctOf(b, 'totalPnl') - pctOf(a, 'totalPnl') },
  realDesc:   { label: 'Realized P&L ↓',  fn: (a, b) => (b.realizedPnl ?? -Infinity) - (a.realizedPnl ?? -Infinity) },
  tradesDesc: { label: 'Trades ↓',        fn: (a, b) => (b.tradeCount ?? 0) - (a.tradeCount ?? 0) },
};
const calmar = (r) => {
  const c = getCagr(r), d = getMaxDD(r);
  return (c != null && d) ? c / Math.abs(d) : -Infinity;
};
const pctOf = (r, key) => (r.capital ? (100 * (r[key] ?? 0)) / r.capital : -Infinity);
const pctVal = (r, key) => (r.capital && r[key] != null ? (100 * r[key]) / r.capital : null);

const PAGE = 40;

function RunCard({ run, selected, onSelect, onCancel, cancelling, rowRef, onKeyDown }) {
  const [cls, txt] = runBadgeFor(run.status);
  const pct = run.progressTotalDays ? Math.round((run.progressDay / run.progressTotalDays) * 100) : null;
  const tags = summarizeRunSettings(run);
  const cagr = getCagr(run), dd = getMaxDD(run);
  const executed = run.startedAt ? new Date(run.startedAt).toLocaleString() : '—';
  return (
    <button type="button" className="run" aria-current={selected} ref={rowRef} onKeyDown={onKeyDown}
      onClick={() => onSelect(run.id)} title={run.params?.notes || undefined}>
      <span className="run-id">
        #{run.id}
        <span className={`badge ${cls}`}>{txt}</span>
        <AuditBadges run={run} />
      </span>
      <span className="run-when">{executed}</span>
      <span className="run-sub">{run.params?.notes || 'defaults'}</span>
      <span className="run-sub tert">
        {run.strategy} · {run.trackMode} · {fmtInrCompact(run.capital)}
        {run.posTranches > 1 ? ` · ${run.posTranches} tranches` : ''}
        {run.execSeconds ? ` · ${(run.execSeconds / 60).toFixed(1)}m` : ''}
        {' · '}{fmtWindowShort(run.startDate, run.endDate)}
      </span>
      {pct != null && run.status === 'RUNNING' && (
        <>
          <span className="prog"><i style={{ width: `${pct}%` }} /></span>
          <span className="run-nums"><span>day {run.progressDay}/{run.progressTotalDays} · {pct}%</span></span>
        </>
      )}
      {cagr != null && (
        <span className="run-nums">
          <span>CAGR <b className={cagr >= 0 ? 'g' : 'l'}>{fmtCagr(cagr, isShortWindow(run))}</b></span>
          <span>DD <b className="l">{fmtMaxDD(dd)}</b></span>
          <span>w12m <b className="w">{fmtW12m(getWorst12m(run))}</b></span>
          <span>Calmar <b className="muted">{calmar(run) === -Infinity ? '—' : calmar(run).toFixed(2)}</b></span>
          <span>Total <b className={pnlCls(run.totalPnl)}>{fmtInrCompact(run.totalPnl)}</b>{' '}
            <b className={pnlCls(run.totalPnl)}>{fmtPctS(pctVal(run, 'totalPnl'))}</b></span>
          <span>{(run.tradeCount ?? 0).toLocaleString('en-IN')} trades</span>
        </span>
      )}
      {run.error && <span className="run-nums"><span className="l">{run.error}</span></span>}
      {!!tags.length && (
        <span className="run-tags">
          {tags.slice(0, 6).map((t, i) => (
            <span key={i} className={`tag${t.startsWith('⚠') ? ' warnt' : ''}`}>{t}</span>
          ))}
        </span>
      )}
      {run.status === 'RUNNING' && (
        <span style={{ gridColumn: '1/-1', marginTop: 6 }}>
          <button type="button" className="btn btn-sm btn-danger" disabled={cancelling}
            onClick={(e) => { e.stopPropagation(); onCancel(run.id); }}>
            {cancelling ? 'Stopping…' : 'Stop'}
          </button>
        </span>
      )}
    </button>
  );
}

const TABLE_COLS = [
  { label: 'Run' },
  { label: 'Executed' },
  { label: 'Time' },
  { label: 'Window', help: HELP.window },
  { label: 'Track' },
  { label: 'Capital', help: HELP.capital, right: true },
  { label: 'Status', help: HELP.status },
  { label: 'Trades', help: HELP.trades, right: true },
  { label: 'Realized', help: HELP.realized, right: true, sort: 'realDesc' },
  { label: 'Real %', help: 'Realized P&L as a share of starting capital', right: true },
  { label: 'Unreal.', help: HELP.unrealized, right: true },
  { label: 'Unreal %', help: 'Unrealized as a share of starting capital', right: true },
  { label: 'Total', help: HELP.total, right: true, sort: 'totalDesc' },
  { label: 'Total %', help: 'Total P&L as a share of starting capital', right: true, sort: 'totalPctD' },
  { label: 'CAGR', help: HELP.cagr, right: true, sort: 'cagrDesc' },
  { label: 'maxDD', help: HELP.maxDD, right: true, sort: 'ddAsc' },
  { label: 'w12m', help: HELP.worst12m, right: true },
  { label: 'Calmar', help: 'CAGR divided by max drawdown', right: true, sort: 'calmarDesc' },
  { label: 'Martin', help: HELP.martin, right: true },
  { label: 'Settings', help: HELP.settings },
  { label: '' },
];

function RunTable({ runs, selectedId, onSelect, onCancel, cancellingId, sortKey, setSortKey }) {
  return (
    <table style={{ minWidth: 1500 }}>
      <thead>
        <tr>
          {TABLE_COLS.map((c, i) => (
            <th key={i} className={`${c.sort ? 'sortable ' : ''}${c.right ? 'right' : ''}`}
              title={c.help} onClick={c.sort ? () => setSortKey(c.sort) : undefined}>
              {c.label}{c.sort && sortKey === c.sort ? ' ▼' : ''}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const [cls, txt] = runBadgeFor(r.status);
          const pct = r.progressTotalDays ? Math.round((r.progressDay / r.progressTotalDays) * 100) : null;
          const cagr = getCagr(r);
          const tags = summarizeRunSettings(r);
          return (
            <tr key={r.id} className={r.id === selectedId ? 'sel' : ''} onClick={() => onSelect(r.id)}
              style={{ cursor: 'pointer' }}>
              <td className="n" style={{ color: 'var(--ink)' }}>#{r.id} <AuditBadges run={r} /></td>
              <td className="n tert">{r.startedAt ? new Date(r.startedAt).toLocaleString() : '—'}</td>
              <td className="n tert">{r.execSeconds ? `${(r.execSeconds / 60).toFixed(1)}m` : '—'}</td>
              <td className="n">{fmtWindowShort(r.startDate, r.endDate)}</td>
              <td>{r.trackMode}</td>
              <td className="n right">{fmtInrCompact(r.capital)}
                {r.posTranches > 1 ? <span className="tert"> ×{r.posTranches}T</span> : null}</td>
              <td className={r.status === 'COMPLETED' ? 'g' : r.status === 'RUNNING' ? 'w' : r.status === 'FAILED' ? 'l' : 'tert'}>
                {r.status}{pct != null && r.status === 'RUNNING' ? <span className="tert"> · {pct}%</span> : null}
              </td>
              <td className="n right">{r.tradeCount ?? '—'}</td>
              <td className={`n right ${pnlCls(r.realizedPnl)}`}>{fmtInrCompact(r.realizedPnl)}</td>
              <td className={`n right ${pnlCls(r.realizedPnl)}`}>{fmtPctS(pctVal(r, 'realizedPnl'))}</td>
              <td className={`n right ${pnlCls(r.unrealizedPnl)}`}>{fmtInrCompact(r.unrealizedPnl)}</td>
              <td className={`n right ${pnlCls(r.unrealizedPnl)}`}>{fmtPctS(pctVal(r, 'unrealizedPnl'))}</td>
              <td className={`n right ${pnlCls(r.totalPnl)}`} style={{ fontWeight: 500 }}>{fmtInrCompact(r.totalPnl)}</td>
              <td className={`n right ${pnlCls(r.totalPnl)}`} style={{ fontWeight: 500 }}>{fmtPctS(pctVal(r, 'totalPnl'))}</td>
              <td className={`n right ${cagr == null ? 'tert' : cagr >= 0 ? 'g' : 'l'}`}>{fmtCagr(cagr, isShortWindow(r))}</td>
              <td className="n right l">{fmtMaxDD(getMaxDD(r))}</td>
              <td className="n right w">{fmtW12m(getWorst12m(r))}</td>
              <td className="n right">{calmar(r) === -Infinity ? '—' : calmar(r).toFixed(2)}</td>
              <td className="n right">{fmtRatio(getMartin(r))}</td>
              <td style={{ whiteSpace: 'normal', minWidth: 210 }}>
                <div style={{ color: 'var(--ink-muted)' }}>{r.params?.notes || 'defaults'}</div>
                <div className="run-tags" style={{ marginTop: 3 }}>
                  {tags.slice(0, 6).map((t, i) => (
                    <span key={i} className={`tag${t.startsWith('⚠') ? ' warnt' : ''}`}>{t}</span>
                  ))}
                </div>
              </td>
              <td>
                {r.status === 'RUNNING' && (
                  <button type="button" className="btn btn-sm btn-danger" disabled={cancellingId === r.id}
                    onClick={(e) => { e.stopPropagation(); onCancel(r.id); }}>
                    {cancellingId === r.id ? '…' : 'Stop'}
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function RunList({ runs, selectedId, onSelect, onCancel, cancellingId }) {
  const isMobile = useIsMobile(1200);
  const rowRefs = useRef({});
  const sentinelRef = useRef(null);
  const [view, setView] = useState('list');
  const [sortKey, setSortKey] = useState('newest');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [trackFilter, setTrackFilter] = useState('ALL');
  const [strategyFilter, setStrategyFilter] = useState('ALL');
  const [idFilter, setIdFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('');
  const [query, setQuery] = useState('');
  const [winnersOnly, setWinnersOnly] = useState(false);
  const [limit, setLimit] = useState(PAGE);

  const strategies = useMemo(
    () => Array.from(new Set(runs.map((r) => r.strategy).filter(Boolean))).sort(), [runs]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const idQ = idFilter.trim().replace(/^#/, '');
    return runs
      .filter((r) => statusFilter === 'ALL' || r.status === statusFilter)
      .filter((r) => trackFilter === 'ALL' || r.trackMode === trackFilter)
      .filter((r) => strategyFilter === 'ALL' || r.strategy === strategyFilter)
      .filter((r) => !idQ || String(r.id).startsWith(idQ))
      .filter((r) => !dateFilter || (r.startedAt && r.startedAt.slice(0, 10) >= dateFilter))
      .filter((r) => !winnersOnly || (r.totalPnl ?? 0) > 0)
      .filter((r) => {
        if (!q) return true;
        const hay = [r.params?.notes, `#${r.id}`, r.startDate, r.endDate,
          ...summarizeRunSettings(r)].filter(Boolean).join(' ').toLowerCase();
        return hay.includes(q);
      })
      .sort(SORTS[sortKey].fn);
  }, [runs, sortKey, statusFilter, trackFilter, strategyFilter, idFilter, dateFilter, query, winnersOnly]);

  useEffect(() => { setLimit(PAGE); },
    [sortKey, statusFilter, trackFilter, strategyFilter, idFilter, dateFilter, query, winnersOnly, view]);

  const visible = useMemo(() => filtered.slice(0, limit), [filtered, limit]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || limit >= filtered.length) return;
    const obs = new IntersectionObserver(
      (entries) => entries[0].isIntersecting && setLimit((l) => l + PAGE),
      { root: el.closest('[data-runscroll]') || null, rootMargin: '120px' });
    obs.observe(el);
    return () => obs.disconnect();
  }, [limit, filtered.length, view]);

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

  const more = filtered.length - visible.length;
  const showTable = view === 'table' && !isMobile;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Runs</h2>
        <span className="eyebrow" style={{ marginLeft: 'auto' }}>
          {filtered.length === runs.length ? `${runs.length} total` : `${filtered.length}/${runs.length}`}
        </span>
        {!isMobile && (
          <div className="seg" role="group" aria-label="Run list density">
            <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>List</button>
            <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button>
          </div>
        )}
      </div>

      <div className="filters">
        <input className="ctl" type="search" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="Search notes / settings…" aria-label="Search runs" style={{ flex: 1, minWidth: 130 }} />
        <input className="ctl" type="search" value={idFilter} onChange={(e) => setIdFilter(e.target.value)}
          placeholder="#id" aria-label="Filter by run id" style={{ width: 62 }} />
        <select className="ctl" value={strategyFilter} onChange={(e) => setStrategyFilter(e.target.value)} aria-label="Strategy">
          <option value="ALL">All strategies</option>
          {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input className="ctl" type="date" value={dateFilter} onChange={(e) => setDateFilter(e.target.value)}
          title="Executed on/after" aria-label="Executed on or after" />
        <select className="ctl" value={sortKey} onChange={(e) => setSortKey(e.target.value)} aria-label="Sort runs">
          {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select className="ctl" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="Status">
          <option value="ALL">All status</option>
          <option value="COMPLETED">Completed</option>
          <option value="RUNNING">Running</option>
          <option value="FAILED">Failed</option>
        </select>
        <select className="ctl" value={trackFilter} onChange={(e) => setTrackFilter(e.target.value)} aria-label="Track">
          <option value="ALL">All tracks</option>
          <option value="QUANT">Quant</option>
          <option value="AI">AI</option>
          <option value="BOTH">Both</option>
        </select>
        <label className="check">
          <input type="checkbox" checked={winnersOnly} onChange={(e) => setWinnersOnly(e.target.checked)} />
          Profitable only
        </label>
      </div>

      {!runs.length ? (
        <div className="empty">No backtest runs yet — configure one above.</div>
      ) : !filtered.length ? (
        <div className="empty">No runs match these filters.</div>
      ) : showTable ? (
        <div className="tablewrap" data-runscroll style={{ maxHeight: 600 }}>
          <RunTable runs={visible} selectedId={selectedId} onSelect={onSelect} onCancel={onCancel}
            cancellingId={cancellingId} sortKey={sortKey} setSortKey={setSortKey} />
          {more > 0 && (
            <div className="sentinel" ref={sentinelRef}>loading {Math.min(PAGE, more)} more of {more}…</div>
          )}
        </div>
      ) : (
        <div className="runs" data-runscroll>
          {visible.map((r, idx) => (
            <RunCard key={r.id} run={r} selected={r.id === selectedId} onSelect={onSelect}
              onCancel={onCancel} cancelling={cancellingId === r.id}
              rowRef={(el) => { rowRefs.current[r.id] = el; }}
              onKeyDown={(e) => handleKeyDown(e, idx)} />
          ))}
          {more > 0 && (
            <div className="sentinel" ref={sentinelRef}>loading {Math.min(PAGE, more)} more of {more}…</div>
          )}
        </div>
      )}
    </section>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Equity chart — account level on top, drawdown as its own underwater
   panel beneath, sharing one time axis.
   ═══════════════════════════════════════════════════════════════════ */
const EQ_MODES = [['mtm', 'MtM total equity'], ['realized', 'Realized-only']];

function EquityChart({ points, capital, levelOf, footNote }) {
  const [showDD, setShowDD] = useState(true);
  const [tip, setTip] = useState(null);
  const boxRef = useRef(null);

  if (!points?.length) return <div className="empty">No equity data for this run.</div>;

  const vals = points.map(levelOf);
  const W = 900, M = { t: 16, r: 56, b: 34, l: 78 };
  const eqH = 216, GAP = showDD ? 20 : 0, DDH = showDD ? 68 : 0;
  const H = M.t + eqH + GAP + DDH + M.b;
  const pw = W - M.l - M.r;
  const xi = (i) => M.l + (i / Math.max(points.length - 1, 1)) * pw;

  let pk = -Infinity;
  const peaks = vals.map((v) => (pk = Math.max(pk, v)));
  const dds = vals.map((v, i) => (peaks[i] > 0 ? ((peaks[i] - v) / peaks[i]) * 100 : 0));
  const maxDD = Math.max(...dds, 0);
  const final = vals[vals.length - 1];
  const peak = Math.max(...vals);

  const lo = Math.min(capital, ...vals) * 0.97;
  const hi = Math.max(capital, ...vals) * 1.04;
  const y = (v) => M.t + eqH - ((v - lo) / (hi - lo || 1)) * eqH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + (hi - lo) * f);
  const line = vals.map((v, i) => `${i ? 'L' : 'M'}${xi(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const peakLine = peaks.map((v, i) => `${i ? 'L' : 'M'}${xi(i).toFixed(1)},${y(v).toFixed(1)}`).join('');

  const ddTop = M.t + eqH + GAP;
  const yd = (d) => ddTop + (d / Math.max(maxDD, 1)) * DDH;
  const ddLine = dds.map((d, i) => `${i ? 'L' : 'M'}${xi(i).toFixed(1)},${yd(d).toFixed(1)}`).join('');
  const trough = dds.indexOf(maxDD);

  const nTicks = Math.min(7, points.length);
  const tickIdx = Array.from({ length: nTicks }, (_, i) =>
    Math.round((i / Math.max(nTicks - 1, 1)) * (points.length - 1)));

  const onMove = (ev) => {
    const r = boxRef.current?.getBoundingClientRect();
    if (!r) return;
    const fx = (ev.clientX - r.left) / r.width;
    const i = Math.max(0, Math.min(points.length - 1,
      Math.round((((fx * W) - M.l) / pw) * (points.length - 1))));
    setTip({ i, left: Math.min(ev.clientX - r.left + 12, r.width - 175),
      top: Math.max(6, ev.clientY - r.top - 56) });
  };

  return (
    <div>
      <div className="chart-head">
        <label className="check">
          <input type="checkbox" checked={showDD} onChange={(e) => setShowDD(e.target.checked)} />
          Drawdown % overlay
        </label>
      </div>
      <div className="chart-legend" style={{ marginBottom: 6 }}>
        <span><i style={{ background: 'var(--c-line)' }} />Account equity</span>
        <span><i style={{ background: 'var(--c-hwm)' }} />High-water mark</span>
        {showDD && <span><i style={{ background: 'var(--loss)', opacity: 0.6 }} />Underwater</span>}
        <span className="tert">Start <b className="mono" style={{ color: 'var(--ink-muted)' }}>{fmtInrCompact(capital)}</b></span>
        <span className="tert">Peak <b className="mono" style={{ color: 'var(--ink-muted)' }}>{fmtInrCompact(peak)}</b></span>
        <span className="tert">End <b className="mono g">{fmtInrCompact(final)}</b></span>
        <span className="tert">Max DD <b className="mono l">−{maxDD.toFixed(1)}%</b></span>
      </div>
      <div className="chartbox" ref={boxRef}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`Account equity from ${fmtInrCompact(capital)} to ${fmtInrCompact(final)}, maximum drawdown ${maxDD.toFixed(1)} percent`}
          onMouseMove={onMove} onMouseLeave={() => setTip(null)}>
          <defs>
            <linearGradient id="btx-eqf" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--c-line)" stopOpacity="var(--c-fill)" />
              <stop offset="100%" stopColor="var(--c-line)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((v, i) => (
            <g key={i}>
              <line x1={M.l} y1={y(v)} x2={W - M.r} y2={y(v)} stroke="var(--c-grid)" strokeDasharray="3,3" />
              <text x={M.l - 8} y={y(v) + 3.5} fill="var(--c-axis)" fontSize="10"
                fontFamily="JetBrains Mono, monospace" textAnchor="end">{fmtInrCompact(v)}</text>
            </g>
          ))}
          <line x1={M.l} y1={y(capital)} x2={W - M.r} y2={y(capital)} stroke="var(--c-hwm)" strokeWidth="1.4" />
          <text x={M.l + 5} y={y(capital) - 5} fill="var(--c-axis)" fontSize="9"
            fontFamily="JetBrains Mono, monospace">start {fmtInrCompact(capital)}</text>
          <path d={`${line} L${xi(points.length - 1).toFixed(1)},${(M.t + eqH).toFixed(1)} L${M.l},${(M.t + eqH).toFixed(1)} Z`} fill="url(#btx-eqf)" />
          <path d={peakLine} fill="none" stroke="var(--c-hwm)" strokeWidth="1" strokeDasharray="4,3" />
          <path d={line} fill="none" stroke="var(--c-line)" strokeWidth="1.8" strokeLinejoin="round" />
          <circle cx={xi(points.length - 1)} cy={y(final)} r="3.4" fill="var(--c-line)" />
          <text x={xi(points.length - 1) - 8} y={y(final) - 9} fill="var(--ink)" fontSize="11"
            fontFamily="JetBrains Mono, monospace" textAnchor="end">{fmtInrCompact(final)}</text>

          {showDD && (
            <>
              <line x1={M.l} y1={ddTop} x2={W - M.r} y2={ddTop} stroke="var(--hair-strong)" />
              <path d={`${ddLine} L${xi(points.length - 1).toFixed(1)},${ddTop} L${M.l},${ddTop} Z`}
                fill="var(--loss)" fillOpacity="0.2" />
              <path d={ddLine} fill="none" stroke="var(--loss)" strokeWidth="1.1" />
              <circle cx={xi(trough)} cy={yd(maxDD)} r="3" fill="var(--loss)" />
              <text x={M.l - 8} y={ddTop + 4} fill="var(--c-axis)" fontSize="10"
                fontFamily="JetBrains Mono, monospace" textAnchor="end">0%</text>
              <text x={M.l - 8} y={ddTop + DDH + 4} fill="var(--c-axis)" fontSize="10"
                fontFamily="JetBrains Mono, monospace" textAnchor="end">−{maxDD.toFixed(1)}%</text>
              <text x={xi(trough) + 7} y={yd(maxDD) - 4} fill="var(--loss)" fontSize="10"
                fontFamily="JetBrains Mono, monospace">{String(points[trough]?.date ?? '').slice(0, 7)}</text>
            </>
          )}

          {tickIdx.map((idx) => (
            <text key={idx} x={xi(idx)} y={H - 10} fill="var(--c-axis)" fontSize="10"
              fontFamily="JetBrains Mono, monospace" textAnchor="middle">
              {String(points[idx]?.date ?? '').slice(0, 7)}
            </text>
          ))}
        </svg>
        {tip && (
          <div className="tip" style={{ left: tip.left, top: tip.top }}>
            <b>{points[tip.i]?.date}</b><br />
            equity <b>{fmtInrCompact(vals[tip.i])}</b><br />
            underwater <span className="l">−{dds[tip.i].toFixed(1)}%</span>
          </div>
        )}
      </div>
      <p className="tert" style={{ fontSize: 11, margin: '6px 2px 4px' }}>{footNote}</p>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Summary
   ═══════════════════════════════════════════════════════════════════ */
function Kpi({ label, value, tone, hint }) {
  return (
    <div className="kpi">
      <div className={`v ${tone || ''}`}>{value}</div>
      <div className="k">{label}</div>
      {hint ? <div className="h">{hint}</div> : null}
    </div>
  );
}
function Stat({ label, value, tone, hint, help }) {
  return (
    <div className="stat" title={help}>
      <div className={`v ${tone || ''}`}>{value}</div>
      <div className="k">{label}</div>
      {hint ? <div className="h">{hint}</div> : null}
    </div>
  );
}

function KpiBar({ run, summary }) {
  const cagr = getCagr(run), dd = getMaxDD(run);
  const winRate = summary?.quant?.count ? summary.quant.winRate : null;
  return (
    <div className="kpis">
      <Kpi label="CAGR" value={fmtCagr(cagr, isShortWindow(run))} hint="mark-to-market weekly"
        tone={(cagr ?? 0) >= 0 ? 'g' : 'l'} />
      <Kpi label="True MtM MaxDD" value={fmtMaxDD(dd)} tone="l" hint="what you sit through" />
      <Kpi label="Max underwater" value={fmtUwMonths(getMaxUwDays(run))} tone="w" hint="longest time below a prior peak" />
      <Kpi label="Calmar" value={calmar(run) === -Infinity ? '—' : calmar(run).toFixed(2)} hint="CAGR ÷ MaxDD" />
      <Kpi label="Win rate" value={winRate != null ? `${winRate}%` : '—'} tone="i"
        hint={summary?.quant?.count ? `${summary.quant.count.toLocaleString('en-IN')} closed` : undefined} />
    </div>
  );
}

function TrackStats({ title, stats, tone, capital }) {
  const totalWithOpen = (stats.totalPnl || 0) + (stats.unrealizedPnl || 0);
  const totalPct = capital ? (totalWithOpen / capital) * 100 : null;
  return (
    <section className="panel" style={{ padding: '12px 14px' }}>
      <div className={`eyebrow ${tone}`} style={{ marginBottom: 8 }}>{title}</div>
      <div className="stats" style={{ border: 'none', background: 'transparent', gap: 10 }}>
        <div><div className="v">{stats.count}</div><div className="k">Closed trades</div></div>
        <div><div className="v">{stats.winRate}%</div><div className="k">Win rate</div></div>
        <div>
          <div className={`v ${pnlCls(stats.totalPnl)}`}>{fmtInr(stats.totalPnl)}</div>
          <div className="k">Realized P&amp;L (net)</div>
          {!!stats.costDrag && (
            <div className="h">gross {fmtInrCompact(stats.totalGrossPnl)} · costs −{fmtInrCompact(stats.costDrag)}</div>
          )}
        </div>
        <div><div className="v">{fmtR(stats.avgR)}</div><div className="k">Avg R</div></div>
        <div>
          <div className={`v ${pnlCls(stats.unrealizedPnl)}`}>{fmtInr(stats.unrealizedPnl)}</div>
          <div className="k">Unrealized ({stats.openPositionCount ?? 0} open)</div>
        </div>
        <div>
          <div className="v w">{fmtInr(stats.maxDrawdown)}</div>
          <div className="k">Realized-only DD{stats.maxDrawdownPct != null ? ` (−${stats.maxDrawdownPct.toFixed(1)}%)` : ''}</div>
        </div>
        <div>
          <div className={`v ${stats.cagrPct == null ? 'tert' : stats.cagrPct >= 0 ? 'g' : 'l'}`}>
            {stats.cagrPct == null ? '—' : `${stats.cagrPct.toFixed(1)}%`}
          </div>
          <div className="k">CAGR</div>
        </div>
        <div><div className="v">{fmtInr(stats.deployed)}</div><div className="k">Capital deployed</div></div>
        <div>
          <div className={`v ${pnlCls(totalWithOpen)}`}>{fmtInr(totalWithOpen)} <span className="tert">{fmtPctS(totalPct)}</span></div>
          <div className="k">Total P&amp;L (realized + unrealized)</div>
        </div>
      </div>
    </section>
  );
}

function RunSummary({ run, runId, status }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');
  const [mode, setMode] = useState('mtm');

  useEffect(() => {
    let alive = true;
    setSummary(null);
    getBacktestSummary(runId).then((s) => alive && setSummary(s)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [runId, status]);

  if (error) return <div className="errbar">{error}</div>;
  if (!summary) return <div className="empty">Loading summary…</div>;

  const pf = summary.portfolio;
  const cap = summary.capital;
  const totalPct = cap && pf ? (pf.totalPnl / cap) * 100 : null;

  // Which series the chart draws. A PORTFOLIO run has the engine's own daily
  // mark-to-market; everything else is rebuilt from cumulative trade flows.
  const hasLevel = !!summary.portfolioEquity?.length;
  const points = hasLevel ? summary.portfolioEquity : (summary.equityCurve || []);
  const levelOf = hasLevel
    ? (p) => p.equity
    : (p) => cap + (p.quantRealizedCumPnl ?? 0) + (p.aiRealizedCumPnl ?? 0)
             + (mode === 'mtm' ? (p.quantUnrealizedPnl ?? 0) + (p.aiUnrealizedPnl ?? 0) : 0);
  const foot = hasLevel
    ? 'Engine mark-to-market. Dashed = running high-water mark; the panel below is percent under it. Sampled weekly.'
    : mode === 'mtm'
      ? 'Account level including open-position marks. Dashed = running high-water mark; the panel below is percent under it.'
      : 'Cash view — capital plus banked P&L only. Hides roughly half the true drawdown; that is why MtM is the default.';

  return (
    <div className="stack">
      {run && <KpiBar run={run} summary={summary} />}

      <section className="panel chartcard">
        <div className="chart-head">
          <h3>{hasLevel ? 'Account equity' : 'Equity curve'}</h3>
          {!hasLevel && (
            <div className="seg" role="group" aria-label="Equity view mode">
              {EQ_MODES.map(([id, label]) => (
                <button key={id} type="button" aria-pressed={mode === id} onClick={() => setMode(id)}>{label}</button>
              ))}
            </div>
          )}
        </div>
        <EquityChart key={`${runId}-${mode}-${hasLevel}`} points={points} capital={cap}
          levelOf={levelOf} footNote={foot} />
      </section>

      {pf ? (
        <>
          <div className="stats">
            <Stat label="Worst 12 months" value={`${pf.worst12mPct.toFixed(1)}%`} tone="l" help={HELP.worst12m} />
            <Stat label="Ulcer index" value={pf.ulcer.toFixed(2)} help={HELP.ulcer} />
            <Stat label="Martin ratio" value={pf.martin.toFixed(2)} help={HELP.martin} />
            <Stat label="Turnover / yr" value={`${pf.turnoverPerYr.toFixed(2)}×`} help={HELP.turnover} />
            <Stat label="Starting capital" value={fmtInrCompact(cap)} help={HELP.capital} />
            {run?.posTranches > 1 && (
              <Stat label="Tranches (per book)" value={`${run.posTranches} × ${fmtInrCompact(cap / run.posTranches)}`}
                hint={run.posPhaseDays ? `base phase ${run.posPhaseDays}` : undefined} />
            )}
            <Stat label="Total P&L" value={fmtInrCompact(pf.totalPnl)} tone={pf.totalPnl >= 0 ? 'g' : 'l'}
              hint={`ends at ${fmtInrCompact(pf.finalEquity)}`} help={HELP.total} />
            <Stat label="Total P&L % of capital" value={fmtPctS(totalPct)} tone={pf.totalPnl >= 0 ? 'g' : 'l'} />
            <Stat label="Closed trades" value={summary.quant?.count ?? '—'} help={HELP.trades}
              hint={summary.quant?.winRate != null ? `${summary.quant.winRate}% won` : undefined} />
          </div>
          {pf.shortWindow && (
            <div className="warnbar">
              ⚠ This run covers under two years and restarts at the initial capital. Its CAGR annualises a
              single short window, and its P&amp;L cannot be added to — or compared with — the compounded
              continuous run.
            </div>
          )}
        </>
      ) : (() => {
        const aiEmpty = !(summary.ai?.count || summary.ai?.openPositionCount);
        return (
          <div className="daygrid">
            <TrackStats title="📐 Quant track" stats={summary.quant} tone="q" capital={cap} />
            {!aiEmpty && <TrackStats title="🤖 AI track" stats={summary.ai} tone="a" capital={cap} />}
          </div>
        );
      })()}

      <div className="bookbar">
        <span>Open positions <b className="i">{summary.openCount}</b></span>
        <span>Pending orders <b className="tert">{summary.pendingCount}</b></span>
        <span>Capital deployed <b>{fmtInr(summary.totalDeployed)}</b> of <b>{fmtInr(cap)}</b></span>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Day drill-down
   ═══════════════════════════════════════════════════════════════════ */
function RankChips({ t }) {
  if (t.quantRank == null && t.aiRank == null) return <span className="c2">unranked</span>;
  return (
    <span className="rank">
      {t.quantRank != null && <span className="rk q">Q{t.quantRank}</span>}
      {t.aiRank != null && <span className="rk a">AI{t.aiRank}</span>}
    </span>
  );
}

function DaySection({ title, rows, extra }) {
  return (
    <div className="panel">
      <div className="panel-head"><h2>{title} <span className="tert">({rows.length})</span></h2></div>
      {rows.length ? (
        <table className="minitable">
          <tbody>
            {rows.map((t) => {
              const [dot, cls] = TRADE_DOT[t.status] || TRADE_DOT.CLOSED;
              return (
                <tr key={t.id}>
                  <td style={{ color: 'var(--ink)', fontWeight: 500 }}>{t.symbol}</td>
                  <td style={{ width: 84 }}><RankChips t={t} /></td>
                  <td><span className={`st ${cls}`}><i style={{ background: dot }} />{t.status}</span></td>
                  <td className="right">{extra(t)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <div className="empty">Nothing here.</div>}
    </div>
  );
}

function DayDrilldown({ runId, minDate, maxDate }) {
  const [d, setD] = useState(minDate || '');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => { setD(minDate || ''); }, [runId, minDate]);

  useEffect(() => {
    if (!d) return;
    let alive = true;
    setError('');
    getBacktestDay(runId, d)
      .then((r) => { if (alive) setData(r); })
      .catch((e) => { if (alive) { setData(null); setError(e.message); } });
    return () => { alive = false; };
  }, [d, runId]);

  const step = (delta) => {
    if (!d) return;
    const next = addDays(d, delta);
    if (minDate && next < minDate) return;
    if (maxDate && next > maxDate) return;
    setD(next);
  };

  const pctOfAlloc = (t, v) => (t.allocation ? (100 * v) / t.allocation : null);

  return (
    <div className="stack">
      <div className="panel" style={{ padding: '10px 14px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="check" style={{ color: 'var(--ink-tert)' }}>
          Date
          <input className="ctl" type="date" value={d} min={minDate} max={maxDate}
            onChange={(e) => setD(e.target.value)} />
        </label>
        <button type="button" className="btn btn-sm" onClick={() => step(-1)}
          disabled={!d || (minDate && d <= minDate)}>← Prev</button>
        <button type="button" className="btn btn-sm" onClick={() => step(1)}
          disabled={!d || (maxDate && d >= maxDate)}>Next →</button>
        {data && (
          <span className="eyebrow" style={{ marginLeft: 'auto' }}>
            {data.ordersFilled.length} filled · {data.closedToday.length} closed · {data.openPositions.length} held
          </span>
        )}
      </div>

      {error && <div className="errbar">{error}</div>}

      {data && (
        <>
          <div className="daygrid">
            <DaySection title="Picks" rows={data.picks} extra={(t) => fmtInr(t.entryTriggerPrice)} />
            <DaySection title="Orders filled" rows={data.ordersFilled} extra={(t) => fmtInr(t.entryFillPrice)} />
            <DaySection title="Closed today" rows={data.closedToday} extra={(t) => (
              <>
                <span className={pnlCls(t.realizedPnl)}>{fmtInr(t.realizedPnl)}</span>{' '}
                <span className={pnlCls(t.realizedPnl)}>{fmtPctS(pctOfAlloc(t, t.realizedPnl))}</span>{' '}
                <span className="tert">· {t.exitReason}</span>
              </>
            )} />
            <DaySection title="Open positions" rows={data.openPositions} extra={(t) => (
              t.status === 'OPEN' ? (
                <>
                  <span className={pnlCls(t.unrealizedPnl)}>{fmtInr(t.unrealizedPnl)}</span>{' '}
                  <span className={pnlCls(t.unrealizedPnl)}>{fmtPctS(pctOfAlloc(t, t.unrealizedPnl))}</span>
                </>
              ) : <span className="tert">resting</span>
            )} />
          </div>
          <div className="panel" style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span className="muted">Realized P&amp;L to date</span>
            <span className={`n ${pnlCls(data.realizedPnlToDate)}`} style={{ fontSize: 17, fontWeight: 500 }}>
              {fmtInr(data.realizedPnlToDate)}
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Trade log
   ═══════════════════════════════════════════════════════════════════ */
// Return % of capital committed — realized for a closed trade, mark-to-market
// for an open one. This is what the rupee columns cannot tell you: a ₹1.7L gain
// on ₹3L committed and on ₹12L committed are not the same trade.
const retPct = (t) => {
  if (!t.allocation) return null;
  if (t.realizedPnl != null) return (100 * t.realizedPnl) / t.allocation;
  if (t.status === 'OPEN' && t.unrealizedPnl != null) return (100 * t.unrealizedPnl) / t.allocation;
  return null;
};
const TRADE_SORT = {
  realizedPnl: (t) => t.realizedPnl ?? t.unrealizedPnl ?? 0,
  unrealizedPnl: (t) => t.unrealizedPnl ?? 0,
  returnPct: (t) => retPct(t) ?? 0,
  rMultiple: (t) => t.rMultiple ?? 0,
  holdingDays: (t) => t.holdingDays ?? 0,
  allocation: (t) => t.allocation ?? 0,
  entryTs: (t) => (t.entryFillDate ? new Date(t.entryFillDate).getTime() : 0),
};

const TRADE_CSV_FIELDS = ['symbol','quantRank','aiRank','signalDate','entryFillDate',
  'entryFillPrice','exitDate','exitPrice','exitReason','quantity','allocation',
  'realizedPnl','grossPnl','unrealizedPnl','rMultiple','holdingDays','status'];

function exportTradesCsv(rows, runId) {
  if (!rows?.length) return;
  const header = [...TRADE_CSV_FIELDS, 'returnPct'].join(',');
  const lines = rows.map((t) => [
    ...TRADE_CSV_FIELDS.map((f) => {
      const v = t[f];
      if (v == null) return '';
      const str = String(v);
      return str.includes(',') ? `"${str}"` : str;
    }),
    retPct(t) != null ? retPct(t).toFixed(2) : '',
  ].join(','));
  const blob = new Blob([[header, ...lines].join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `backtest_run_${runId}_trades.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

const fmtScatterInr = (v) => {
  const a = Math.abs(v);
  if (a >= 1e7) return `₹${(v / 1e7).toFixed(1)}Cr`;
  if (a >= 1e5) return `₹${(v / 1e5).toFixed(1)}L`;
  if (a >= 1e3) return `₹${(v / 1e3).toFixed(0)}k`;
  return `₹${Math.round(v)}`;
};
const fmtMonth = (ts) => new Date(ts).toISOString().slice(0, 7);

const SCATTER_PLOTS = {
  hold_ret:  { label: 'Holding days vs Return %',
    x: { k: 'holdingDays', label: 'holding period (days)', fmt: (v) => `${Math.round(v)}d` },
    y: { k: 'returnPct', label: 'return %', fmt: (v) => `${v.toFixed(1)}%`, zero: true } },
  date_ret:  { label: 'Entry date vs Return %',
    x: { k: 'entryTs', label: 'entry date', fmt: fmtMonth },
    y: { k: 'returnPct', label: 'return %', fmt: (v) => `${v.toFixed(1)}%`, zero: true } },
  date_pnl:  { label: 'Entry date vs Realized P&L',
    x: { k: 'entryTs', label: 'entry date', fmt: fmtMonth },
    y: { k: 'realizedPnl', label: 'realized P&L', fmt: fmtScatterInr, zero: true } },
  rank_ret:  { label: 'Entry rank vs Return %',
    x: { k: 'rank', label: 'quant rank at entry', fmt: (v) => `#${Math.round(v)}` },
    y: { k: 'returnPct', label: 'return %', fmt: (v) => `${v.toFixed(1)}%`, zero: true } },
  alloc_ret: { label: 'Position size vs Return %',
    x: { k: 'allocation', label: 'capital committed', fmt: fmtScatterInr },
    y: { k: 'returnPct', label: 'return %', fmt: (v) => `${v.toFixed(1)}%`, zero: true } },
  hold_pnl:  { label: 'Holding days vs Realized P&L',
    x: { k: 'holdingDays', label: 'holding period (days)', fmt: (v) => `${Math.round(v)}d` },
    y: { k: 'realizedPnl', label: 'realized P&L', fmt: fmtScatterInr, zero: true } },
};

function TradeScatter({ trades }) {
  const [plotKey, setPlotKey] = useState('hold_ret');
  const [tip, setTip] = useState(null);
  const boxRef = useRef(null);
  const plot = SCATTER_PLOTS[plotKey];

  const base = useMemo(() => trades
    .filter((t) => t.status === 'CLOSED' && t.allocation > 0 && t.realizedPnl != null)
    .map((t) => ({
      sym: t.symbol, exit: t.exitReason || '—', rank: t.quantRank,
      entryDate: t.entryFillDate, exitDate: t.exitDate,
      holdingDays: t.holdingDays, allocation: t.allocation, realizedPnl: t.realizedPnl,
      returnPct: (100 * t.realizedPnl) / t.allocation,
      entryTs: t.entryFillDate ? new Date(t.entryFillDate).getTime() : null,
    })), [trades]);

  const pts = useMemo(() => base.filter((d) => d[plot.x.k] != null && d[plot.y.k] != null), [base, plot]);
  if (base.length < 3) return null;

  const W = 900, H = 300, L = 66, R = 16, T = 14, B = 40;
  const pw = W - L - R, ph = H - T - B;
  const xs = pts.map((d) => d[plot.x.k]), ys = pts.map((d) => d[plot.y.k]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  if (plot.y.zero) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
  if (x1 === x0) x1 = x0 + 1;
  if (y1 === y0) y1 = y0 + 1;
  const px = (x1 - x0) * 0.04, py = (y1 - y0) * 0.07;
  x0 -= px; x1 += px; y0 -= py; y1 += py;
  const sx = (v) => L + (pw * (v - x0)) / (x1 - x0);
  const sy = (v) => T + ph * (1 - (v - y0) / (y1 - y0));
  const wins = pts.filter((d) => d.returnPct > 0).length;

  const show = (e, d) => {
    const rect = boxRef.current?.getBoundingClientRect();
    if (!rect) return;
    let left = e.clientX - rect.left + 14;
    if (left > rect.width - 210) left -= 232;
    setTip({ left, top: Math.max(4, e.clientY - rect.top - 20), d });
  };

  return (
    <section className="panel chartcard">
      <div className="chart-head">
        <h3>Closed-trade scatter</h3>
        <select className="ctl" value={plotKey} aria-label="Scatter axes"
          onChange={(e) => { setPlotKey(e.target.value); setTip(null); }}>
          {Object.entries(SCATTER_PLOTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <span className="tert" style={{ fontSize: 11 }}>
          {pts.length} closed trades · {((100 * wins) / (pts.length || 1)).toFixed(1)}% winners · hover a dot for details
        </span>
      </div>
      <div className="chartbox" ref={boxRef}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Scatter of closed trades: ${plot.label}`}
          onMouseLeave={() => setTip(null)}>
          {[0.15, 0.4, 0.65, 0.9].map((f, i) => {
            const v = y0 + (y1 - y0) * f;
            return (
              <g key={`y${i}`}>
                <line x1={L} y1={sy(v)} x2={W - R} y2={sy(v)} stroke="var(--c-grid)" />
                <text x={L - 7} y={sy(v) + 3.5} fill="var(--c-axis)" fontSize="10"
                  fontFamily="JetBrains Mono, monospace" textAnchor="end">{plot.y.fmt(v)}</text>
              </g>
            );
          })}
          {[0.08, 0.32, 0.56, 0.8].map((f, i) => {
            const v = x0 + (x1 - x0) * f;
            return (
              <g key={`x${i}`}>
                <line x1={sx(v)} y1={T} x2={sx(v)} y2={T + ph} stroke="var(--c-grid)" />
                <text x={sx(v)} y={T + ph + 15} fill="var(--c-axis)" fontSize="10"
                  fontFamily="JetBrains Mono, monospace" textAnchor="middle">{plot.x.fmt(v)}</text>
              </g>
            );
          })}
          {plot.y.zero && y0 < 0 && (
            <g>
              <line x1={L} y1={sy(0)} x2={W - R} y2={sy(0)} stroke="var(--ink-subtle)" strokeDasharray="4,3" />
              <text x={L - 7} y={sy(0) + 3.5} fill="var(--ink-subtle)" fontSize="10"
                fontFamily="JetBrains Mono, monospace" textAnchor="end">0</text>
            </g>
          )}
          {pts.map((d, i) => (
            <circle key={i} cx={sx(d[plot.x.k])} cy={sy(d[plot.y.k])} r="3.4"
              fill={d.returnPct > 0 ? 'var(--gain)' : 'var(--loss)'} fillOpacity="0.62"
              stroke="transparent" strokeWidth="7"
              onMouseEnter={(e) => show(e, d)} onMouseMove={(e) => show(e, d)} />
          ))}
          <text x={(L + W - R) / 2} y={H - 5} fill="var(--c-axis)" fontSize="10" textAnchor="middle">{plot.x.label}</text>
        </svg>
        {tip && (
          <div className="tip" style={{ left: tip.left, top: tip.top }}>
            <b>{tip.d.sym}{tip.d.rank ? ` · Q${tip.d.rank}` : ''}</b><br />
            {plot.x.label}: <b>{plot.x.fmt(tip.d[plot.x.k])}</b><br />
            {plot.y.label}: <span className={tip.d[plot.y.k] > 0 ? 'g' : 'l'}>{plot.y.fmt(tip.d[plot.y.k])}</span><br />
            return <span className={tip.d.returnPct > 0 ? 'g' : 'l'}>{tip.d.returnPct.toFixed(1)}%</span>
            {' · '}P&amp;L {fmtScatterInr(tip.d.realizedPnl)}<br />
            held {tip.d.holdingDays}d · alloc {fmtScatterInr(tip.d.allocation)}<br />
            <span className="tert">{tip.d.entryDate} → {tip.d.exitDate || '—'} · {tip.d.exit}</span>
          </div>
        )}
      </div>
    </section>
  );
}

function TradeChartModal({ runId, trade, onClose }) {
  useEffect(() => {
    if (!trade) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [trade, onClose]);
  if (!trade) return null;
  return (
    <div className="btx btx-backdrop" onClick={onClose}>
      <div className="btx-modal" role="dialog" aria-modal="true"
        aria-label={`${trade.symbol} trade chart`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="t">{trade.symbol} · trade #{trade.id} · {trade.status}</span>
          <span className="tert" style={{ fontSize: 12 }}>
            {trade.entryFillDate || trade.signalDate} → {trade.exitDate || 'open'} · {trade.exitReason || 'open position'}
            {retPct(trade) != null && (
              <span className={retPct(trade) > 0 ? 'g' : 'l'}> · {fmtPctS(retPct(trade))}</span>
            )}
          </span>
          <button type="button" className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>✕</button>
        </div>
        <div className="modal-body" style={{ padding: 0 }}>
          <img src={backtestTradeChartUrl(runId, trade.id)} alt={`${trade.symbol} chart`}
            style={{ width: '100%', height: 'auto', display: 'block' }} />
        </div>
      </div>
    </div>
  );
}

function TradeLog({ runId }) {
  const [track, setTrack] = useState('');
  const [status, setStatus] = useState('');
  const [exitReason, setExitReason] = useState('');
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState('desc');
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [chartTrade, setChartTrade] = useState(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getBacktestTrades(runId)
      .then((t) => { if (alive) { setTrades(t); setLoading(false); } })
      .catch((e) => { if (alive) { setError(e.message); setLoading(false); } });
    return () => { alive = false; };
  }, [runId]);

  const exitReasons = useMemo(
    () => Array.from(new Set(trades.map((t) => t.exitReason).filter(Boolean))).sort(), [trades]);

  const rows = useMemo(() => {
    let out = trades;
    if (track === 'quant') out = out.filter((t) => t.quantRank != null);
    else if (track === 'ai') out = out.filter((t) => t.aiRank != null);
    if (status) out = out.filter((t) => t.status === status);
    if (exitReason) out = out.filter((t) => t.exitReason === exitReason);
    if (sortKey) {
      const get = TRADE_SORT[sortKey];
      out = [...out].sort((a, b) => (sortDir === 'asc' ? get(a) - get(b) : get(b) - get(a)));
    }
    return out;
  }, [trades, track, status, exitReason, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(key); setSortDir('desc'); }
  };
  const arrow = (k) => (sortKey === k ? (sortDir === 'desc' ? ' ▼' : ' ▲') : '');
  const th = (k, label, cls) => (
    <th className={`sortable ${cls || ''}`} title="Click to sort" onClick={() => toggleSort(k)}
      aria-sort={sortKey === k ? (sortDir === 'desc' ? 'descending' : 'ascending') : 'none'}>
      {label}{arrow(k)}
    </th>
  );

  const closed = rows.filter((t) => t.realizedPnl != null);
  const openRows = rows.filter((t) => t.status === 'OPEN');
  const net = closed.reduce((a, t) => a + t.realizedPnl, 0);
  const unreal = openRows.reduce((a, t) => a + (t.unrealizedPnl || 0), 0);
  const wins = closed.filter((t) => t.realizedPnl > 0).length;
  const avgR = closed.length ? closed.reduce((a, t) => a + (t.rMultiple || 0), 0) / closed.length : 0;

  return (
    <div className="stack">
      <div className="panel" style={{ padding: '10px 14px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="ctl" value={track} onChange={(e) => setTrack(e.target.value)}>
          <option value="">All tracks</option>
          <option value="quant">Quant</option>
          <option value="ai">AI</option>
        </select>
        <select className="ctl" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="PENDING">Pending</option>
          <option value="OPEN">Open</option>
          <option value="CLOSED">Closed</option>
          <option value="SUPERSEDED">Superseded</option>
        </select>
        <select className="ctl" value={exitReason} onChange={(e) => setExitReason(e.target.value)}>
          <option value="">All exit reasons</option>
          {exitReasons.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button type="button" className="btn btn-sm" disabled={!rows.length}
          onClick={() => exportTradesCsv(rows, runId)}>⬇ Export CSV</button>
        {sortKey && (
          <button type="button" className="btn btn-sm"
            onClick={() => { setSortKey(null); setSortDir('desc'); }}>Clear sort</button>
        )}
        <span className="tert" style={{ fontSize: 11.5, marginLeft: 'auto', marginRight: 6 }}>
          Click a symbol to open its chart
        </span>
        <span className="eyebrow">{rows.length} of {trades.length} rows</span>
      </div>

      {error && <div className="errbar">{error}</div>}

      {!loading && <TradeScatter trades={rows} />}

      <section className="panel">
        <div className="tablewrap">
          {loading ? (
            <div className="empty">Loading trades…</div>
          ) : !rows.length ? (
            <div className="empty">No trades match these filters.</div>
          ) : (
            <table className="tl">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Status</th>
                  {th('entryTs', 'Entry ₹', 'right')}
                  <th className="right">Exit ₹</th>
                  <th className="right">Trail SL</th>
                  <th>Reason</th>
                  {th('holdingDays', 'Held', 'right')}
                  {th('allocation', 'Alloc', 'right')}
                  <th className="right">
                    P&amp;L{' '}
                    <span className={`sk ${sortKey === 'realizedPnl' ? 'on' : ''}`}
                      onClick={(e) => { e.stopPropagation(); toggleSort('realizedPnl'); }}
                      title="Sort by rupees">₹{arrow('realizedPnl')}</span>
                    <span className="tert"> / </span>
                    <span className={`sk ${sortKey === 'returnPct' ? 'on' : ''}`}
                      onClick={(e) => { e.stopPropagation(); toggleSort('returnPct'); }}
                      title="Sort by % of capital committed">%{arrow('returnPct')}</span>
                  </th>
                  {th('rMultiple', 'R', 'right')}
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => {
                  const rp = retPct(t);
                  const pnl = t.realizedPnl != null ? t.realizedPnl : (t.status === 'OPEN' ? t.unrealizedPnl : null);
                  const moved = t.trailSl != null && t.structuralSl != null && t.trailSl > t.structuralSl * 1.03;
                  const [dot, cls] = TRADE_DOT[t.status] || TRADE_DOT.CLOSED;
                  return (
                    <tr key={t.id} className={trackRowClass(t)}>
                      <td title={`signal ${t.signalDate} · trigger ${fmtInr(t.entryTriggerPrice)}`}>
                        <button type="button" className="symbtn" onClick={() => setChartTrade(t)}
                          title={`Open ${t.symbol} chart`}>{t.symbol}</button>
                        <div><RankChips t={t} /></div>
                      </td>
                      <td><span className={`st ${cls}`}><i style={{ background: dot }} />
                        {t.status[0] + t.status.slice(1).toLowerCase()}</span></td>
                      <td className="right">
                        <div className="c1">{t.entryFillPrice != null ? fmtPx(t.entryFillPrice) : <span className="tert">not filled</span>}</div>
                        <div className="c2">{shortDate(t.entryFillDate)}</div>
                      </td>
                      <td className="right">
                        <div className="c1">{t.exitPrice != null ? fmtPx(t.exitPrice) : <span className="tert">—</span>}</div>
                        <div className="c2">{shortDate(t.exitDate)}</div>
                      </td>
                      <td className="right">
                        <div className="c1 w">{t.trailSl != null ? fmtPx(t.trailSl) : '—'}</div>
                        {moved && <div className="c2">ratcheted</div>}
                      </td>
                      <td><span className="c1" style={{ fontSize: 12 }}>{t.exitReason || <span className="tert">—</span>}</span></td>
                      <td className="right"><span className="c1">{t.holdingDays != null ? t.holdingDays : '—'}</span>
                        <span className="c2" style={{ display: 'inline' }}> d</span></td>
                      <td className="right"><span className="c1">{fmtInrCompact(t.allocation)}</span></td>
                      <td className="right">
                        <div className={`c1 ${pnlCls(pnl)}`} style={{ fontWeight: 600 }}>{pnl != null ? fmtInr(pnl) : '—'}</div>
                        <div className={`c2 ${pnlCls(rp)}`}>{fmtPctS(rp)}{t.status === 'OPEN' ? ' mtm' : ''}</div>
                      </td>
                      <td className="right"><span className={`c1 ${pnlCls(t.rMultiple)}`}>{fmtR(t.rMultiple)}</span></td>
                    </tr>
                  );
                })}
              </tbody>
              {!!closed.length && (
                <tfoot>
                  <tr>
                    <td>{closed.length} closed</td>
                    <td>{openRows.length} open</td>
                    <td colSpan={5}>win rate <span className="i">{((100 * wins) / closed.length).toFixed(1)}%</span></td>
                    <td className="right">net</td>
                    <td className="right">
                      <span className={pnlCls(net)} style={{ fontWeight: 600 }}>{fmtInr(net)}</span>
                      {!!unreal && <div className={`c2 ${pnlCls(unreal)}`}>{fmtInr(unreal)} open</div>}
                    </td>
                    <td className="right"><span className={pnlCls(avgR)}>{fmtR(avgR)}</span></td>
                  </tr>
                </tfoot>
              )}
            </table>
          )}
        </div>
      </section>

      <TradeChartModal runId={runId} trade={chartTrade} onClose={() => setChartTrade(null)} />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════
   Page
   ═══════════════════════════════════════════════════════════════════ */
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
      setSelectedId((prev) => (prev == null && rs.length ? rs[0].id : prev));
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
    pollRef.current = setInterval(() => { if (!document.hidden) refresh(); }, 15000);
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

  const selectRun = (id) => {
    setSelectedId(id);
    setDetailTab('summary');
    setSelectionNonce((n) => n + 1);
    refresh();
  };

  return (
    <div className="btx">
      <div className="work">
        <div className="stack">
          <CompactBacktestForm
            open={formOpen}
            onToggleOpen={setFormOpen}
            onCreated={(id) => { selectRun(id); refresh(); }}
            blocked={!!running}
            blockedReason={running ? `Run #${running.id} in progress` : ''}
          />
          {error && <div className="errbar">{error}</div>}
          <RunList runs={runs} selectedId={selectedId} onSelect={selectRun}
            onCancel={cancel} cancellingId={cancellingId} />
        </div>

        <div className="stack">
          {!selected ? (
            <div className="panel empty">Select a run on the left to see its summary.</div>
          ) : (
            <>
              <div className="runhead">
                <span className="title">Run #{selected.id}</span>
                <span className="window">
                  {selected.startDate} → {selected.endDate} · {selected.strategy} · {selected.trackMode}
                  {' · '}{fmtInrCompact(selected.capital)}
                  {selected.posTranches > 1 ? ` · ${selected.posTranches}T` : ''}
                  {selected.status === 'RUNNING' && selected.progressTotalDays
                    ? ` · day ${selected.progressDay}/${selected.progressTotalDays}` : ''}
                </span>
                {selected.error && <span className="l" style={{ fontSize: 12 }}>{selected.error}</span>}
                {selected.status === 'RUNNING' && (
                  <button type="button" className="btn btn-sm btn-danger" disabled={cancellingId === selected.id}
                    onClick={() => cancel(selected.id)}>
                    {cancellingId === selected.id ? 'Stopping…' : 'Stop run'}
                  </button>
                )}
                <div className="seg" role="tablist" style={{ marginLeft: 'auto' }}>
                  {DETAIL_TABS.map((t) => (
                    <button key={t.id} type="button" role="tab" aria-selected={detailTab === t.id}
                      onClick={() => setDetailTab(t.id)}>{t.label}</button>
                  ))}
                </div>
              </div>

              {selected.status === 'RUNNING' && (
                <div className="warnbar">
                  Run in progress — results below update once trades are simulated. If progress hasn&apos;t
                  moved in a while, use Stop to unblock the next run.
                </div>
              )}

              {detailTab === 'summary' && (
                <RunSummary key={`${selected.id}-${selectionNonce}`} run={selected}
                  runId={selected.id} status={selected.status} />
              )}
              {detailTab === 'day' && (
                <DayDrilldown runId={selected.id} minDate={selected.startDate} maxDate={selected.endDate} />
              )}
              {detailTab === 'trades' && <TradeLog runId={selected.id} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
