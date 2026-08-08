import React, { useEffect, useRef, useState } from 'react';
import {
  createBacktestRun, listBacktestRuns, getBacktestRun, getBacktestSummary,
  getBacktestTrades, getBacktestDay,
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

// ---------------- Run config form ----------------

const DEFAULT_FORM = {
  start_date: '', end_date: '', track_mode: 'BOTH', capital: 400000,
  restIndefinite: true, resting_window_days: 5,
  stacking_guard: false, stacking_guard_mode: 'SKIP',
  breakeven: true, half_booking: true, trailing: true, fixed_target: true,
  notes: '',
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

function RunConfigForm({ onCreated, blocked, blockedReason }) {
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
      const payload = {
        start_date: f.start_date, end_date: f.end_date, track_mode: f.track_mode,
        capital: Number(f.capital) || 400000,
        resting_window_days: f.restIndefinite ? null : Number(f.resting_window_days) || null,
        stacking_guard: f.stacking_guard,
        stacking_guard_mode: f.stacking_guard ? f.stacking_guard_mode : null,
        exit_config: {
          breakeven: f.breakeven, half_booking: f.half_booking,
          trailing: f.trailing, fixed_target: f.fixed_target,
        },
        notes: f.notes || null,
      };
      const res = await createBacktestRun(payload);
      onCreated(res.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="bg-slate-900/60 border border-slate-700 rounded-lg p-4 space-y-4">
      <div className="text-sm font-semibold text-slate-200">New Backtest Run</div>

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
            <option value="BOTH">Both (top-3 + top-3)</option>
          </select>
        </label>
        <label className="text-xs text-slate-400 flex flex-col gap-1">
          Capital (₹)
          <input type="number" value={f.capital} onChange={(e) => set('capital')(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" min="10000" step="10000" />
        </label>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1 border-t border-slate-800">
        <div className="space-y-2 pt-3">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Entry order</div>
          <Toggle label="Rest indefinitely until window ends" checked={f.restIndefinite}
            onChange={set('restIndefinite')} />
          {!f.restIndefinite && (
            <label className="text-xs text-slate-400 flex items-center gap-2 ml-6">
              Expire after
              <input type="number" min="1" value={f.resting_window_days}
                onChange={(e) => set('resting_window_days')(e.target.value)}
                className="w-16 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm" />
              trading days unfilled
            </label>
          )}
          <Toggle label="Position-stacking guard" checked={f.stacking_guard}
            onChange={set('stacking_guard')}
            hint="OPEN positions in a symbol always skip a new pick; this controls the PENDING case." />
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

        <div className="space-y-2 pt-3">
          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Exit rules (always-on: −8% intraday + close-based structural SL)</div>
          <Toggle label="Breakeven move at +1R" checked={f.breakeven} onChange={set('breakeven')} />
          <Toggle label="Half-book + trail rest at +2R" checked={f.half_booking} onChange={set('half_booking')} />
          <Toggle label="Trailing stop ladder" checked={f.trailing} onChange={set('trailing')} />
          <Toggle label="Fixed target exit (2R)" checked={f.fixed_target} onChange={set('fixed_target')} />
        </div>
      </div>

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

function RunRow({ run, selected, onSelect }) {
  const pct = run.progressTotalDays ? Math.round((run.progressDay / run.progressTotalDays) * 100) : null;
  return (
    <tr onClick={() => onSelect(run.id)}
      className={`cursor-pointer border-t border-slate-800 hover:bg-slate-800/40 ${selected ? 'bg-slate-800/60' : ''}`}>
      <td className="py-2 px-3 text-sm text-slate-200">#{run.id}</td>
      <td className="py-2 px-3 text-sm text-slate-300">{run.startDate} → {run.endDate}</td>
      <td className="py-2 px-3 text-sm text-slate-300">{run.trackMode}</td>
      <td className="py-2 px-3 text-sm text-slate-300">{fmtInr(run.capital)}</td>
      <td className={`py-2 px-3 text-sm font-semibold ${STATUS_COLOR[run.status] || 'text-slate-300'}`}>
        {run.status}
        {run.status === 'RUNNING' && pct != null && <span className="text-slate-400 font-normal"> · {pct}%</span>}
      </td>
      <td className="py-2 px-3 text-sm text-slate-300">{run.tradeCount ?? '—'}</td>
    </tr>
  );
}

function RunList({ runs, selectedId, onSelect }) {
  if (!runs.length) return <div className="text-sm text-slate-400 px-1">No backtest runs yet — configure one above.</div>;
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg overflow-x-auto">
      <table className="w-full min-w-[560px]">
        <thead>
          <tr className="text-left text-[11px] text-slate-500 uppercase tracking-wide">
            <th className="py-2 px-3">Run</th>
            <th className="py-2 px-3">Window</th>
            <th className="py-2 px-3">Track</th>
            <th className="py-2 px-3">Capital</th>
            <th className="py-2 px-3">Status</th>
            <th className="py-2 px-3">Trades</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => <RunRow key={r.id} run={r} selected={r.id === selectedId} onSelect={onSelect} />)}
        </tbody>
      </table>
    </div>
  );
}

// ---------------- Equity curve (lightweight inline SVG, no chart lib) ----------------

function EquityCurve({ points }) {
  if (!points.length) return <div className="text-sm text-slate-500 py-8 text-center">No closed trades yet.</div>;
  const W = 700, H = 220, PAD = 32;
  const all = points.flatMap((p) => [p.quantCumPnl, p.aiCumPnl]).filter((v) => v != null);
  const min = Math.min(0, ...all), max = Math.max(0, ...all);
  const range = max - min || 1;
  const x = (i) => PAD + (i / Math.max(points.length - 1, 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - ((v - min) / range) * (H - 2 * PAD);
  const path = (key) => points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p[key])}`).join(' ');
  const zeroY = y(0);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-56">
      <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="#475569" strokeDasharray="3,3" />
      <path d={path('quantCumPnl')} fill="none" stroke="#38bdf8" strokeWidth="2" />
      <path d={path('aiCumPnl')} fill="none" stroke="#c084fc" strokeWidth="2" />
      <text x={PAD} y={16} fontSize="10" fill="#38bdf8">● Quant</text>
      <text x={PAD + 60} y={16} fontSize="10" fill="#c084fc">● AI</text>
      <text x={W - PAD} y={16} fontSize="10" fill="#94a3b8" textAnchor="end">{points[points.length - 1]?.date}</text>
    </svg>
  );
}

function KpiCard({ title, stats, color }) {
  return (
    <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4">
      <div className={`text-sm font-semibold mb-2 ${color}`}>{title}</div>
      <div className="grid grid-cols-2 gap-3">
        <div><div className="text-lg font-bold text-slate-100">{stats.count}</div><div className="text-[10px] text-slate-400 uppercase">Trades</div></div>
        <div><div className="text-lg font-bold text-slate-100">{stats.winRate}%</div><div className="text-[10px] text-slate-400 uppercase">Win rate</div></div>
        <div><div className={`text-lg font-bold ${pnlColor(stats.totalPnl)}`}>{fmtInr(stats.totalPnl)}</div><div className="text-[10px] text-slate-400 uppercase">Total P&amp;L</div></div>
        <div><div className="text-lg font-bold text-slate-100">{fmtR(stats.avgR)}</div><div className="text-[10px] text-slate-400 uppercase">Avg R</div></div>
        <div className="col-span-2"><div className="text-lg font-bold text-amber-300">{fmtInr(stats.maxDrawdown)}</div><div className="text-[10px] text-slate-400 uppercase">Max drawdown</div></div>
      </div>
    </div>
  );
}

function RunSummary({ runId }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    getBacktestSummary(runId).then((s) => alive && setSummary(s)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [runId]);

  if (error) return <div className="text-sm text-red-300">{error}</div>;
  if (!summary) return <div className="text-sm text-slate-400">Loading summary…</div>;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <KpiCard title="📐 Quant track" stats={summary.quant} color="text-sky-300" />
        <KpiCard title="🤖 AI track" stats={summary.ai} color="text-purple-300" />
      </div>
      <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-4">
        <div className="text-sm font-semibold text-slate-200 mb-2">Equity curve (cumulative realized P&amp;L)</div>
        <EquityCurve points={summary.equityCurve} />
      </div>
      <div className="flex gap-4 text-sm text-slate-300">
        <span>Open positions: <b className="text-blue-300">{summary.openCount}</b></span>
        <span>Pending orders: <b className="text-slate-400">{summary.pendingCount}</b></span>
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

  return (
    <div className="space-y-3">
      <label className="text-xs text-slate-400 flex items-center gap-2">
        Date
        <input type="date" value={d} min={minDate} max={maxDate}
          onChange={(e) => setD(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-slate-100 text-sm" />
      </label>

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

// ---------------- Trade log ----------------

function TradeLog({ runId }) {
  const [track, setTrack] = useState('');
  const [status, setStatus] = useState('');
  const [trades, setTrades] = useState([]);
  const [error, setError] = useState('');

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
        <table className="w-full min-w-[880px]">
          <thead>
            <tr className="text-left text-[11px] text-slate-500 uppercase tracking-wide">
              <th className="py-2 px-3">Symbol</th>
              <th className="py-2 px-3">Rank</th>
              <th className="py-2 px-3">Signal</th>
              <th className="py-2 px-3">Entry</th>
              <th className="py-2 px-3">Fill</th>
              <th className="py-2 px-3">Exit</th>
              <th className="py-2 px-3">Reason</th>
              <th className="py-2 px-3">P&amp;L</th>
              <th className="py-2 px-3">R</th>
              <th className="py-2 px-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} className="border-t border-slate-800">
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
                <td className={`py-1.5 px-3 text-sm font-semibold ${pnlColor(t.realizedPnl)}`}>{fmtInr(t.realizedPnl)}</td>
                <td className="py-1.5 px-3 text-sm text-slate-300">{fmtR(t.rMultiple)}</td>
                <td className={`py-1.5 px-3 text-sm ${TRADE_STATUS_COLOR[t.status]}`}>{t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!trades.length && <div className="text-sm text-slate-500 px-3 py-4">No trades match these filters.</div>}
      </div>
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
  const pollRef = useRef(null);

  const refresh = async () => {
    try {
      const rs = await listBacktestRuns();
      setRuns(rs);
      if (!selectedId && rs.length) setSelectedId(rs[0].id);
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

  const running = runs.find((r) => r.status === 'RUNNING');
  const selected = runs.find((r) => r.id === selectedId);

  return (
    <div className="space-y-4">
      <RunConfigForm
        onCreated={(id) => { setSelectedId(id); refresh(); }}
        blocked={!!running}
        blockedReason={running ? `Run #${running.id} is currently in progress — only one run at a time.` : ''}
      />

      {error && <div className="bg-red-900/40 border border-red-700 text-red-200 text-sm rounded px-3 py-2">{error}</div>}

      <RunList runs={runs} selectedId={selectedId} onSelect={setSelectedId} />

      {selected && (
        <div className="space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="text-sm text-slate-300">
              Run #{selected.id} · {selected.startDate} → {selected.endDate} · {selected.trackMode}
              {selected.status === 'RUNNING' && selected.progressTotalDays
                ? ` · day ${selected.progressDay}/${selected.progressTotalDays}`
                : ''}
              {selected.error && <span className="text-red-300"> · {selected.error}</span>}
            </div>
            <div className="flex gap-1">
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
            <div className="text-sm text-amber-300">Run in progress — results below will update once trades are simulated.</div>
          )}

          {detailTab === 'summary' && <RunSummary runId={selected.id} />}
          {detailTab === 'day' && <DayDrilldown runId={selected.id} minDate={selected.startDate} maxDate={selected.endDate} />}
          {detailTab === 'trades' && <TradeLog runId={selected.id} />}
        </div>
      )}
    </div>
  );
}
