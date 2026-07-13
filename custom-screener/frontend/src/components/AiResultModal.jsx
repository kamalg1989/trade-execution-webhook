import React, { useState } from 'react';
import { aiChartSrc, aiFeedback } from '../api/client.js';

// Popup per approved wireframe: annotated daily/weekly chart tabs + full AI
// report (phase, base, patterns, IFP verdict, levels vs computed, thesis,
// feedback). Renders over the screener page; close returns to the list.

const REC_STYLE = {
  SETUP_READY: 'bg-emerald-900/60 text-emerald-300 border-emerald-700',
  EARLY_STAGE: 'bg-amber-900/60 text-amber-300 border-amber-700',
  NOT_READY: 'bg-slate-800 text-slate-300 border-slate-600',
  AVOID: 'bg-red-900/60 text-red-300 border-red-700',
};

const fmt = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));

function LevelRow({ label, check }) {
  if (!check) return null;
  const ok = check.status === 'verified';
  return (
    <tr className="border-t border-slate-800">
      <td className="py-1.5 pr-2 text-slate-400">{label}</td>
      <td className="py-1.5 text-right text-slate-200">
        {fmt(check.ai)} / {fmt(check.computed)}{' '}
        {check.status === 'no_computed' ? null : ok ? (
          <span className="text-emerald-400 text-xs">✓ within {check.deviation_pct ?? 0}%</span>
        ) : check.status === 'ai_missing' ? (
          <span className="text-slate-500 text-xs">AI gave no level</span>
        ) : (
          <span className="text-amber-400 text-xs">⚠ off by {check.deviation_pct}%</span>
        )}
      </td>
    </tr>
  );
}

