import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { CheckCircle, AlertCircle, Zap, Shield, ShieldOff, Loader, TrendingUp, LogOut,
  Trash2, RefreshCw, AlertTriangle, Check, LayoutGrid, Table2, ChevronUp, ChevronDown } from 'lucide-react';

const api = async (path, body) => {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
};

const rowTint = (p) => {
  if (p.danger) return 'border-l-4 border-red-500 bg-red-950/50';
  const r = p.rMultiple;
  if (r == null) return 'border-l-4 border-slate-600 bg-slate-700';
  if (r < 0) return 'border-l-4 border-red-500 bg-red-950/40';
  if (r < 1) return 'border-l-4 border-green-700 bg-green-950/40';
  if (r < 2) return 'border-l-4 border-green-500 bg-green-800/40';
  return 'border-l-4 border-emerald-300 bg-emerald-700/40';
};
const rBadge = (r) => {
  if (r == null) return null;
  const cls = r < 0 ? 'text-red-400' : r < 1 ? 'text-green-400' : r < 2 ? 'text-green-300' : 'text-emerald-200';
  return <span className={`text-[11px] font-bold ${cls}`}>{r >= 0 ? '+' : ''}{r}R</span>;
};

const TABLE_COLUMNS = [
  { key: 'symbol', label: 'Symbol', align: 'left', sticky: true },
  { key: 'quantity', label: 'Qty', align: 'right' },
  { key: 'buyPrice', label: 'Entry', align: 'right' },
  { key: 'current_price', label: 'LTP', align: 'right' },
  { key: 'structuralSL', label: 'Struct SL', align: 'right' },
  { key: 'safetySL', label: 'Safety SL', align: 'right' },
  { key: 'stop_loss', label: 'Current SL', align: 'right' },
  { key: 'rMultiple', label: 'R', align: 'right' },
  { key: 'pnl', label: 'PnL', align: 'right' },
  { key: 'pnlPct', label: 'PnL %', align: 'right' },
  { key: 'status', label: 'Status', align: 'left' },
];

const statusOf = (p) => (p.pendingExit ? 'EXIT_PENDING' : p.danger ? 'DANGER' : p.riskZone);
const statusClass = (s) => ({
  DANGER: 'text-red-300', CRITICAL: 'text-red-400', WARNING: 'text-yellow-300',
  SAFE: 'text-green-300', NO_SL: 'text-orange-400', EXIT_PENDING: 'text-blue-300',
}[s] || 'text-slate-300');

const fmtNum = (v, decimals = 2) => (v == null ? '—' : Number(v).toFixed(decimals));
const fmtSigned = (v, decimals = 1, suffix = '') =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(decimals)}${suffix}`;

function tableCell(p, key) {
  switch (key) {
    case 'symbol': return p.symbol;
    case 'quantity': return p.quantity ?? '—';
    case 'buyPrice': return fmtNum(p.buyPrice);
    case 'current_price': return fmtNum(p.current_price);
    case 'structuralSL': return fmtNum(p.structuralSL);
    case 'safetySL': return fmtNum(p.safetySL);
    case 'stop_loss': return p.stop_loss ? fmtNum(p.stop_loss) : '—';
    case 'rMultiple': return fmtSigned(p.rMultiple, 1, 'R');
    case 'pnl': return p.pnl == null ? '—' : `${p.pnl >= 0 ? '+' : ''}₹${Math.abs(p.pnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
    case 'pnlPct': return fmtSigned(p.pnlPct, 1, '%');
    case 'status': return statusOf(p);
    default: return '—';
  }
}

function tableCellClass(p, key) {
  if (key === 'safetySL') return 'text-red-300';
  if (key === 'rMultiple') return p.rMultiple == null ? 'text-slate-400' : p.rMultiple >= 0 ? 'text-green-400' : 'text-red-400';
  if (key === 'pnl' || key === 'pnlPct') {
    const v = key === 'pnl' ? p.pnl : p.pnlPct;
    return v == null ? 'text-slate-400' : v >= 0 ? 'text-green-400' : 'text-red-400';
  }
  if (key === 'status') return statusClass(statusOf(p));
  if (key === 'current_price') return 'text-blue-400 font-semibold';
  return 'text-slate-100';
}

