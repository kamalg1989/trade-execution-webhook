import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle, Shield, Loader, RefreshCw,
  MoreVertical, LogOut, Trash2, Check, ChevronDown, Zap, LayoutGrid, Table2, ChevronUp } from 'lucide-react';

const api = async (path, body) => {
  const apiKey = localStorage.getItem('trading_api_key');
  if (!apiKey || apiKey === 'undefined' || apiKey === 'null') {
    localStorage.removeItem('trading_api_key');
    alert('❌ API key not set on this device.\n\nGo to Settings → Trading Protection → Load API Key (enter PIN) once, then retry.');
    return { ok: false, data: { detail: 'API key not set' } };
  }
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
    body: JSON.stringify(body)
  });
  if (r.status === 401 || r.status === 403) {
    localStorage.removeItem('trading_api_key');
    return { ok: false, data: { detail: 'Stored API key invalid — cleared. Re-load it from Settings (PIN).' } };
  }
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
};

// ---------- R-ladder progress bar ----------
const RLadder = ({ p }) => {
  const hasProtection = p.stop_loss > 0;
  const rStop = hasProtection ? (p.structuralSL || p.safetySL) : (p.safetySL || p.structuralSL);
  if (!rStop || !p.buyPrice || p.buyPrice <= rStop) return null;
  const rUnit = p.buyPrice - rStop;
  const maxR = Math.max(3, Math.ceil(p.rMultiple ?? 0) + 1);
  // Extend the scale below Buy so an underwater position (negative R, e.g.
  // IKS at -1.41R) doesn't get clamped/pinned at the Buy tick — previously
  // Math.max(0, ...) floored every sub-Buy price to the same 0% position.
  const minR = Math.min(-1, Math.floor(p.rMultiple ?? 0));
  const span = maxR - minR;
  const toPct = (price) => Math.min(100, Math.max(0, (((price - p.buyPrice) / rUnit) - minR) / span * 100));
  const ticks = [];
  for (let i = minR; i <= maxR; i++) ticks.push(i);
  const buyPct = toPct(p.buyPrice);
  const curPct = toPct(p.current_price);
  const underwater = p.current_price < p.buyPrice;
  return (
    <div className="mt-3 mb-1">
      <div className="relative h-7">
        <div className="absolute top-2.5 left-0 right-0 h-1 rounded bg-slate-600/40" />
        <div className={`absolute top-2.5 h-1 rounded ${underwater ? 'bg-red-400' : 'bg-green-400'}`}
          style={{ left: `${Math.min(buyPct, curPct)}%`, width: `${Math.abs(curPct - buyPct)}%` }} />
        {ticks.map(i => (
          <React.Fragment key={i}>
            <div className="absolute top-1 w-0.5 h-4 bg-slate-500" style={{ left: `${((i - minR) / span) * 100}%` }} />
            <div className="absolute top-6 text-[9px] text-slate-400 -translate-x-1/2"
              style={{ left: `${((i - minR) / span) * 100}%` }}>{i === 0 ? 'Buy' : `${i}R`}</div>
          </React.Fragment>
        ))}
        {p.stop_loss > 0 && (
          <div className="absolute top-0.5 w-1 h-5 bg-red-400 rounded -translate-x-1/2"
            title={`SL ₹${p.stop_loss}`} style={{ left: `${toPct(p.stop_loss)}%` }} />
        )}
        <div className={`absolute top-1.5 w-3 h-3 rounded-full border-2 border-slate-900 -translate-x-1/2 ${underwater ? 'bg-red-400' : 'bg-green-400'}`}
          title={`Now ₹${p.current_price}`} style={{ left: `${curPct}%` }} />
      </div>
    </div>
  );
};

