import React, { useState, useEffect, useCallback } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle, Shield, Loader, RefreshCw,
  MoreVertical, LogOut, Trash2, Check, ChevronDown, Zap } from 'lucide-react';

const api = async (path, body) => {
  const apiKey = localStorage.getItem('trading_api_key');
  if (!apiKey) {
    alert('❌ API key not found. Please load it from Settings first.');
    return { ok: false, data: {} };
  }
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
    body: JSON.stringify(body)
  });
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
};

// ---------- R-ladder progress bar ----------
const RLadder = ({ p }) => {
  const rStop = p.structuralSL || p.safetySL;
  if (!rStop || !p.buyPrice || p.buyPrice <= rStop) return null;
  const rUnit = p.buyPrice - rStop;
  const maxR = Math.max(3, Math.ceil(p.rMultiple ?? 0) + 1);
  const toPct = (price) => Math.min(97, Math.max(0, ((price - p.buyPrice) / (rUnit * maxR)) * 100));
  const ticks = [];
  for (let i = 0; i <= maxR; i++) ticks.push(i);
  return (
    <div className="mt-3 mb-1">
      <div className="relative h-7">
        <div className="absolute top-2.5 left-0 right-0 h-1 rounded bg-slate-600/40" />
        <div className="absolute top-2.5 left-0 h-1 rounded bg-green-400"
          style={{ width: `${toPct(p.current_price)}%` }} />
        {ticks.map(i => (
          <React.Fragment key={i}>
            <div className="absolute top-1 w-0.5 h-4 bg-slate-500" style={{ left: `${(i / maxR) * 100}%` }} />
            <div className="absolute top-6 text-[9px] text-slate-400 -translate-x-1/2"
              style={{ left: `${(i / maxR) * 100}%` }}>{i === 0 ? 'Buy' : `${i}R`}</div>
          </React.Fragment>
        ))}
        {p.stop_loss > 0 && (
          <div className="absolute top-0.5 w-1 h-5 bg-red-400 rounded -translate-x-1/2"
            title={`SL ₹${p.stop_loss}`} style={{ left: `${toPct(p.stop_loss)}%` }} />
        )}
        <div className="absolute top-1.5 w-3 h-3 rounded-full bg-green-400 border-2 border-slate-900 -translate-x-1/2"
          title={`Now ₹${p.current_price}`} style={{ left: `${toPct(p.current_price)}%` }} />
      </div>
    </div>
  );
};

