import React, { useState, useEffect, useCallback } from 'react';
import { CheckCircle, AlertCircle, Zap, Shield, ShieldOff, Loader, TrendingUp, LogOut, Trash2 } from 'lucide-react';

const api = async (path, body) => {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
};

export default function StopLossTrackerMobile() {
  const [positions, setPositions] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [choice, setChoice] = useState({});
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
    if (!sl || sl <= 0 || sl >= p.current_price) {
      setMessage({ type: 'error', text: `Choose a level below current ₹${p.current_price}` }); return;
    }
    run(`set-${p.id}`, `Place SL for ${p.symbol} @ ₹${sl}? Real Dhan order.`,
      '/api/sl/place-at-level', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl },
      (d) => `✅ ${p.symbol}: SL @ ₹${d.trigger}`);
  };

  const moveChosen = (p) => {
    const sl = Number(choice[p.id]);
    if (!sl || sl <= 0 || sl >= p.current_price) {
      setMessage({ type: 'error', text: `Choose a level below current ₹${p.current_price}` }); return;
    }
    run(`move-${p.id}`, `Move ${p.symbol} SL to ₹${sl}? Places new SL, cancels old.`,
      '/api/sl/move', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl, oldOrderId: p.slOrders?.[0]?.orderId || '' },
      (d) => `🔼 ${p.symbol}: SL → ₹${d.trigger}`);
  };

  const structuralExit = (p) => run(`exit-${p.id}`, `Exit ${p.symbol} x${p.quantity} at next open? Real Dhan order.`,
    '/api/sl/structural-exit', { securityId: p.id, quantity: p.quantity, symbol: p.symbol },
    (d) => `📤 ${p.symbol}: exit @ ~₹${d.trigger}`);

  const cancel = (p, orderId) => run(`cancel-${orderId}`, `Cancel SL ${orderId} for ${p.symbol}?`,
    '/api/sl/cancel', { orderId, symbol: p.symbol }, () => `🗑️ ${p.symbol}: cancelled`);

  const zoneColor = (z) => ({
    SAFE: 'text-green-400 bg-green-900', WARNING: 'text-yellow-400 bg-yellow-900',
    CRITICAL: 'text-red-400 bg-red-900', NO_SL: 'text-orange-400 bg-orange-900',
  }[z] || 'text-slate-300 bg-slate-600');

  if (loading) return <div className="p-4 text-center text-slate-400">Loading...</div>;

  const unprotected = positions.filter(p => p.riskZone === 'NO_SL');
  const protectedPos = positions.filter(p => p.riskZone !== 'NO_SL');

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen pb-8">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <span className="text-xs text-slate-400">{protectedPos.length} protected · {unprotected.length} unprotected</span>
        <button onClick={() => setAutoRefresh(!autoRefresh)}
          className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-semibold ${autoRefresh ? 'bg-blue-600' : 'bg-slate-700 text-slate-300'}`}>
          <Zap className="w-3 h-3" />{autoRefresh ? 'Live' : 'Paused'}
        </button>
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
          <h3 className="text-sm font-bold mb-1 flex items-center gap-2 text-orange-400">
            <ShieldOff className="w-4 h-4" /> Needs Stop Loss ({unprotected.length})
          </h3>
          <p className="text-[10px] text-orange-300/70 mb-3">Tap a suggested level or enter your own.</p>
          <div className="space-y-2">
            {unprotected.map(p => (
              <div key={p.id} className="bg-slate-800 border border-orange-800/50 rounded-lg p-3">
                <div className="flex justify-between items-start mb-2">
                  <div><p className="font-bold text-sm">{p.symbol}</p><p className="text-xs text-slate-400">{p.quantity} · buy ₹{p.buyPrice}</p></div>
                  <div className="text-right">
                    <p className="font-bold text-sm">₹{p.current_price}</p>
                    <p className={`text-xs ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{p.pnl >= 0 ? '+' : ''}₹{Math.abs(p.pnl)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                  </div>
                </div>
                <div className="flex items-stretch gap-2 flex-nowrap min-w-0">
                  <select
                    value={choice[p.id] ?? p.slOptions?.[0]?.price ?? ''}
                    onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                    className="min-w-0 bg-slate-900 border border-slate-600 rounded px-2 py-2 text-xs focus:border-orange-500 focus:outline-none h-9">
                    {(p.slOptions || []).length === 0 && <option value="">No valid levels</option>}
                    {(p.slOptions || []).map(o => (
                      <option key={o.basis} value={o.price}>
                        ₹{o.price} · {o.label} ({o.pctFromEntry > 0 ? '+' : ''}{o.pctFromEntry}%)
                      </option>
                    ))}
                  </select>
                  <button onClick={() => placeChosen(p)} disabled={busy[`set-${p.id}`] || !(p.slOptions || []).length}
                    className="bg-orange-600 active:bg-orange-700 disabled:opacity-50 font-bold px-3 rounded text-sm flex items-center justify-center gap-1 flex-shrink-0 h-9 whitespace-nowrap">
                    {busy[`set-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Shield className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* PROTECTED */}
      <div className="px-4 py-4 border-t border-slate-700">
        <h3 className="text-sm font-bold mb-3 flex items-center gap-2 text-green-400">
          <Shield className="w-4 h-4" /> Protected ({protectedPos.length})
        </h3>
        {protectedPos.length === 0 ? (
          <p className="text-xs text-slate-400 text-center py-4">No active forever SL orders</p>
        ) : (
          <div className="space-y-2">
            {protectedPos.map(p => {
              const trailOpts = (p.slOptions || []).filter(o => o.price > p.stop_loss);
              return (
              <div key={p.id} className="bg-slate-700 rounded-lg p-3">
                <div className="flex justify-between items-start mb-2">
                  <div><p className="font-bold text-sm">{p.symbol}</p><p className="text-xs text-slate-400">{p.quantity} · buy ₹{p.buyPrice}</p></div>
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${zoneColor(p.riskZone)}`}>{p.riskZone}</span>
                </div>
                <div className="bg-slate-600/60 rounded p-2 space-y-1 text-xs mb-2">
                  <div className="flex justify-between"><span className="text-slate-400">Current:</span><span className="text-blue-400 font-bold">₹{p.current_price}</span></div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">SL:</span>
                    <span className="text-red-400 font-bold">₹{p.stop_loss}{p.slPctFromEntry != null && <span className="text-slate-400 font-normal"> ({p.slPctFromEntry > 0 ? '+' : ''}{p.slPctFromEntry}% vs buy)</span>}</span>
                  </div>
                  <div className="flex justify-between"><span className="text-slate-400">Distance:</span><span>{p.distanceToSL}%</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">P&L:</span><span className={p.pnl >= 0 ? 'text-green-400 font-bold' : 'text-red-400 font-bold'}>{p.pnl >= 0 ? '+' : ''}₹{Math.abs(p.pnl)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</span></div>
                </div>
                {/* Trail-to-level dropdown */}
                <div className="flex items-stretch gap-2 mb-2 flex-nowrap min-w-0">
                  <select
                    value={choice[p.id] ?? ''}
                    onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                    disabled={trailOpts.length === 0}
                    className="min-w-0 bg-slate-900 border border-slate-600 rounded px-2 py-2 text-xs focus:border-green-500 focus:outline-none disabled:opacity-40 h-9">
                    <option value="">{trailOpts.length ? 'Trail…' : 'SL highest'}</option>
                    {trailOpts.map(o => (
                      <option key={o.basis} value={o.price}>₹{o.price} · {o.label} ({o.pctFromEntry > 0 ? '+' : ''}{o.pctFromEntry}%)</option>
                    ))}
                  </select>
                  <button onClick={() => moveChosen(p)} disabled={busy[`move-${p.id}`] || !choice[p.id]}
                    className="bg-green-700 active:bg-green-600 disabled:opacity-50 text-xs font-semibold px-3 rounded flex items-center justify-center gap-1 flex-shrink-0 h-9 whitespace-nowrap">
                    {busy[`move-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <TrendingUp className="w-3.5 h-3.5" />}
                  </button>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => structuralExit(p)} disabled={busy[`exit-${p.id}`]}
                    className="flex-1 bg-amber-700 active:bg-amber-600 disabled:opacity-50 text-xs font-semibold px-2 py-2 rounded flex items-center justify-center gap-1">
                    {busy[`exit-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />} Exit Now
                  </button>
                  {p.slOrders?.slice(0, 1).map(o => (
                    <button key={o.orderId} onClick={() => cancel(p, o.orderId)} disabled={busy[`cancel-${o.orderId}`]}
                      className="flex-1 bg-red-800 active:bg-red-700 disabled:opacity-50 text-xs font-semibold px-2 py-2 rounded flex items-center justify-center gap-1">
                      {busy[`cancel-${o.orderId}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />} Cancel
                    </button>
                  ))}
                </div>
              </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ALERTS */}
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
