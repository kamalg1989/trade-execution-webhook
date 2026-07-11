import React, { useState, useEffect, useCallback } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle, ShieldOff, Shield, Zap, Loader,
  Trash2, TrendingUp, LogOut, RefreshCw, Check, Pencil } from 'lucide-react';

const api = async (path, body) => {
  const apiKey = localStorage.getItem('trading_api_key');
  if (!apiKey) {
    alert('❌ API key not found. Please load it from Settings first.');
    return { ok: false, data: {} };
  }
  const r = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey
    },
    body: JSON.stringify(body)
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
};

// Row tint by R-multiple of the current move (danger overrides).
const rowTint = (p) => {
  if (p.danger) return 'border-l-4 border-red-500 bg-red-950/50';
  const r = p.rMultiple;
  if (r == null) return 'border-l-4 border-slate-600 bg-slate-800';
  if (r < 0) return 'border-l-4 border-red-500 bg-red-950/30';
  if (r < 1) return 'border-l-4 border-green-700 bg-green-950/30';
  if (r < 2) return 'border-l-4 border-green-500 bg-green-800/30';
  return 'border-l-4 border-emerald-300 bg-emerald-700/30';
};
const rBadge = (r) => {
  if (r == null) return null;
  const cls = r < 0 ? 'text-red-400' : r < 1 ? 'text-green-400' : r < 2 ? 'text-green-300' : 'text-emerald-200';
  return <span className={`text-xs font-bold ${cls}`}>{r >= 0 ? '+' : ''}{r}R</span>;
};