export default function StopLossTracker() {
  const [positions, setPositions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState({});
  const [message, setMessage] = useState(null);
  const [menuOpen, setMenuOpen] = useState(null);   // position id with open menu
  const [showDone, setShowDone] = useState(false);
  const [structIn, setStructIn] = useState({});
  const [customSl, setCustomSl] = useState({});

  const fetchData = useCallback(async () => {
    try {
      const r = await fetch('/api/sl-alerts');
      if (!r.ok) throw new Error('fetch failed');
      const data = await r.json();
      setPositions(data.positions || []);
    } catch (e) { console.error('SL fetch failed:', e); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    const close = () => setMenuOpen(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, []);

  const run = async (key, confirmMsg, path, body, okMsg) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(b => ({ ...b, [key]: true }));
    setMessage(null);
    const { ok, data } = await api(path, body);
    setMessage(ok ? { type: 'ok', text: okMsg(data) } : { type: 'error', text: data.detail || 'Action failed' });
    if (ok) fetchData();
    setBusy(b => ({ ...b, [key]: false }));
  };

  // ---------- Execute the recommended action ----------
  const executeReco = (p) => {
    const r = p.recommendation || {};
    if (r.action === 'EXIT') {
      run(`reco-${p.id}`, `EXIT ${p.symbol} at next open?\n\n${r.reason}\nReal Dhan order.`,
        '/api/sl/structural-exit', { securityId: p.id, quantity: p.quantity, symbol: p.symbol },
        d => `📤 ${p.symbol}: exit placed @ ~₹${d.trigger}`);
    } else if (r.action === 'SET_SL' && r.trigger) {
      run(`reco-${p.id}`, `Place SL for ${p.symbol} @ ₹${r.trigger}?\n\nReal Dhan forever order.`,
        '/api/sl/place-at-level', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: r.trigger },
        d => `🛡️ ${p.symbol}: SL set @ ₹${d.trigger}`);
    } else if (r.action === 'SELL_HALF') {
      run(`reco-${p.id}`, `${p.symbol}: sell HALF (${Math.floor(p.quantity / 2)}) at next open and move SL on the rest to ₹${r.trigger}?\n\nReal Dhan orders.`,
        '/api/sl/sell-half', { securityId: p.id, symbol: p.symbol, newTrigger: r.trigger },
        d => `💰 ${d.message}`);
    } else if (r.action === 'TRAIL' && r.trigger) {
      run(`reco-${p.id}`, `Move ${p.symbol} SL up to ₹${r.trigger}?\n\nPlaces new SL, cancels old.`,
        '/api/sl/move', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: r.trigger, oldOrderId: p.slOrders?.[0]?.orderId || '' },
        d => `🔼 ${p.symbol}: SL → ₹${d.trigger}`);
    }
  };

  // ---------- Menu actions ----------
  const customMove = (p) => {
    const sl = Number(customSl[p.id]);
    if (!sl || sl <= 0 || sl >= p.current_price) return setMessage({ type: 'error', text: `${p.symbol}: level must be below ₹${p.current_price}` });
    const hasSl = p.stop_loss > 0;
    run(`custom-${p.id}`, `${hasSl ? 'Move' : 'Place'} ${p.symbol} SL ${hasSl ? 'to' : '@'} ₹${sl}?`,
      hasSl ? '/api/sl/move' : '/api/sl/place-at-level',
      hasSl ? { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl, oldOrderId: p.slOrders?.[0]?.orderId || '' }
            : { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: sl },
      d => `✅ ${p.symbol}: SL @ ₹${d.trigger}`);
  };

  const saveStructural = (p) => {
    const v = Number(structIn[p.id]);
    if (!v || v <= 0) return setMessage({ type: 'error', text: 'Enter a valid structural SL' });
    run(`struct-${p.id}`, null, '/api/sl/set-structural',
      { symbol: p.symbol, structuralSL: v }, () => `✅ ${p.symbol}: structural SL ₹${v}`);
  };

  if (loading) return <div className="p-8 text-slate-400 dark:text-slate-400">Loading stop loss data…</div>;

  const reco = (p) => p.recommendation || { action: 'NONE' };
  const exits = positions.filter(p => reco(p).action === 'EXIT');
  const unprotected = positions.filter(p => reco(p).action === 'SET_SL');
  const trailDue = positions.filter(p => ['SELL_HALF', 'TRAIL'].includes(reco(p).action));
  const done = positions.filter(p => reco(p).action === 'NONE');
  const pending = exits.length + unprotected.length + trailDue.length;

  const btnStyle = {
    EXIT: 'bg-red-600 hover:bg-red-700',
    SET_SL: 'bg-amber-600 hover:bg-amber-700',
    SELL_HALF: 'bg-emerald-600 hover:bg-emerald-700',
    TRAIL: 'bg-green-600 hover:bg-green-700',
  };

  const Card = ({ p, accent }) => {
    const r = reco(p);
    const isBusy = busy[`reco-${p.id}`];
    return (
      <div className={`rounded-xl p-4 bg-slate-800/70 border border-slate-700/60 border-l-4 ${accent}`}>
        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-bold text-[15px]">{p.symbol}</span>
              {p.rMultiple != null && (
                <span className={`text-xs font-bold ${p.rMultiple < 0 ? 'text-red-400' : 'text-green-400'}`}>
                  {p.rMultiple >= 0 ? '+' : ''}{p.rMultiple}R
                </span>
              )}
              {p.halfBooked && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/60 text-emerald-300">half booked</span>}
            </div>
            <p className="text-xs text-slate-400 mt-1.5">
              Buy ₹{p.buyPrice} · Now <span className="text-blue-400">₹{p.current_price}</span>
              {p.stop_loss > 0 && <> · SL <span className="text-red-400">₹{p.stop_loss}</span></>}
              {p.structuralSL && <> · Struct ₹{p.structuralSL}</>}
              {p.pnl != null && <> · <span className={p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>{p.pnl >= 0 ? '+' : ''}₹{p.pnl?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span></>}
            </p>
            <p className="text-xs text-slate-300 mt-1">{r.reason}</p>
            {['SELL_HALF', 'TRAIL'].includes(r.action) && <RLadder p={p} />}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0" onClick={e => e.stopPropagation()}>
            {r.action !== 'NONE' && (
              <button onClick={() => executeReco(p)} disabled={isBusy || (!r.trigger && r.action !== 'EXIT')}
                className={`${btnStyle[r.action]} disabled:opacity-50 text-white font-semibold text-sm px-4 py-2.5 rounded-lg whitespace-nowrap flex items-center gap-2`}>
                {isBusy ? <Loader className="w-4 h-4 animate-spin" /> : null}
                {r.label}
              </button>
            )}
            <div className="relative">
              <button onClick={() => setMenuOpen(menuOpen === p.id ? null : p.id)}
                className="p-2.5 rounded-lg bg-slate-700/70 hover:bg-slate-600 text-slate-300">
                <MoreVertical className="w-4 h-4" />
              </button>
              {menuOpen === p.id && (
                <div className="absolute right-0 mt-1 w-64 bg-slate-800 border border-slate-600 rounded-lg shadow-xl z-30 p-3 space-y-3">
                  <div>
                    <p className="text-[11px] text-slate-400 mb-1">Custom SL level</p>
                    <div className="flex gap-1.5">
                      <input type="number" step="0.05" placeholder={`< ₹${p.current_price}`}
                        value={customSl[p.id] ?? ''}
                        onChange={e => setCustomSl(s => ({ ...s, [p.id]: e.target.value }))}
                        className="flex-1 min-w-0 bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-xs" />
                      <button onClick={() => customMove(p)} disabled={busy[`custom-${p.id}`]}
                        className="bg-blue-600 hover:bg-blue-700 px-3 rounded text-xs font-semibold">
                        {busy[`custom-${p.id}`] ? <Loader className="w-3.5 h-3.5 animate-spin" /> : 'Set'}
                      </button>
                    </div>
                    {(p.slOptions || []).length > 0 && (
                      <select onChange={e => setCustomSl(s => ({ ...s, [p.id]: e.target.value }))}
                        className="w-full mt-1.5 bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-xs" defaultValue="">
                        <option value="" disabled>Suggested levels…</option>
                        {p.slOptions.map(o => <option key={o.basis} value={o.price}>₹{o.price} · {o.label}</option>)}
                      </select>
                    )}
                  </div>
                  {p.structuralEditable && (
                    <div>
                      <p className="text-[11px] text-slate-400 mb-1">Structural SL</p>
                      <div className="flex gap-1.5">
                        <input type="number" step="0.05" placeholder={p.structuralSL ? String(p.structuralSL) : 'set'}
                          value={structIn[p.id] ?? ''}
                          onChange={e => setStructIn(s => ({ ...s, [p.id]: e.target.value }))}
                          className="flex-1 min-w-0 bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-xs" />
                        <button onClick={() => saveStructural(p)} disabled={busy[`struct-${p.id}`]}
                          className="bg-purple-600 hover:bg-purple-700 px-3 rounded text-xs font-semibold">
                          <Check className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="border-t border-slate-700 pt-2 space-y-1">
                    <button onClick={() => run(`exit-${p.id}`, `Exit ${p.symbol} at next open?`, '/api/sl/structural-exit',
                        { securityId: p.id, quantity: p.quantity, symbol: p.symbol }, d => `📤 ${p.symbol}: exit @ ~₹${d.trigger}`)}
                      className="w-full text-left text-xs px-2 py-1.5 rounded hover:bg-slate-700 text-amber-300 flex items-center gap-2">
                      <LogOut className="w-3.5 h-3.5" /> Exit full position at open
                    </button>
                    {p.slOrders?.slice(0, 1).map(o => (
                      <button key={o.orderId}
                        onClick={() => run(`cancel-${o.orderId}`, `Cancel SL for ${p.symbol}?`, '/api/sl/cancel',
                          { orderId: o.orderId, symbol: p.symbol }, () => `🗑️ ${p.symbol}: SL cancelled`)}
                        className="w-full text-left text-xs px-2 py-1.5 rounded hover:bg-slate-700 text-red-300 flex items-center gap-2">
                        <Trash2 className="w-3.5 h-3.5" /> Cancel SL order
                      </button>
                    ))}
                    {p.halfBooked && (
                      <button onClick={() => run(`clearhalf-${p.id}`, null, '/api/sl/clear-half-booked',
                          { symbol: p.symbol }, () => `${p.symbol}: half-booked flag cleared`)}
                        className="w-full text-left text-xs px-2 py-1.5 rounded hover:bg-slate-700 text-slate-300 flex items-center gap-2">
                        <Zap className="w-3.5 h-3.5" /> Clear half-booked flag
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  };

  const Section = ({ title, color, items, accent }) => items.length === 0 ? null : (
    <div>
      <h2 className={`text-[11px] font-bold tracking-widest mb-2 ${color}`}>{title}</h2>
      <div className="space-y-2.5">{items.map(p => <Card key={p.id} p={p} accent={accent} />)}</div>
    </div>
  );

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-2xl lg:text-3xl font-bold">🛡️ Tonight's actions</h1>
            <p className="text-xs text-slate-400 mt-1">
              EOD review · {pending} action{pending !== 1 ? 's' : ''} pending · {done.length} OK
            </p>
          </div>
          <div className="flex items-center gap-2">
            {exits.length > 0 && <span className="text-[11px] px-2.5 py-1 rounded-full bg-red-900/60 text-red-300 font-semibold">{exits.length} exit</span>}
            {unprotected.length > 0 && <span className="text-[11px] px-2.5 py-1 rounded-full bg-amber-900/60 text-amber-300 font-semibold">{unprotected.length} unprotected</span>}
            {trailDue.length > 0 && <span className="text-[11px] px-2.5 py-1 rounded-full bg-green-900/60 text-green-300 font-semibold">{trailDue.length} trail due</span>}
            <button onClick={() => { setRefreshing(true); fetchData().finally(() => setRefreshing(false)); }}
              disabled={refreshing} className="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 disabled:opacity-50">
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {message && (
          <div className={`rounded-lg p-3 text-sm flex items-center gap-2 ${message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
            {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
            {message.text}
          </div>
        )}

        {pending === 0 && (
          <div className="rounded-xl p-6 bg-green-900/20 border border-green-800/40 text-center">
            <CheckCircle className="w-8 h-8 text-green-400 mx-auto mb-2" />
            <p className="font-semibold text-green-300">All clear — nothing to do tonight</p>
            <p className="text-xs text-slate-400 mt-1">Every position is protected and within its R ladder.</p>
          </div>
        )}

        <Section title="STEP 1 — EXIT REQUIRED" color="text-red-400" items={exits} accent="border-l-red-500" />
        <Section title="STEP 2 — PLACE INITIAL SL" color="text-amber-400" items={unprotected} accent="border-l-amber-500" />
        <Section title="STEP 3 — BOOK PROFIT / TRAIL (R-LADDER)" color="text-green-400" items={trailDue} accent="border-l-green-500" />

        {/* Nothing to do */}
        {done.length > 0 && (
          <div>
            <h2 className="text-[11px] font-bold tracking-widest mb-2 text-slate-500">NOTHING TO DO ({done.length})</h2>
            <div className="space-y-1.5">
              {(showDone ? done : done.slice(0, 4)).map(p => (
                <div key={p.id} className="flex items-center justify-between px-4 py-2.5 rounded-lg bg-slate-800/40 border border-slate-700/40">
                  <div className="text-sm min-w-0 flex items-center gap-2 flex-wrap">
                    <span className="font-semibold">{p.symbol}</span>
                    {p.rMultiple != null && (
                      <span className={`text-xs font-bold ${p.rMultiple < 0 ? 'text-red-400' : 'text-green-400'}`}>
                        {p.rMultiple >= 0 ? '+' : ''}{p.rMultiple}R
                      </span>
                    )}
                    <span className="text-xs text-slate-400">
                      SL ₹{p.stop_loss}{p.slBasis ? ` (${p.slBasis})` : ''} · {reco(p).reason}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0" onClick={e => e.stopPropagation()}>
                    <span className="text-[10px] px-2 py-0.5 rounded bg-slate-700/60 text-slate-400 font-semibold">SL OK</span>
                    <button onClick={() => setMenuOpen(menuOpen === p.id ? null : p.id)}
                      className="p-1.5 rounded bg-slate-700/40 hover:bg-slate-600 text-slate-400">
                      <MoreVertical className="w-3.5 h-3.5" />
                    </button>
                    {menuOpen === p.id && (
                      <div className="absolute right-8 mt-2 w-64 bg-slate-800 border border-slate-600 rounded-lg shadow-xl z-30 p-3 space-y-2">
                        <div className="flex gap-1.5">
                          <input type="number" step="0.05" placeholder={`SL < ₹${p.current_price}`}
                            value={customSl[p.id] ?? ''}
                            onChange={e => setCustomSl(s => ({ ...s, [p.id]: e.target.value }))}
                            className="flex-1 min-w-0 bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-xs" />
                          <button onClick={() => customMove(p)} className="bg-blue-600 hover:bg-blue-700 px-3 rounded text-xs font-semibold">Set</button>
                        </div>
                        <button onClick={() => run(`exit-${p.id}`, `Exit ${p.symbol} at next open?`, '/api/sl/structural-exit',
                            { securityId: p.id, quantity: p.quantity, symbol: p.symbol }, d => `📤 ${p.symbol}: exit @ ~₹${d.trigger}`)}
                          className="w-full text-left text-xs px-2 py-1.5 rounded hover:bg-slate-700 text-amber-300">Exit at open</button>
                        {p.slOrders?.slice(0, 1).map(o => (
                          <button key={o.orderId}
                            onClick={() => run(`cancel-${o.orderId}`, `Cancel SL for ${p.symbol}?`, '/api/sl/cancel',
                              { orderId: o.orderId, symbol: p.symbol }, () => `🗑️ ${p.symbol}: SL cancelled`)}
                            className="w-full text-left text-xs px-2 py-1.5 rounded hover:bg-slate-700 text-red-300">Cancel SL</button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {done.length > 4 && (
                <button onClick={() => setShowDone(!showDone)}
                  className="w-full text-center text-xs text-slate-400 hover:text-slate-200 py-2 flex items-center justify-center gap-1">
                  {showDone ? 'Show less' : `Show ${done.length - 4} more`}
                  <ChevronDown className={`w-3.5 h-3.5 ${showDone ? 'rotate-180' : ''}`} />
                </button>
              )}
            </div>
          </div>
        )}

        {/* Rule legend */}
        <div className="bg-slate-800/40 rounded-lg p-3 text-[11px] text-slate-400">
          <strong className="text-slate-300">The 3-rule ladder:</strong>
          <span className="text-slate-300"> +1R</span> → SL to breakeven ·
          <span className="text-slate-300"> +2R</span> → sell half, SL to +1R ·
          <span className="text-slate-300"> +NR</span> → trail SL to +(N−1)R.
          Close below structural always exits at next open. R = buy − structural SL (or −8% safety if unset).
        </div>
      </div>
    </div>
  );
}
