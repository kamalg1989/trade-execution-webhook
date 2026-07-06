import React, { useState, useEffect, useCallback } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle, ShieldOff, Shield, Zap, Loader, Trash2, TrendingUp, LogOut } from 'lucide-react';

const api = async (path, body) => {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
};

export default function StopLossTracker() {
  const [positions, setPositions] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [choice, setChoice] = useState({});   // per-position selected SL price
  const [busy, setBusy] = useState({});
  const [message, setMessage] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await fetch('/api/sl-alerts');
      if (!r.ok) throw new Error('fetch failed');
      const data = await r.json();
      setPositions(data.positions || []);
      setAlerts(data.alerts || []);
    } catch (e) {
      console.error('SL fetch failed:', e);
    }
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
    setMessage(ok
      ? { type: 'ok', text: okMsg(data) }
      : { type: 'error', text: data.detail || 'Action failed' });
    if (ok) fetchData();
    setBusy(b => ({ ...b, [key]: false }));
  };

  // Place SL at the level chosen from the dropdown (unprotected → new order)
  const placeChosen = (p) => {
    const sl = Number(choice[p.id] ?? p.slOptions?.[0]?.price);
    if (!sl || sl <= 0 || sl >= p.current_price) {
      setMessage({ type: 'error', text: `${p.symbol}: choose a level below current ₹${p.current_price}` });
      return;
    }
    run(
      `set-${p.id}`,
      `Place SL for ${p.symbol} x${p.quantity} @ ₹${sl}?\n\nReal Dhan forever order.`,
      '/api/sl/place-at-level',
      { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl },
      (d) => `✅ ${p.symbol}: SL @ ₹${d.trigger}`,
    );
  };

  // Move a protected position's SL to the chosen level (place-first, cancel-old)
  const moveChosen = (p) => {
    const sl = Number(choice[p.id]);
    if (!sl || sl <= 0 || sl >= p.current_price) {
      setMessage({ type: 'error', text: `${p.symbol}: choose a level below current ₹${p.current_price}` });
      return;
    }
    run(
      `move-${p.id}`,
      `Move ${p.symbol} SL to ₹${sl}?\n\nPlaces new forever SL, then cancels the old one.`,
      '/api/sl/move',
      { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl, oldOrderId: p.slOrders?.[0]?.orderId || '' },
      (d) => `🔼 ${p.symbol}: SL moved to ₹${d.trigger}`,
    );
  };

  const structuralExit = (p) => run(
    `exit-${p.id}`,
    `Place STRUCTURAL EXIT for ${p.symbol} x${p.quantity}?\n\nExit-forever sells at next open. Real Dhan order.`,
    '/api/sl/structural-exit',
    { securityId: p.id, quantity: p.quantity, symbol: p.symbol },
    (d) => `📤 ${p.symbol}: exit order @ ~₹${d.trigger}`,
  );

  const cancel = (p, orderId) => run(
    `cancel-${orderId}`,
    `Cancel SL order ${orderId} for ${p.symbol}?`,
    '/api/sl/cancel',
    { orderId, symbol: p.symbol },
    () => `🗑️ ${p.symbol}: SL cancelled`,
  );

  const zoneStyle = (z) => ({
    SAFE: 'bg-green-900 text-green-400', WARNING: 'bg-yellow-900 text-yellow-400',
    CRITICAL: 'bg-red-900 text-red-400', NO_SL: 'bg-orange-900 text-orange-400',
  }[z] || 'bg-slate-600 text-slate-300');

  if (loading) return <div className="p-8 text-slate-400">Loading stop loss data...</div>;

  const unprotected = positions.filter(p => p.riskZone === 'NO_SL');
  const protectedPos = positions.filter(p => p.riskZone !== 'NO_SL');

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
          <div>
            <h1 className="text-2xl lg:text-4xl font-bold mb-1">🛡️ Stop Loss Tracker</h1>
            <p className="text-xs lg:text-base text-slate-400">
              Forever orders · {protectedPos.length} protected · {unprotected.length} unprotected · manual buttons only
            </p>
          </div>
          <button onClick={() => setAutoRefresh(!autoRefresh)}
            className={`self-start flex items-center gap-2 px-3 py-1.5 rounded text-sm font-semibold ${autoRefresh ? 'bg-blue-600' : 'bg-slate-700 text-slate-300'}`}>
            <Zap className="w-4 h-4" />{autoRefresh ? 'Live (20s)' : 'Paused'}
          </button>
        </div>

        {message && (
          <div className={`rounded-lg p-3 text-sm flex items-center gap-2 ${message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
            {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
            {message.text}
          </div>
        )}

        {/* UNPROTECTED */}
        {unprotected.length > 0 && (
          <div className="bg-orange-950/60 border border-orange-700 rounded-lg p-4 lg:p-6">
            <h2 className="text-base lg:text-xl font-bold mb-1 flex items-center gap-2 text-orange-400">
              <ShieldOff className="w-5 lg:w-6 h-5 lg:h-6" /> Unprotected Positions ({unprotected.length})
            </h2>
            <p className="text-xs text-orange-300/80 mb-4">No active forever SL. Use a suggested level or enter your own.</p>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {unprotected.map(p => (
                <div key={p.id} className="bg-slate-800 rounded-lg p-4 border border-orange-800/50">
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <p className="font-bold text-base">{p.symbol}</p>
                      <p className="text-xs text-slate-400">{p.quantity} qty · buy ₹{p.buyPrice}</p>
                    </div>
                    <div className="text-right">
                      <p className="font-bold text-sm">₹{p.current_price}</p>
                      <p className={`text-xs ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {p.pnl >= 0 ? '+' : ''}₹{p.pnl?.toLocaleString('en-IN', {maximumFractionDigits: 0})}
                      </p>
                    </div>
                  </div>

                  {/* Suggested-level dropdown */}
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
                      className="bg-orange-600 hover:bg-orange-700 disabled:opacity-50 font-bold px-3 rounded text-sm flex items-center justify-center gap-1 flex-shrink-0 h-9 whitespace-nowrap">
                      {busy[`set-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
                      Set
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* PROTECTED */}
        <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
          <h2 className="text-base lg:text-xl font-bold mb-4 flex items-center gap-2">
            <Shield className="w-5 lg:w-6 h-5 lg:h-6 text-green-400" /> Protected Positions ({protectedPos.length})
          </h2>
          {protectedPos.length === 0 ? (
            <p className="text-slate-400 text-sm text-center py-6">No positions with active forever SL orders</p>
          ) : (
            <div className="space-y-3">
              {protectedPos.map(p => {
                // Trail dropdown: only levels ABOVE the current SL (ratchet up)
                const trailOpts = (p.slOptions || []).filter(o => o.price > p.stop_loss);
                return (
                <div key={p.id} className="bg-slate-800 rounded-lg p-4">
                  <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
                    <div className="grid grid-cols-3 lg:grid-cols-7 gap-3 flex-1 text-sm">
                      <div><p className="text-[10px] text-slate-400">Symbol</p><p className="font-bold">{p.symbol}</p></div>
                      <div><p className="text-[10px] text-slate-400">Qty</p><p>{p.quantity}</p></div>
                      <div><p className="text-[10px] text-slate-400">Buy</p><p>₹{p.buyPrice}</p></div>
                      <div><p className="text-[10px] text-slate-400">Current</p><p className="text-blue-400">₹{p.current_price}</p></div>
                      <div>
                        <p className="text-[10px] text-slate-400">SL</p>
                        <p className="text-red-400">₹{p.stop_loss}
                          {p.slPctFromEntry != null && (
                            <span className="text-slate-400"> ({p.slPctFromEntry > 0 ? '+' : ''}{p.slPctFromEntry}%)</span>
                          )}
                        </p>
                      </div>
                      <div><p className="text-[10px] text-slate-400">Dist</p><p>{p.distanceToSL}%</p></div>
                      <div>
                        <p className="text-[10px] text-slate-400">Zone</p>
                        <span className={`px-2 py-0.5 rounded text-xs font-bold ${zoneStyle(p.riskZone)}`}>{p.riskZone}</span>
                      </div>
                    </div>

                    <div className="flex items-stretch gap-2 flex-nowrap min-w-0">
                      {/* Trail-to-level dropdown */}
                      <select
                        value={choice[p.id] ?? ''}
                        onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                        disabled={trailOpts.length === 0}
                        className="min-w-0 bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-xs focus:border-green-500 focus:outline-none disabled:opacity-40 h-9">
                        <option value="">{trailOpts.length ? 'Trail…' : 'SL highest'}</option>
                        {trailOpts.map(o => (
                          <option key={o.basis} value={o.price}>
                            ₹{o.price} · {o.label} ({o.pctFromEntry > 0 ? '+' : ''}{o.pctFromEntry}%)
                          </option>
                        ))}
                      </select>
                      <button onClick={() => moveChosen(p)} disabled={busy[`move-${p.id}`] || !choice[p.id]}
                        title="Place new SL at chosen level, then cancel old"
                        className="bg-green-700 hover:bg-green-600 disabled:opacity-50 text-xs font-semibold px-3 rounded flex items-center justify-center gap-1 h-9 flex-shrink-0 whitespace-nowrap">
                        {busy[`move-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <TrendingUp className="w-3.5 h-3.5" />}
                        Move
                      </button>
                      <button onClick={() => structuralExit(p)} disabled={busy[`exit-${p.id}`]}
                        title="Place exit-forever (sells at next open)"
                        className="bg-amber-700 hover:bg-amber-600 disabled:opacity-50 text-xs font-semibold px-3 py-1.5 rounded flex items-center gap-1">
                        {busy[`exit-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <LogOut className="w-3.5 h-3.5" />}
                        Exit Now
                      </button>
                      {p.slOrders?.map(o => (
                        <button key={o.orderId} onClick={() => cancel(p, o.orderId)} disabled={busy[`cancel-${o.orderId}`]}
                          title={`Cancel SL order ${o.orderId}`}
                          className="bg-red-800 hover:bg-red-700 disabled:opacity-50 text-xs font-semibold px-3 py-1.5 rounded flex items-center gap-1">
                          {busy[`cancel-${o.orderId}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                          Cancel
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                );
              })}
            </div>
          )}
        </div>

        {/* ALERTS */}
        {alerts.length > 0 && (
          <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
            <h2 className="text-base lg:text-xl font-bold mb-3 flex items-center gap-2 text-yellow-400">
              <AlertTriangle className="w-5 h-5" /> Alerts ({alerts.length})
            </h2>
            <div className="space-y-1.5 max-h-56 overflow-y-auto">
              {alerts.map((a, i) => (
                <p key={i} className={`text-sm ${a.type === 'CRITICAL' ? 'text-red-300' : a.type === 'WARNING' ? 'text-yellow-300' : 'text-orange-300'}`}>{a.message}</p>
              ))}
            </div>
          </div>
        )}

        <div className="bg-slate-800/60 rounded-lg p-4 text-xs text-slate-400">
          <strong className="text-slate-300">How it works:</strong>{' '}
          SLs are Dhan <strong>forever orders</strong> (persist across days). Existing SLs are detected from your forever SELL orders.
          <span className="text-blue-400"> Safety −8%</span> = backstop below avg cost ·
          <span className="text-purple-400"> Structural</span> = level from your screener sheet ·
          <span className="text-green-400"> Trail Up</span> = ratchet −8% higher after 1R ·
          <span className="text-amber-400"> Exit Now</span> = exit-forever that sells at next open.
          Nothing updates automatically — every change is a button you press.
        </div>
      </div>
    </div>
  );
}