export default function AiResultModal({ result, date, open, onClose }) {
  const [tab, setTab] = useState('daily');
  const [fb, setFb] = useState(null);
  const [fbMsg, setFbMsg] = useState('');

  if (!open || !result) return null;
  const a = result.analysis || {};
  const v = result.verification || {};
  const bp = a.buy_point || {};
  const charts = result.charts || {};
  const img = tab === 'daily' ? charts.daily_annotated || charts.daily
    : charts.weekly_annotated || charts.weekly;
  const risk = bp.breakout_level && bp.stop_level ? bp.breakout_level - bp.stop_level : null;

  const sendFeedback = async (val) => {
    setFb(val);
    try {
      await aiFeedback({ symbol: result.symbol, analysisDate: date, feedback: val });
      setFbMsg('Feedback saved');
    } catch (e) {
      setFbMsg(`Feedback: ${e.message}`);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-start sm:items-center justify-center overflow-y-auto p-2 sm:p-6"
      onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl my-4"
        onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
          <div>
            <span className="text-lg font-bold text-white">{result.symbol}</span>
            <span className="text-sm text-slate-400 ml-2">
              ₹{fmt(result.close)} · {date}{result.fromStore ? ' · cached' : ''}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${REC_STYLE[a.recommendation] || REC_STYLE.NOT_READY}`}>
              {a.recommendation || '—'} · {fmt(a.confidence)}
            </span>
            <button onClick={onClose}
              className="p-1.5 text-slate-300 hover:text-white bg-slate-800 rounded-lg leading-none">✕</button>
          </div>
        </div>

        <div className="p-4 space-y-3">
          {/* Chart tabs */}
          <div className="flex gap-2">
            {['daily', 'weekly'].map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-4 py-1 rounded-md text-sm font-semibold capitalize ${tab === t ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400'}`}>
                {t}
              </button>
            ))}
          </div>
          <div className="bg-slate-950 rounded-lg p-1 border border-slate-800">
            {img ? (
              <img src={aiChartSrc(img)} alt={`${result.symbol} ${tab} annotated chart`}
                className="w-full h-auto rounded" />
            ) : (
              <div className="text-slate-500 text-sm p-6 text-center">Chart unavailable</div>
            )}
            <p className="text-[11px] text-slate-500 px-2 py-1">
              Dashed lines: green = AI breakout, red = AI stop, gray = computed support
            </p>
          </div>

          {/* Metric row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
            {[['Phase', a.market_cycle_phase], ['Base count', a.base_count && `Base ${a.base_count}`],
              ['Base quality', a.base_quality], ['Buy point', bp.type]].map(([k, val]) => (
              <div key={k} className="bg-slate-800/70 rounded-lg px-2 py-2">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">{k}</div>
                <div className="text-sm font-semibold text-slate-100 capitalize">{(val || '—').toString().replace(/_/g, ' ')}</div>
              </div>
            ))}
          </div>

          {/* IFP verdict */}
          <div className="border border-slate-700 rounded-lg px-3 py-2">
            <div className="text-xs font-semibold text-slate-400 mb-1">
              IFP verdict — {a.ifp_verdict?.present ? 'footprint present' : 'no footprint'} ({fmt(a.ifp_verdict?.confidence)})
              <span className="ml-2 text-slate-500 font-normal">computed score {fmt(result.ifpScore)}</span>
            </div>
            <p className="text-sm text-slate-200 leading-relaxed">{a.ifp_verdict?.evidence || '—'}</p>
          </div>

          {/* Patterns */}
          {(a.patterns || []).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {a.patterns.map((p, i) => (
                <span key={i} title={p.description}
                  className="bg-purple-900/50 text-purple-300 border border-purple-800 text-xs px-2.5 py-1 rounded-full">
                  {p.type.replace(/_/g, ' ')} · {fmt(p.confidence)} · {p.timeframe}
                </span>
              ))}
            </div>
          )}

          {/* Levels vs computed */}
          <table className="w-full text-sm">
            <tbody>
              <LevelRow label="Breakout (AI / computed)" check={v.breakout} />
              <LevelRow label="Stop loss (AI / computed)" check={v.stop} />
              {risk != null && bp.breakout_level ? (
                <tr className="border-t border-slate-800">
                  <td className="py-1.5 pr-2 text-slate-400">Risk per share</td>
                  <td className="py-1.5 text-right text-slate-200">
                    ₹{fmt(risk)} ({fmt((risk / bp.breakout_level) * 100, 1)}%)
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>

          {/* Base quality reasons + weekly context + thesis */}
          {(a.base_quality_reasons || []).length > 0 && (
            <p className="text-xs text-slate-400">
              Base quality: {a.base_quality_reasons.join(' · ')}
            </p>
          )}
          {a.weekly_context && (
            <p className="text-xs text-slate-400">Weekly: {a.weekly_context}</p>
          )}
          <p className="text-sm text-slate-200 leading-relaxed border-t border-slate-800 pt-3">
            <span className="font-semibold text-slate-100">AI thesis: </span>{a.thesis || '—'}
          </p>

          {/* Feedback */}
          <div className="flex items-center gap-2 border-t border-slate-800 pt-3">
            {['CORRECT', 'PARTIAL', 'WRONG'].map((val) => (
              <button key={val} onClick={() => sendFeedback(val)}
                className={`px-3 py-1.5 text-xs rounded-lg border ${fb === val ? 'bg-blue-600 border-blue-500 text-white' : 'bg-slate-800 border-slate-600 text-slate-300 hover:text-white'}`}>
                {val === 'CORRECT' ? '👍 Correct' : val === 'WRONG' ? '👎 Wrong' : 'Partial'}
              </button>
            ))}
            <span className="text-xs text-slate-500 ml-1">{fbMsg}</span>
            <a className="ml-auto text-xs text-blue-400 hover:text-blue-300"
              href={`https://www.tradingview.com/chart/?symbol=NSE:${result.symbol}`}
              target="_blank" rel="noreferrer">TradingView ↗</a>
          </div>
        </div>
      </div>
    </div>
  );
}
