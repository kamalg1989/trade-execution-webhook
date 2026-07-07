import React, { useState, useEffect, useCallback } from 'react';
import { CheckCircle, AlertCircle, Zap, Shield, ShieldOff, Loader, TrendingUp, LogOut,
  Trash2, RefreshCw, AlertTriangle, Check } from 'lucide-react';

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

  useEffect(() => {
    fetchData();
    if (!autoRefresh) return;
    const t = setInterval(fetchData, 20000);
    return () => clearInterval(t);
  }, [autoRefresh, fetchData]);

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
          <button onClick={() => { setRefreshing(true); fetchData().finally(() => setRefreshing(false)); }} disabled={refreshing}
            className="p-2 rounded-lg bg-slate-700 text-slate-200 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button onClick={() => setAutoRefresh(!autoRefresh)}
            className={`p-2 rounded-lg ${autoRefresh ? 'bg-blue-600' : 'bg-slate-700 text-slate-300'}`}>
            <Zap className="w-4 h-4" />
          </button>
        </div>
      </div>

      {message && (
        <div className={`mx-4 mt-3 rounded-lg p-2.5 text-xs flex items-center gap-2 ${message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
          {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
          {message.text}
        </div>
      )}

      {/* UNPROTECTED */}
      {unprotected.length > 0 && (
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
                  <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${p.danger ? zoneColor('DANGER') : zoneColor(p.riskZone)}`}>
                    {p.danger ? 'DANGER' : (p.slBasis || p.riskZone)}
                  </span>
                </div>
                {(p.danger || p.watch) && (
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
                  <button onClick={() => structuralExit(p)} disabled={busy[`exit-${p.id}`]}
                    title="Exit now" className={`${iconBtn} max-w-[46px] bg-amber-700 active:bg-amber-600`}>
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