// Structural (−1R) and target (+2R) prices with % from buy
const RMeta = ({ p }) => {
  const rStop = p.structuralSL || p.safetySL;
  if (!rStop || !p.buyPrice || p.buyPrice <= rStop) return null;
  const isStruct = !!p.structuralSL;
  const rUnit = p.buyPrice - rStop;
  const t2 = p.buyPrice + 2 * rUnit;
  const riskPct = ((rUnit / p.buyPrice) * 100).toFixed(1);
  const rewPct = ((2 * rUnit / p.buyPrice) * 100).toFixed(1);
  return (
    <p className="text-[11px] text-slate-400 mt-1">
      {isStruct ? 'Struct −1R: ' : 'Safety −1R (no structural set): '}
      <span className="text-red-300">₹{rStop.toFixed(1)} (−{riskPct}%)</span>
      {' · '}Target +2R: <span className="text-green-300">₹{t2.toFixed(1)} (+{rewPct}%)</span>
    </p>
  );
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

const actionOf = (p) => p.recommendation?.action || 'NONE';
const actionLabel = (a) => ({
  EXIT: 'Exit', SET_SL: 'Set SL', SELL_HALF: 'Sell half', TRAIL: 'Trail', NONE: 'OK',
  EXIT_PENDING: 'Exit pending', HALF_EXIT_PENDING: 'Half-exit pending',
}[a] || a);
const actionClass = (a) => ({
  EXIT: 'text-red-400', SET_SL: 'text-amber-400', SELL_HALF: 'text-emerald-400', TRAIL: 'text-green-400',
  EXIT_PENDING: 'text-blue-400', HALF_EXIT_PENDING: 'text-blue-400',
}[a] || 'text-slate-400');

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
    case 'status': return actionLabel(actionOf(p));
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
  if (key === 'status') return actionClass(actionOf(p));
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
      let av = sortKey === 'status' ? actionOf(a) : a[sortKey];
      let bv = sortKey === 'status' ? actionOf(b) : b[sortKey];
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
              <tr key={p.id} className={`border-b border-slate-700/60 ${actionOf(p) === 'EXIT' ? 'bg-red-950/30' : ''}`}>
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
  const [viewMode, setViewMode] = useState('cards');
  const [summary, setSummary] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await fetch('/api/sl-alerts');
      if (!r.ok) throw new Error('fetch failed');
      const data = await r.json();
      setPositions(data.positions || []);
    } catch (e) { console.error('SL fetch failed:', e); }
    setLoading(false);
  }, []);

  const fetchSummary = useCallback(async () => {
    try {
      const r = await fetch('/api/portfolio');
      if (!r.ok) throw new Error('fetch failed');
      setSummary(await r.json());
    } catch (e) { console.error('Portfolio summary fetch failed:', e); }
  }, []);

  useEffect(() => { fetchData(); fetchSummary(); }, [fetchData, fetchSummary]);
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
    const raw = structIn[p.id];
    const v = (raw === undefined || raw === '') ? Number(p.structuralSL) : Number(raw);
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
  const exitPending = positions.filter(p => ['EXIT_PENDING', 'HALF_EXIT_PENDING'].includes(reco(p).action));
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
              {p.boughtToday && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/60 text-blue-300">bought today</span>}
            </div>
            <p className="text-xs text-slate-400 mt-1.5">
              Buy ₹{p.buyPrice} · Now <span className="text-blue-400">₹{p.current_price}</span>
              {p.stop_loss > 0 && <> · SL <span className="text-red-400">₹{p.stop_loss}</span></>}
              {p.structuralSL && <> · Struct ₹{p.structuralSL}</>}
              {p.pnl != null && <> · <span className={p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>{p.pnl >= 0 ? '+' : ''}₹{p.pnl?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span></>}
            </p>
            <RMeta p={p} />
            <p className="text-xs text-slate-300 mt-1">{r.reason}</p>
            <RLadder p={p} />
          </div>
          <div className="flex items-center gap-2 flex-shrink-0 flex-wrap" onClick={e => e.stopPropagation()}>
            {['EXIT_PENDING', 'HALF_EXIT_PENDING'].includes(r.action) ? (
              <span className="bg-blue-900/50 text-blue-300 font-semibold text-sm px-4 py-2.5 rounded-lg whitespace-nowrap flex items-center gap-2">
                ⏳ {r.label}
              </span>
            ) : r.action !== 'NONE' && (
              <button onClick={() => executeReco(p)} disabled={isBusy || (!r.trigger && r.action !== 'EXIT')}
                className={`${btnStyle[r.action]} disabled:opacity-50 text-white font-semibold text-sm px-4 py-2.5 rounded-lg whitespace-nowrap flex items-center gap-2`}>
                {isBusy ? <Loader className="w-4 h-4 animate-spin" /> : null}
                {r.label}
              </button>
            )}
            {r.action === 'SELL_HALF' && r.altTrail?.trigger && (
              <button onClick={() => run(`alt-${p.id}`,
                  `Trail ${p.symbol} FULL position SL to ₹${r.altTrail.trigger} (no selling)?\n\nPlaces new SL, cancels old.`,
                  '/api/sl/move', { securityId: p.id, quantity: p.quantity, symbol: p.symbol, trigger: r.altTrail.trigger, oldOrderId: p.slOrders?.[0]?.orderId || '' },
                  d => `🔼 ${p.symbol}: full position SL → ₹${d.trigger}`)}
                disabled={busy[`alt-${p.id}`]}
                className="bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white font-semibold text-sm px-4 py-2.5 rounded-lg whitespace-nowrap flex items-center gap-2">
                {busy[`alt-${p.id}`] ? <Loader className="w-4 h-4 animate-spin" /> : null}
                {r.altTrail.label}
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
            {exitPending.length > 0 && <span className="text-[11px] px-2.5 py-1 rounded-full bg-blue-900/60 text-blue-300 font-semibold">{exitPending.length} resting</span>}
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
            <button onClick={() => { setRefreshing(true); Promise.all([fetchData(), fetchSummary()]).finally(() => setRefreshing(false)); }}
              disabled={refreshing} title="Refresh" className="p-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 disabled:opacity-50">
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {summary && (
          <div className="grid grid-cols-3 gap-2 lg:gap-4">
            <div className="bg-slate-800/60 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-[11px] lg:text-sm mb-0.5 lg:mb-1">Invested</p>
              <p className="text-sm lg:text-xl font-bold truncate">
                ₹{summary.totalInvested?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className="bg-slate-800/60 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-[11px] lg:text-sm mb-0.5 lg:mb-1">Current Value</p>
              <p className="text-sm lg:text-xl font-bold text-blue-400 truncate">
                ₹{summary.totalValue?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </p>
            </div>
            <div className={`rounded-lg p-3 lg:p-4 ${summary.unrealizedPnL >= 0 ? 'bg-green-900/30' : 'bg-red-900/30'}`}>
              <p className="text-slate-400 text-[11px] lg:text-sm mb-0.5 lg:mb-1">Unrealized P&L</p>
              <p className={`text-sm lg:text-xl font-bold truncate ${summary.unrealizedPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {summary.unrealizedPnL >= 0 ? '+' : ''}₹{summary.unrealizedPnL?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </p>
            </div>
          </div>
        )}

        {message && (
          <div className={`rounded-lg p-3 text-sm flex items-center gap-2 ${message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
            {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
            {message.text}
          </div>
        )}

        {viewMode === 'table' && (
          <PositionsTable positions={positions}
            onRefresh={() => { setRefreshing(true); fetchData().finally(() => setRefreshing(false)); }}
            refreshing={refreshing} />
        )}

        {viewMode === 'cards' && pending === 0 && (
          <div className="rounded-xl p-6 bg-green-900/20 border border-green-800/40 text-center">
            <CheckCircle className="w-8 h-8 text-green-400 mx-auto mb-2" />
            <p className="font-semibold text-green-300">All clear — nothing to do tonight</p>
            <p className="text-xs text-slate-400 mt-1">Every position is protected and within its R ladder.</p>
          </div>
        )}

        {viewMode === 'cards' && <Section title="STEP 1 — EXIT REQUIRED" color="text-red-400" items={exits} accent="border-l-red-500" />}
        {viewMode === 'cards' && <Section title="RESTING — ORDER PLACED, AWAITING FILL AT OPEN" color="text-blue-400" items={exitPending} accent="border-l-blue-500" />}
        {viewMode === 'cards' && <Section title="STEP 2 — PLACE INITIAL SL" color="text-amber-400" items={unprotected} accent="border-l-amber-500" />}
        {viewMode === 'cards' && <Section title="STEP 3 — BOOK PROFIT / TRAIL (R-LADDER)" color="text-green-400" items={trailDue} accent="border-l-green-500" />}

        {/* Nothing to do */}
        {viewMode === 'cards' && done.length > 0 && (
          <div>
            <h2 className="text-[11px] font-bold tracking-widest mb-2 text-slate-500">NOTHING TO DO ({done.length})</h2>
            <div className="space-y-1.5">
              {(showDone ? done : done.slice(0, 4)).map(p => (
                <div key={p.id} className="px-4 py-2.5 rounded-lg bg-slate-800/40 border border-slate-700/40 flex items-start justify-between gap-2">
                  <div className="text-sm min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold">{p.symbol}</span>
                      {p.rMultiple != null && (
                        <span className={`text-xs font-bold ${p.rMultiple < 0 ? 'text-red-400' : 'text-green-400'}`}>
                          {p.rMultiple >= 0 ? '+' : ''}{p.rMultiple}R
                        </span>
                      )}
                      {p.boughtToday && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/60 text-blue-300">bought today</span>}
                      <span className="text-xs text-slate-400">
                        Buy ₹{p.buyPrice} · Now <span className="text-blue-400">₹{p.current_price}</span> · SL ₹{p.stop_loss}{p.slBasis ? ` (${p.slBasis})` : ''} · {reco(p).reason}
                      </span>
                    </div>
                    <RMeta p={p} />
                    <RLadder p={p} />
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
