import React, { useState } from 'react';
import { aiAnalyze } from '../api/client.js';
import AiResultModal from './AiResultModal.jsx';
import { Tip } from './FilterPanel.jsx';

// AI filter stage: runs on the current screener results (max 50 symbols).
// Shows ranked AI-analyzed list; row click opens the analysis popup.

const REC_BADGE = {
  SETUP_READY: 'bg-emerald-900/60 text-emerald-300',
  EARLY_STAGE: 'bg-amber-900/60 text-amber-300',
  NOT_READY: 'bg-slate-800 text-slate-400',
  AVOID: 'bg-red-900/60 text-red-300',
};

const fmt = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));

export default function AiAnalysisPanel({ symbols, date }) {
  const [gateMode, setGateMode] = useState('hard');
  const [threshold, setThreshold] = useState(0.3);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState(null);
  const [picked, setPicked] = useState(null);
  const [showGated, setShowGated] = useState(false);

  const run = async () => {
    setRunning(true);
    setError('');
    try {
      const res = await aiAnalyze({
        symbols: symbols.slice(0, 50),
        indicatorDate: date || null,
        gateMode,
        ifpThreshold: Number(threshold),
      });
      setData(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  if (!symbols.length) return null;
  const results = data?.results || [];
  const gated = data?.gated || [];

  return (
    <div className="bg-slate-900/60 border border-purple-800/60 rounded-lg p-3 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="text-[11px] uppercase tracking-wide text-purple-400 w-full sm:w-auto">
          AI analysis — IFP · base · patterns
        </div>
        <label className="flex flex-col text-xs text-slate-300 gap-1">
          <span className="flex items-center gap-1.5">Gate <Tip text="Pre-AI cost filter. Hard: stocks below the IFP threshold are dropped BEFORE calling the AI - zero cost for them, listed under 'gated out'. Soft: every stock goes to the AI regardless (higher cost; use when you want a second opinion on weak-IFP names). Uses the stored nightly IFP score." /></span>
          <select value={gateMode} onChange={(e) => setGateMode(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 w-28">
            <option value="hard">Hard (cheap)</option>
            <option value="soft">Soft (all)</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-slate-300 gap-1">
          <span className="flex items-center gap-1.5">IFP threshold <Tip text="Minimum nightly IFP score to pass the hard gate and reach the AI. 0.30 = balanced default; raise to 0.40 to analyze only strong-footprint stocks (cheapest); lower to widen the net." /></span>
          <input type="number" step="0.05" min="0" max="1" value={threshold}
            onChange={(e) => setThreshold(e.target.value)} disabled={gateMode === 'soft'}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 w-24 text-slate-100 disabled:opacity-40" />
        </label>
        <button onClick={run} disabled={running}
          className="px-4 py-1.5 text-sm rounded bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white font-semibold">
          {running ? 'Analyzing… (~30s)' : `AI analyze ${Math.min(symbols.length, 50)} stocks`}
        </button>
        {data && (
          <span className="text-xs text-slate-400">
            {data.gate.passed} passed gate · {data.fromStore} cached · {data.analyzed} analyzed · {data.gate.gatedOut} gated out
          </span>
        )}
      </div>

      {error && <div className="bg-red-900/40 border border-red-700 text-red-200 text-sm rounded px-3 py-2">{error}</div>}

      {results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
                <th className="py-1.5 pr-2">Symbol</th>
                <th className="pr-2">IFP</th>
                <th className="pr-2">Phase</th>
                <th className="pr-2">Base</th>
                <th className="pr-2">Pattern</th>
                <th className="pr-2">Buy point</th>
                <th className="pr-2">Conf</th>
                <th className="pr-2">Verdict</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                const a = r.analysis || {};
                const top = (a.patterns || [])[0];
                const verified = r.verification?.overall;
                return (
                  <tr key={r.symbol} onClick={() => !r.error && setPicked(r)}
                    className={`border-t border-slate-800 ${r.error ? 'opacity-50' : 'cursor-pointer hover:bg-slate-800/60'}`}>
                    <td className="py-2 pr-2 font-semibold text-slate-100">{r.symbol}</td>
                    {r.error ? (
                      <td colSpan="7" className="text-xs text-red-300">{r.error}</td>
                    ) : (
                      <>
                        <td className="pr-2 text-slate-300">{fmt(r.ifpScore)}</td>
                        <td className="pr-2 text-slate-300 capitalize">{a.market_cycle_phase || '—'}</td>
                        <td className="pr-2 text-slate-300">{a.base_count || '—'}</td>
                        <td className="pr-2 text-slate-300">{top ? top.type.replace(/_/g, ' ') : '—'}</td>
                        <td className="pr-2 text-slate-300 capitalize">{(a.buy_point?.type || '—').replace(/_/g, ' ')}</td>
                        <td className="pr-2 text-slate-300">{fmt(a.confidence)}</td>
                        <td className="pr-2">
                          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${REC_BADGE[a.recommendation] || REC_BADGE.NOT_READY}`}>
                            {a.recommendation || '—'}
                          </span>
                          {verified === 'mismatch' && <span title="AI levels differ from computed" className="ml-1 text-amber-400">⚠</span>}
                        </td>
                        <td className="text-slate-600">›</td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {gated.length > 0 && (
        <div>
          <button onClick={() => setShowGated(!showGated)} className="text-xs text-slate-500 hover:text-slate-300">
            {showGated ? '▾' : '▸'} {gated.length} stocks gated out (weak IFP)
          </button>
          {showGated && (
            <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-1">
              {gated.map((g) => (
                <span key={g.symbol}>{g.symbol} ({g.ifp_score == null ? g.reason : fmt(g.ifp_score)})</span>
              ))}
            </div>
          )}
        </div>
      )}

      <AiResultModal result={picked} date={data?.indicatorDate} open={!!picked} onClose={() => setPicked(null)} />
    </div>
  );
}