function PositionsTable({ positions, onRefresh, refreshing }) {
  const [sortKey, setSortKey] = useState('rMultiple');
  const [sortDir, setSortDir] = useState(-1);

  const rows = useMemo(() => positions.map(p => ({
    ...p,
    pnlPct: p.buyPrice ? Math.round(((p.current_price - p.buyPrice) / p.buyPrice) * 1000) / 10 : null,
  })), [positions]);

  const sorted = useMemo(() => {
    const list = [...rows];
    list.sort((a, b) => {
      let av = sortKey === 'status' ? statusOf(a) : a[sortKey];
      let bv = sortKey === 'status' ? statusOf(b) : b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av.localeCompare(bv) * sortDir;
      return (av - bv) * sortDir;
    });
    return list;
  }, [rows, sortKey, sortDir]);

  const onHeaderClick = (key) => {
    if (sortKey === key) setSortDir(d => -d);
    else { setSortKey(key); setSortDir(1); }
  };

  return (
    <div className="rounded-lg border border-slate-700 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-800/60 border-b border-slate-700">
        <span className="text-[11px] text-slate-400">Tap a column to sort</span>
        <button onClick={onRefresh} disabled={refreshing} className="p-1.5 rounded bg-slate-700 text-slate-200 disabled:opacity-50">
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="border-collapse text-[11px] whitespace-nowrap w-full">
          <thead>
            <tr className="bg-slate-800 border-b border-slate-600">
              {TABLE_COLUMNS.map(c => (
                <th key={c.key} onClick={() => onHeaderClick(c.key)}
                  className={`px-2.5 py-2 font-medium text-slate-400 cursor-pointer select-none ${c.align === 'right' ? 'text-right' : 'text-left'} ${c.sticky ? 'sticky left-0 bg-slate-800 z-10' : ''}`}>
                  <span className="inline-flex items-center gap-0.5">
                    {c.label}
                    {sortKey === c.key && (sortDir === 1 ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(p => (
              <tr key={p.id} className={`border-b border-slate-700/60 ${p.danger ? 'bg-red-950/30' : ''}`}>
                {TABLE_COLUMNS.map(c => (
                  <td key={c.key}
                    className={`px-2.5 py-2 ${c.align === 'right' ? 'text-right' : 'text-left'} ${c.sticky ? 'sticky left-0 bg-slate-900 font-semibold text-white' : tableCellClass(p, c.key)}`}>
                    {tableCell(p, c.key)}
                  </td>
                ))}
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr><td colSpan={TABLE_COLUMNS.length} className="px-3 py-4 text-center text-slate-400">No positions</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function StopLossTrackerMobile() {
  const [positions, setPositions] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [choice, setChoice] = useState({});
  const [structIn, setStructIn] = useState({});
  const [busy, setBusy] = useState({});
  const [message, setMessage] = useState(null);
  const [viewMode, setViewMode] = useState('cards');
  const [summary, setSummary] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await fetch('/api/sl-alerts');
      if (!r.ok) throw new Error('failed');
      const data = await r.json();
      setPositions(data.positions || []);
      setAlerts(data.alerts || []);
    } catch (e) { console.error('SL fetch failed:', e); }
    setLoading(false);
  }, []);

  const fetchSummary = useCallback(async () => {
    try {
      const r = await fetch('/api/portfolio');
      if (!r.ok) throw new Error('failed');
      setSummary(await r.json());
    } catch (e) { console.error('Portfolio summary fetch failed:', e); }
  }, []);

  useEffect(() => {
    fetchData();
    fetchSummary();
    if (!autoRefresh) return;
    const t = setInterval(() => { fetchData(); fetchSummary(); }, 20000);
    return () => clearInterval(t);
  }, [autoRefresh, fetchData, fetchSummary]);

  const run = async (key, confirmMsg, path, body, okMsg) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(b => ({ ...b, [key]: true }));
    setMessage(null);
    const { ok, data } = await api(path, body);
    setMessage(ok ? { type: 'ok', text: okMsg(data) } : { type: 'error', text: data.detail || 'Failed' });
    if (ok) fetchData();
    setBusy(b => ({ ...b, [key]: false }));
  };

  const placeChosen = (p) => {
    const sl = Number(choice[p.id] ?? p.slOptions?.[0]?.price);
    if (!sl || sl <= 0 || sl >= p.current_price) return setMessage({ type: 'error', text: `Choose a level below ₹${p.current_price}` });
    run(`set-${p.id}`, `Place SL for ${p.symbol} @ ₹${sl}? Real Dhan order.`,
      '/api/sl/place-at-level', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl },
      (d) => `✅ ${p.symbol}: SL @ ₹${d.trigger}`);
  };

  const moveChosen = (p) => {
    const sl = Number(choice[p.id]);
    if (!sl || sl <= 0 || sl >= p.current_price) return setMessage({ type: 'error', text: `Choose a level below ₹${p.current_price}` });
    run(`move-${p.id}`, `Move ${p.symbol} SL to ₹${sl}? Places new SL, cancels old.`,
      '/api/sl/move', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl, oldOrderId: p.slOrders?.[0]?.orderId || '' },
      (d) => `🔼 ${p.symbol}: SL → ₹${d.trigger}`);
  };

  const structuralExit = (p) => run(`exit-${p.id}`, `Exit ${p.symbol} at next open? Real Dhan order.`,
    '/api/sl/structural-exit', { securityId: p.id, quantity: p.quantity, symbol: p.symbol },
    (d) => `📤 ${p.symbol}: exit @ ~₹${d.trigger}`);

  const cancel = (p, orderId) => run(`cancel-${orderId}`, `Cancel SL for ${p.symbol}?`,
    '/api/sl/cancel', { orderId, symbol: p.symbol }, () => `🗑️ ${p.symbol}: cancelled`);

  const saveStructural = (p) => {
    const v = Number(structIn[p.id]);
    if (!v || v <= 0) return setMessage({ type: 'error', text: 'Enter a valid structural SL' });
    run(`struct-${p.id}`, null, '/api/sl/set-structural',
      { symbol: p.symbol, structuralSL: v }, () => `✅ ${p.symbol}: structural SL ₹${v}`);
  };

  const zoneColor = (z) => ({
    SAFE: 'text-green-300 bg-green-900', WARNING: 'text-yellow-300 bg-yellow-900',
    CRITICAL: 'text-red-300 bg-red-900', DANGER: 'text-white bg-red-600',
  }[z] || 'text-slate-200 bg-slate-600');

  const StructVal = ({ p }) => (
    p.structuralSL && !p.structuralEditable ? (
      <span className="text-purple-300 font-bold">₹{p.structuralSL}</span>
    ) : (
      <span className="inline-flex items-center gap-1">
        <input type="number" step="0.05" placeholder={p.structuralSL ? String(p.structuralSL) : 'set'}
          value={structIn[p.id] ?? (p.structuralSL || '')}
          onChange={e => setStructIn(s => ({ ...s, [p.id]: e.target.value }))}
          className="bg-slate-900 border border-slate-600 rounded px-1.5 py-0.5 w-16 text-xs focus:border-purple-500 focus:outline-none" />
        <button onClick={() => saveStructural(p)} disabled={busy[`struct-${p.id}`]} className="text-purple-300">
          {busy[`struct-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        </button>
      </span>
    )
  );

  if (loading) return <div className="p-4 text-center text-slate-400">Loading…</div>;

  const unprotected = positions.filter(p => p.riskZone === 'NO_SL');
  const protectedPos = positions.filter(p => p.riskZone !== 'NO_SL');
  const iconBtn = 'flex-1 py-2 rounded flex items-center justify-center disabled:opacity-40';

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen pb-8">
      {/* Toolbar */}
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <span className="text-xs text-slate-400">{protectedPos.length} protected · {unprotected.length} unprotected</span>
        <div className="flex items-center gap-2">
          <div className="flex bg-slate-900 border border-slate-600 rounded-lg p-0.5">
            <button onClick={() => setViewMode('cards')} title="Card view"
              className={`p-1.5 rounded-md ${viewMode === 'cards' ? 'bg-blue-600 text-white' : 'text-slate-400'}`}>
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button onClick={() => setViewMode('table')} title="Table view"
              className={`p-1.5 rounded-md ${viewMode === 'table' ? 'bg-blue-600 text-white' : 'text-slate-400'}`}>
              <Table2 className="w-4 h-4" />
            </button>
          </div>
          <button onClick={() => { setRefreshing(true); Promise.all([fetchData(), fetchSummary()]).finally(() => setRefreshing(false)); }} disabled={refreshing}
            title="Refresh" className="p-2 rounded-lg bg-slate-700 text-slate-200 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button onClick={() => setAutoRefresh(!autoRefresh)}
            className={`p-2 rounded-lg ${autoRefresh ? 'bg-blue-600' : 'bg-slate-700 text-slate-300'}`}>
            <Zap className="w-4 h-4" />
          </button>
        </div>
      </div>

      {summary && (
        <div className="px-4 pt-3 grid grid-cols-3 gap-2">
          <div className="bg-slate-800/60 rounded-lg p-2">
            <p className="text-slate-400 text-[10px] mb-0.5">Invested</p>
            <p className="text-xs font-bold truncate">₹{summary.totalInvested?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-2">
            <p className="text-slate-400 text-[10px] mb-0.5">Value</p>
            <p className="text-xs font-bold text-blue-400 truncate">₹{summary.totalValue?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</p>
          </div>
          <div className={`rounded-lg p-2 ${summary.unrealizedPnL >= 0 ? 'bg-green-900/30' : 'bg-red-900/30'}`}>
            <p className="text-slate-400 text-[10px] mb-0.5">Unrealized</p>
            <p className={`text-xs font-bold truncate ${summary.unrealizedPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {summary.unrealizedPnL >= 0 ? '+' : ''}₹{summary.unrealizedPnL?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </p>
          </div>
        </div>
      )}

      {message && (
        <div className={`mx-4 mt-3 rounded-lg p-2.5 text-xs flex items-center gap-2 ${message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
          {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
          {message.text}
        </div>
      )}

      {/* TABLE VIEW */}
      {viewMode === 'table' && (
        <div className="px-4 py-4">
          <PositionsTable positions={positions}
            onRefresh={() => { setRefreshing(true); fetchData().finally(() => setRefreshing(false)); }}
            refreshing={refreshing} />
        </div>
      )}

      {/* UNPROTECTED */}
      {viewMode === 'cards' && unprotected.length > 0 && (
        <div className="px-4 py-4">
          <h3 className="text-sm font-bold mb-2 flex items-center gap-2 text-orange-400">
            <ShieldOff className="w-4 h-4" /> Needs Stop Loss ({unprotected.length})
          </h3>
          <div className="space-y-2">
            {unprotected.map(p => (
              <div key={p.id} className={`rounded-lg p-3 ${rowTint(p)}`}>
                <div className="flex justify-between items-start mb-1.5">
                  <div className="flex items-center gap-2">
                    <p className="font-bold text-sm">{p.symbol}</p>{rBadge(p.rMultiple)}
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-sm">₹{p.current_price}</p>
                    <p className={`text-xs ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{p.pnl >= 0 ? '+' : ''}₹{Math.abs(p.pnl)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-1 text-[10px] mb-2">
                  <div><span className="text-slate-400">Buy</span><br/>₹{p.buyPrice}</div>
                  <div><span className="text-slate-400">Safety</span><br/>₹{p.safetySL ?? '—'}</div>
                  <div><span className="text-slate-400">Struct</span><br/><StructVal p={p} /></div>
                </div>
                <div className="flex items-stretch gap-2">
                  <select value={choice[p.id] ?? p.slOptions?.[0]?.price ?? ''}
                    onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                    className="min-w-0 flex-1 bg-slate-900 border border-slate-600 rounded px-2 text-xs h-9 focus:border-orange-500 focus:outline-none">
                    {(p.slOptions || []).length === 0 && <option value="">No valid levels</option>}
                    {(p.slOptions || []).map(o => (
                      <option key={o.basis} value={o.price}>₹{o.price} · {o.label}</option>
                    ))}
                  </select>
                  <button onClick={() => placeChosen(p)} disabled={busy[`set-${p.id}`] || !(p.slOptions || []).length}
                    className="bg-orange-600 active:bg-orange-700 disabled:opacity-50 font-semibold px-4 rounded text-sm flex items-center gap-1 h-9 flex-shrink-0">
                    {busy[`set-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />} Set
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* PROTECTED */}
      {viewMode === 'cards' && (
      <div className="px-4 py-4 border-t border-slate-700">
        <h3 className="text-sm font-bold mb-2 flex items-center gap-2 text-green-400">
          <Shield className="w-4 h-4" /> Protected ({protectedPos.length})
        </h3>
        {protectedPos.length === 0 ? (
          <p className="text-xs text-slate-400 text-center py-4">No active SL orders</p>
        ) : (
          <div className="space-y-2">
            {protectedPos.map(p => {
              const trailOpts = (p.slOptions || []).filter(o => o.price > p.stop_loss);
              return (
              <div key={p.id} className={`rounded-lg p-3 ${rowTint(p)}`}>
                <div className="flex justify-between items-start mb-1.5">
                  <div className="flex items-center gap-2"><p className="font-bold text-sm">{p.symbol}</p>{rBadge(p.rMultiple)}</div>
                  <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${p.pendingExit ? 'bg-blue-900/60 text-blue-300' : p.danger ? zoneColor('DANGER') : zoneColor(p.riskZone)}`}>
                    {p.pendingExit ? 'EXIT PENDING' : p.danger ? 'DANGER' : (p.slBasis || p.riskZone)}
                  </span>
                </div>
                {p.pendingExit ? (
                  <div className="mb-2 text-[11px] font-semibold flex items-start gap-1 text-blue-300">
                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
                    ⏳ Exit order already placed — resting at broker, fills at next open
                  </div>
                ) : (p.danger || p.watch) && (
                  <div className={`mb-2 text-[11px] font-semibold flex items-start gap-1 ${p.danger ? 'text-red-300' : 'text-amber-300'}`}>
                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
                    {p.danger ? `Closed ₹${p.lastClose} below structural ₹${p.structuralSL} — EXIT next open`
                              : `Live below structural ₹${p.structuralSL} — watch for close`}
                  </div>
                )}
                <div className="bg-slate-600/50 rounded p-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs mb-2">
                  <div className="flex justify-between"><span className="text-slate-400">Buy</span><span>₹{p.buyPrice}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Current</span><span className="text-blue-400 font-bold">₹{p.current_price}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Safety</span><span>₹{p.safetySL ?? '—'}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Struct</span><StructVal p={p} /></div>
                  <div className="flex justify-between col-span-2 pt-1 border-t border-slate-500/40">
                    <span className="text-slate-400">Current SL</span>
                    <span className="text-red-400 font-bold">₹{p.stop_loss}{p.slPctFromEntry != null && <span className="text-slate-400 font-normal"> ({p.slPctFromEntry > 0 ? '+' : ''}{p.slPctFromEntry}%)</span>}</span>
                  </div>
                </div>
                <div className="flex items-stretch gap-2">
                  <select value={choice[p.id] ?? ''}
                    onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                    disabled={trailOpts.length === 0}
                    className="min-w-0 flex-1 bg-slate-900 border border-slate-600 rounded px-2 text-xs h-9 focus:border-green-500 focus:outline-none disabled:opacity-40">
                    <option value="">{trailOpts.length ? 'Trail…' : 'SL highest'}</option>
                    {trailOpts.map(o => (<option key={o.basis} value={o.price}>₹{o.price} · {o.label}</option>))}
                  </select>
                  <button onClick={() => moveChosen(p)} disabled={busy[`move-${p.id}`] || !choice[p.id]}
                    title="Move SL up" className={`${iconBtn} max-w-[46px] bg-green-700 active:bg-green-600`}>
                    {busy[`move-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <TrendingUp className="w-4 h-4" />}
                  </button>
                  <button onClick={() => structuralExit(p)} disabled={busy[`exit-${p.id}`] || p.pendingExit}
                    title={p.pendingExit ? "Exit already pending" : "Exit now"} className={`${iconBtn} max-w-[46px] ${p.pendingExit ? 'bg-slate-600' : 'bg-amber-700 active:bg-amber-600'}`}>
                    {busy[`exit-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <LogOut className="w-4 h-4" />}
                  </button>
                  {p.slOrders?.slice(0, 1).map(o => (
                    <button key={o.orderId} onClick={() => cancel(p, o.orderId)} disabled={busy[`cancel-${o.orderId}`]}
                      title="Cancel SL" className={`${iconBtn} max-w-[46px] bg-red-800 active:bg-red-700`}>
                      {busy[`cancel-${o.orderId}`] ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    </button>
                  ))}
                </div>
              </div>
              );
            })}
          </div>
        )}
      </div>
      )}

      {/* Alerts */}
      {alerts.length > 0 && (
        <div className="px-4 py-4 border-t border-slate-700">
          <h3 className="text-sm font-bold mb-2 text-yellow-400">⚠️ Alerts ({alerts.length})</h3>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {alerts.map((a, i) => <p key={i} className="text-xs text-slate-300">{a.message}</p>)}
          </div>
        </div>
      )}
    </div>
  );
}
