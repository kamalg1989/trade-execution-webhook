import React, { useState } from 'react';
import { aiAnalyze, aiOutcomesSummary } from '../api/client.js';
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

// Small marker for AI-generated columns (vs computed-by-our-math columns)
const AI_ICON = <span title="AI-generated value" className="text-purple-400 text-[9px] align-super">✦</span>;

const CHIP = { strong: 'bg-emerald-900/60 text-emerald-300', moderate: 'bg-amber-900/60 text-amber-300', weak: 'bg-red-900/60 text-red-300' };
export function IfpChips({ ifp }) {
  return (
    <span className="inline-flex gap-1">
      {[['V', ifp.volume_pattern, 'Volume pattern'], ['S', ifp.base_structure, 'Base structure'], ['P', ifp.pullback_depth, 'Pullback depth']].map(([l, v, t]) => (
        <span key={l} title={`${t}: ${v}`} className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${CHIP[v] || 'bg-slate-800 text-slate-400'}`}>{l}</span>
      ))}
    </span>
  );
}

export default function AiAnalysisPanel({ symbols, date }) {
  const [gateMode, setGateMode] = useState('hard');
  const [aiMode, setAiMode] = useState('gemini');
  const [chartScope, setChartScope] = useState('daily');
  const [promptVersion, setPromptVersion] = useState('v3');
  const [threshold, setThreshold] = useState(0.3);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState(null);
  const [picked, setPicked] = useState(null);
  const [showGated, setShowGated] = useState(false);
  const [perf, setPerf] = useState(null);
  const [showPerf, setShowPerf] = useState(false);
  const togglePerf = async () => {
    if (!showPerf && !perf) {
      try { setPerf((await aiOutcomesSummary()).summary || []); } catch { setPerf([]); }
    }
    setShowPerf(!showPerf);
  };

  const run = async () => {
    setRunning(true);
    setError('');
    try {
      const res = await aiAnalyze({
        symbols: symbols.slice(0, 50),
        indicatorDate: date || null,
        gateMode,
        ifpThreshold: Number(threshold),
        aiMode,
        chartScope,
        promptVersion,
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
          <span className="flex items-center gap-1.5">Prompt <Tip text="Which analysis prompt the AI runs. V3 VISUAL (new default): pure chart-reading - the AI sees ONLY the chart image plus two calibration examples from your own trades (COHANCE = strong IFP, TNPETRO = weak), rates volume pattern / base structure / pullback depth, flags extended stocks, and gives a crisp 2-sentence verdict. No computed numbers are fed in - our math is used only afterwards as an independent cross-check. Daily chart only, cheapest (~60-70% fewer tokens than v2). V2 GROUNDED: the earlier prompt - AI gets the chart plus computed IFP/level numbers and reports phase, base count and patterns. Results are stored per prompt version, so the AI performance table compares v2 vs v3 win rates directly." /></span>
          <select value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 w-36">
            <option value="v3">v3 — Visual (new)</option>
            <option value="v2">v2 — Grounded</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-slate-300 gap-1">
          <span className="flex items-center gap-1.5">AI engine <Tip text="Which model analyzes the charts. GEMINI (~Rs 0.15/stock, daily-only): Google's Gemini 3.1 Flash-Lite - cheapest option and now the default, but it's a 'Lite' tier model so chart-reading/reasoning quality is unproven here - not yet A/B tested against Sonnet, so treat any recommendation loosely until you've compared a few. HAIKU (~Rs 0.5/stock): fast cheap scan - in our head-to-head test it was too optimistic on weak charts (called setups where Sonnet said AVOID), so treat its SETUP_READY loosely too. SONNET (~Rs 2/stock): best judgment, most reliable AVOID calls - use before putting real money on a setup. HYBRID (~Rs 0.9/stock): Haiku scans everything, Sonnet automatically re-checks anything Haiku rates SETUP_READY or EARLY_STAGE - Gemini is not part of this chain. Results are stored per model, so switching engines re-analyzes only if that model has not seen the stock/date." /></span>
          <select value={aiMode} onChange={(e) => setAiMode(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 w-40">
            <option value="gemini">Gemini 3.1 Flash-Lite (cheapest)</option>
            <option value="haiku">Haiku (cheap)</option>
            <option value="hybrid">Hybrid (best value)</option>
            <option value="sonnet">Sonnet (best)</option>
          </select>
        </label>
        {promptVersion !== 'v3' && (
        <label className="flex flex-col text-xs text-slate-300 gap-1">
          <span className="flex items-center gap-1.5">Charts <Tip text="Daily only (default): one chart image, ~40% cheaper and faster than daily+weekly (e.g. Gemini ~Rs 0.15, Haiku ~Rs 0.4, Sonnet ~Rs 1.6 per stock) - fine for quick pattern/IFP scans, but base_count and phase lean on daily structure alone. Daily + weekly: the model also sees weekly context - base counting and market-cycle phase are much more reliable with it (your deck counts bases on weekly structure), at higher cost." /></span>
          <select value={chartScope} onChange={(e) => setChartScope(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100 w-36">
            <option value="daily">Daily only (-40%)</option>
            <option value="both">Daily + weekly</option>
          </select>
        </label>
        )}
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
                <th className="pr-2"><span className="inline-flex items-center gap-1">IFP <Tip text="Computed nightly IFP score (0-1) from our own volume math - NOT from the AI. Used by the hard gate. Fraction of recent days showing the institutional accumulation signature." /></span></th>
                <th className="pr-2"><span className="inline-flex items-center gap-1">Base type{AI_ICON} <Tip text="AI-read base classification. TYPE A = base after uptrend: a strong prior move up, then a pullback/consolidation base, now breaking out (e.g. COHANCE). TYPE B = accumulation after distribution: long downtrend or sideways period, institutions quietly accumulating at lows, base formed at the bottom, now breaking out. Both are valid trades - type is context, not a filter. (v2 results show market-cycle phase here instead.)" /></span></th>
                <th className="pr-2"><span className="inline-flex items-center gap-1">Vol(V)·Struct(S)·Pull(P){AI_ICON} <Tip text="The AI's three IFP quality ratings, each strong (green) / moderate (amber) / weak (red). V = VOLUME PATTERN (most important): spike on the move up, then dry-up in the base. S = BASE STRUCTURE: tight orderly consolidation vs wide messy chop. P = PULLBACK DEPTH: shallow pullback with a clear floor vs giving back most of the move. Hover each chip for its rating." /></span></th>
                <th className="pr-2"><span className="inline-flex items-center gap-1">Ext{AI_ICON} <Tip text="Extended flag (AI): the stock has already moved far from its base without consolidating. Extended = lower priority even with good IFP - prefer stocks just starting to move off a fresh base." /></span></th>
                <th className="pr-2"><span className="inline-flex items-center gap-1">BO / Stop{AI_ICON} <Tip text="Breakout and stop levels the AI read visually off the chart: breakout = top of the coil/base, stop = below the inside-bar low. Cross-checked against our computed levels in the popup (green tick = both methods agree within 2%)." /></span></th>
                <th className="pr-2"><span className="inline-flex items-center gap-1">Conf{AI_ICON} <Tip text="AI's own confidence in its verdict (0-1)." /></span></th>
                <th className="pr-2"><span className="inline-flex items-center gap-1">Verdict{AI_ICON} <Tip text="AI recommendation: SETUP_READY (2-3 strong criteria, near clean breakout, not extended) / EARLY_STAGE (base forming) / NOT_READY (weak or unreadable) / AVOID (distribution signs or broken base)." /></span></th>
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
                        <td className="pr-2 text-slate-300 capitalize" title={a.base_type === 'A' ? 'Type A — base after uptrend (strong move up, then consolidation base)' : a.base_type === 'B' ? 'Type B — accumulation after distribution (base at the bottom after downtrend/sideways)' : 'Market cycle phase (v2 result)'}>{a.base_type ? `Type ${a.base_type}` : (a.market_cycle_phase || '—')}</td>
                        <td className="pr-2">{a.ifp ? <IfpChips ifp={a.ifp} /> : <span className="text-slate-300">{top ? top.type.replace(/_/g, ' ') : (a.base_count ? `base ${a.base_count}` : '—')}</span>}</td>
                        <td className="pr-2">{a.extended ? <span className="text-amber-400" title="Extended from base — lower priority">⚠</span> : <span className="text-slate-600">—</span>}</td>
                        <td className="pr-2 text-slate-300 text-xs">{a.buy_point?.breakout_level ? `${a.buy_point.breakout_level} / ${a.buy_point.stop_level ?? '—'}` : '—'}</td>
                        <td className="pr-2 text-slate-300">{fmt(a.confidence)}</td>
                        <td className="pr-2">
                          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${REC_BADGE[a.recommendation] || REC_BADGE.NOT_READY}`}>
                            {a.recommendation || '—'}
                          </span>
                          {verified === 'mismatch' && <span title="AI levels differ from computed" className="ml-1 text-amber-400">⚠</span>}
                          {r.stage === 'sonnet_confirmed' && <span title={`Sonnet-confirmed (Haiku said ${r.haikuRec})`} className="ml-1 text-[10px] text-blue-300">S✓</span>}
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

      <div>
        <button onClick={togglePerf} className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1">
          {showPerf ? '\u25be' : '\u25b8'} AI performance (forward returns per engine and verdict)
          <Tip text="Outcome tracking: every AI analysis is scored nightly against what the stock actually did afterwards - 5/20/60-day forward returns from the analysis-date close, whether price hit the AI breakout level within 20 bars, and whether it hit the AI stop. Win rate = % of calls with positive 20-day return. This is your ground truth for judging Haiku vs Sonnet and whether SETUP_READY calls earn - numbers fill in as days pass after each analysis." />
        </button>
        {showPerf && perf && (
          <div className="overflow-x-auto mt-2">
            {perf.length === 0 ? (
              <div className="text-xs text-slate-500">No outcome data yet - returns fill in as trading days pass after each analysis.</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-500 uppercase tracking-wide">
                    <th className="py-1 pr-2">Engine</th><th className="pr-2">Verdict</th><th className="pr-2">N</th>
                    <th className="pr-2">Avg 5d %</th><th className="pr-2">Avg 20d %</th><th className="pr-2">Avg 60d %</th>
                    <th className="pr-2">Win 20d</th><th className="pr-2">BO hit</th><th className="pr-2">Stop hit</th><th className="pr-2">Feedback</th>
                  </tr>
                </thead>
                <tbody>
                  {perf.map((r, i) => (
                    <tr key={i} className="border-t border-slate-800 text-slate-300">
                      <td className="py-1 pr-2">{r.engine}</td>
                      <td className="pr-2">{r.recommendation}</td>
                      <td className="pr-2">{r.n}</td>
                      <td className="pr-2">{r.avg_ret_5d ?? '\u2014'}</td>
                      <td className="pr-2">{r.avg_ret_20d ?? '\u2014'}</td>
                      <td className="pr-2">{r.avg_ret_60d ?? '\u2014'}</td>
                      <td className="pr-2">{r.win_rate_20d != null ? r.win_rate_20d + '%' : '\u2014'}</td>
                      <td className="pr-2">{r.breakout_hit_pct != null ? r.breakout_hit_pct + '%' : '\u2014'}</td>
                      <td className="pr-2">{r.stop_hit_pct != null ? r.stop_hit_pct + '%' : '\u2014'}</td>
                      <td className="pr-2">{r.feedback_n > 0 ? `${r.feedback_correct}/${r.feedback_n} correct` : '\u2014'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      <AiResultModal result={picked} date={data?.indicatorDate} open={!!picked} onClose={() => setPicked(null)} />
    </div>
  );
}