export default function StopLossTracker() {
  const [positions, setPositions] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [choice, setChoice] = useState({});       // per-position chosen SL price
  const [structIn, setStructIn] = useState({});    // per-position structural SL input
  const [busy, setBusy] = useState({});
  const [message, setMessage] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await fetch('/api/sl-alerts');
      if (!r.ok) throw new Error('fetch failed');
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
    setMessage(ok ? { type: 'ok', text: okMsg(data) } : { type: 'error', text: data.detail || 'Action failed' });
    if (ok) fetchData();
    setBusy(b => ({ ...b, [key]: false }));
  };

  const placeChosen = (p) => {
    const sl = Number(choice[p.id] ?? p.slOptions?.[0]?.price);
    if (!sl || sl <= 0 || sl >= p.current_price) return setMessage({ type: 'error', text: `${p.symbol}: choose a level below ₹${p.current_price}` });
    run(`set-${p.id}`, `Place SL for ${p.symbol} @ ₹${sl}?\n\nReal Dhan forever order.`,
      '/api/sl/place-at-level', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl },
      (d) => `✅ ${p.symbol}: SL @ ₹${d.trigger}`);
  };

  const moveChosen = (p) => {
    const sl = Number(choice[p.id]);
    if (!sl || sl <= 0 || sl >= p.current_price) return setMessage({ type: 'error', text: `${p.symbol}: choose a level below ₹${p.current_price}` });
    run(`move-${p.id}`, `Move ${p.symbol} SL to ₹${sl}?\n\nPlaces new SL, cancels old.`,
      '/api/sl/move', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl, oldOrderId: p.slOrders?.[0]?.orderId || '' },
      (d) => `🔼 ${p.symbol}: SL → ₹${d.trigger}`);
  };

  const structuralExit = (p) => run(`exit-${p.id}`,
    `Structural EXIT for ${p.symbol}?\n\nExit-forever sells at next open. Real Dhan order.`,
    '/api/sl/structural-exit', { securityId: p.id, quantity: p.quantity, symbol: p.symbol },
    (d) => `📤 ${p.symbol}: exit @ ~₹${d.trigger}`);

  const cancel = (p, orderId) => run(`cancel-${orderId}`, `Cancel SL for ${p.symbol}?`,
    '/api/sl/cancel', { orderId, symbol: p.symbol }, () => `🗑️ ${p.symbol}: SL cancelled`);

  const saveStructural = (p) => {
    const v = Number(structIn[p.id]);
    if (!v || v <= 0) return setMessage({ type: 'error', text: 'Enter a valid structural SL' });
    run(`struct-${p.id}`, null, '/api/sl/set-structural',
      { symbol: p.symbol, structuralSL: v }, () => `✅ ${p.symbol}: structural SL set to ₹${v}`);
  };

  const zoneStyle = (z) => ({
    SAFE: 'bg-green-900 text-green-300', WARNING: 'bg-yellow-900 text-yellow-300',
    CRITICAL: 'bg-red-900 text-red-300', DANGER: 'bg-red-600 text-white',
  }[z] || 'bg-slate-600 text-slate-200');

  // Compact structural cell: value if present, else inline editable input
  const StructuralCell = ({ p, small }) => (
    p.structuralSL && !p.structuralEditable ? (
      <span className="text-purple-300">₹{p.structuralSL}</span>
    ) : (
      <span className="inline-flex items-center gap-1">
        <input type="number" step="0.05" placeholder={p.structuralSL ? String(p.structuralSL) : 'set'}
          value={structIn[p.id] ?? (p.structuralSL || '')}
          onChange={e => setStructIn(s => ({ ...s, [p.id]: e.target.value }))}
          className={`bg-slate-900 border border-slate-600 rounded px-1.5 py-0.5 ${small ? 'w-16' : 'w-20'} text-xs focus:border-purple-500 focus:outline-none`} />
        <button onClick={() => saveStructural(p)} disabled={busy[`struct-${p.id}`]}
          title="Save structural SL"
          className="text-purple-300 hover:text-purple-200 disabled:opacity-50">
          {busy[`struct-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        </button>
      </span>
    )
  );

  if (loading) return <div className="p-8 text-slate-400">Loading stop loss data…</div>;

  const unprotected = positions.filter(p => p.riskZone === 'NO_SL');
  const protectedPos = positions.filter(p => p.riskZone !== 'NO_SL');
  const iconBtn = 'p-2 rounded-lg disabled:opacity-40 flex items-center justify-center';

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-7xl mx-auto space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl lg:text-3xl font-bold">🛡️ Stop Loss</h1>
            <p className="text-xs text-slate-400">{protectedPos.length} protected · {unprotected.length} unprotected</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => { setRefreshing(true); fetchData().finally(() => setRefreshing(false)); }}
              disabled={refreshing} title="Refresh prices & SL"
              className="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 disabled:opacity-50">
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
            <button onClick={() => setAutoRefresh(!autoRefresh)} title="Auto-refresh"
              className={`p-2 rounded-lg ${autoRefresh ? 'bg-blue-600' : 'bg-slate-700 text-slate-300'}`}>
              <Zap className="w-4 h-4" />
            </button>
          </div>
        </div>

        {message && (
          <div className={`rounded-lg p-3 text-sm flex items-center gap-2 ${message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
            {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
            {message.text}
          </div>
        )}

        {/* UNPROTECTED */}
        {unprotected.length > 0 && (
          <div>
            <h2 className="text-sm font-bold mb-2 flex items-center gap-2 text-orange-400">
              <ShieldOff className="w-4 h-4" /> Unprotected ({unprotected.length})
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
              {unprotected.map(p => (
                <div key={p.id} className={`rounded-lg p-3 ${rowTint(p)}`}>
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex items-center gap-2">
                      <p className="font-bold">{p.symbol}</p>
                      {rBadge(p.rMultiple)}
                    </div>
                    <div className="text-right">
                      <p className="font-bold text-sm">₹{p.current_price}</p>
                      <p className={`text-xs ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{p.pnl >= 0 ? '+' : ''}₹{p.pnl?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-1 text-[11px] mb-2">
                    <div><span className="text-slate-400">Buy</span><br/><span>₹{p.buyPrice}</span></div>
                    <div><span className="text-slate-400">Safety</span><br/><span>₹{p.safetySL ?? '—'}</span></div>
                    <div><span className="text-slate-400">Structural</span><br/><StructuralCell p={p} small /></div>
                  </div>
                  <div className="flex items-stretch gap-2">
                    <select value={choice[p.id] ?? p.slOptions?.[0]?.price ?? ''}
                      onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                      className="min-w-0 flex-1 bg-slate-900 border border-slate-600 rounded px-2 text-xs h-9 focus:border-orange-500 focus:outline-none">
                      {(p.slOptions || []).length === 0 && <option value="">No valid levels</option>}
                      {(p.slOptions || []).map(o => (
                        <option key={o.basis} value={o.price}>₹{o.price} · {o.label} ({o.pctFromEntry > 0 ? '+' : ''}{o.pctFromEntry}%)</option>
                      ))}
                    </select>
                    <button onClick={() => placeChosen(p)} disabled={busy[`set-${p.id}`] || !(p.slOptions || []).length}
                      title="Place SL" className="bg-orange-600 hover:bg-orange-700 disabled:opacity-50 font-semibold px-3 rounded text-sm flex items-center gap-1 h-9 flex-shrink-0">
                      {busy[`set-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />} Set
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* PROTECTED */}
        <div>
          <h2 className="text-sm font-bold mb-2 flex items-center gap-2 text-green-400">
            <Shield className="w-4 h-4" /> Protected ({protectedPos.length})
          </h2>
          {protectedPos.length === 0 ? (
            <p className="text-slate-400 text-sm py-4">No positions with active SL orders</p>
          ) : (
            <div className="space-y-2">
              {protectedPos.map(p => {
                const trailOpts = (p.slOptions || []).filter(o => o.price > p.stop_loss);
                return (
                <div key={p.id} className={`rounded-lg p-3 ${rowTint(p)}`}>
                  {(p.danger || p.watch) && (
                    <div className={`mb-2 text-xs font-semibold flex items-center gap-1.5 ${p.danger ? 'text-red-300' : 'text-amber-300'}`}>
                      <AlertTriangle className="w-3.5 h-3.5" />
                      {p.danger ? `Closed ₹${p.lastClose} below structural ₹${p.structuralSL} — EXIT at next open`
                                : `Live below structural ₹${p.structuralSL} — watch for a close below`}
                    </div>
                  )}
                  <div className="flex flex-col lg:flex-row lg:items-center gap-3">
                    {/* Metrics */}
                    <div className="grid grid-cols-3 lg:grid-cols-6 gap-x-4 gap-y-2 flex-1 text-sm">
                      <div>
                        <p className="text-[10px] text-slate-400">Symbol</p>
                        <p className="font-bold flex items-center gap-1.5">{p.symbol} {rBadge(p.rMultiple)}</p>
                      </div>
                      <div><p className="text-[10px] text-slate-400">Buy</p><p>₹{p.buyPrice}</p></div>
                      <div><p className="text-[10px] text-slate-400">Current</p><p className="text-blue-400">₹{p.current_price}</p></div>
                      <div><p className="text-[10px] text-slate-400">Safety −8%</p><p className="text-slate-300">₹{p.safetySL ?? '—'}</p></div>
                      <div><p className="text-[10px] text-slate-400">Structural</p><p><StructuralCell p={p} /></p></div>
                      <div>
                        <p className="text-[10px] text-slate-400">Current SL</p>
                        <p className="text-red-400 font-semibold">₹{p.stop_loss}
                          {p.slPctFromEntry != null && <span className="text-slate-400 font-normal"> ({p.slPctFromEntry > 0 ? '+' : ''}{p.slPctFromEntry}%)</span>}
                        </p>
                        <span className={`inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold ${p.danger ? zoneStyle('DANGER') : zoneStyle(p.riskZone)}`}>
                          {p.danger ? 'DANGER' : (p.slBasis || p.riskZone)}
                        </span>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-stretch gap-1.5 flex-shrink-0">
                      <select value={choice[p.id] ?? ''}
                        onChange={e => setChoice(s => ({ ...s, [p.id]: e.target.value }))}
                        disabled={trailOpts.length === 0}
                        title="Trail SL to a higher level"
                        className="min-w-0 w-32 bg-slate-900 border border-slate-600 rounded px-2 text-xs h-9 focus:border-green-500 focus:outline-none disabled:opacity-40">
                        <option value="">{trailOpts.length ? 'Trail…' : 'SL highest'}</option>
                        {trailOpts.map(o => (
                          <option key={o.basis} value={o.price}>₹{o.price} · {o.label}</option>
                        ))}
                      </select>
                      <button onClick={() => moveChosen(p)} disabled={busy[`move-${p.id}`] || !choice[p.id]}
                        title="Move SL up to the chosen level"
                        className={`${iconBtn} bg-green-700 hover:bg-green-600`}>
                        {busy[`move-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <TrendingUp className="w-4 h-4" />}
                      </button>
                      <button onClick={() => structuralExit(p)} disabled={busy[`exit-${p.id}`]}
                        title="Exit now (sells at next open)"
                        className={`${iconBtn} bg-amber-700 hover:bg-amber-600`}>
                        {busy[`exit-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : <LogOut className="w-4 h-4" />}
                      </button>
                      {p.slOrders?.slice(0, 1).map(o => (
                        <button key={o.orderId} onClick={() => cancel(p, o.orderId)} disabled={busy[`cancel-${o.orderId}`]}
                          title="Cancel SL order"
                          className={`${iconBtn} bg-red-800 hover:bg-red-700`}>
                          {busy[`cancel-${o.orderId}`] ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
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
          <div className="bg-slate-800/60 rounded-lg p-3">
            <h2 className="text-sm font-bold mb-2 flex items-center gap-2 text-yellow-400">
              <AlertTriangle className="w-4 h-4" /> Alerts ({alerts.length})
            </h2>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {alerts.map((a, i) => (
                <p key={i} className={`text-xs ${a.type === 'DANGER' ? 'text-red-300' : a.type === 'CRITICAL' ? 'text-red-300' : a.type === 'WARNING' || a.type === 'WATCH' ? 'text-yellow-300' : 'text-orange-300'}`}>{a.message}</p>
              ))}
            </div>
          </div>
        )}

        {/* Legend */}
        <div className="bg-slate-800/40 rounded-lg p-3 text-[11px] text-slate-400">
          <strong className="text-slate-300">Row colour = R-multiple</strong> (R = buy − structural SL, or −8% if unset):
          <span className="text-red-400"> below buy</span> ·
          <span className="text-green-400"> 0–1R</span> ·
          <span className="text-green-300"> 1–2R</span> ·
          <span className="text-emerald-200"> 2R+</span>.
          Actions: <TrendingUp className="w-3 h-3 inline" /> trail · <LogOut className="w-3 h-3 inline" /> exit · <Trash2 className="w-3 h-3 inline" /> cancel.
          Structural SL is editable (<Pencil className="w-3 h-3 inline" />) when not set from the sheet/screener.
        </div>
      </div>
    </div>
  );
}
