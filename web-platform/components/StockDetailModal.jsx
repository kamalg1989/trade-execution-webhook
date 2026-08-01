import React from 'react';
import { X, BarChart3, ShoppingCart } from 'lucide-react';

function D({ label, v }) {
  return (
    <div>
      <p className="text-slate-400 text-[9px]">{label}</p>
      <p className="font-semibold text-white">{v}</p>
    </div>
  );
}

const riskRewardPct = (s) => {
  const e = s.entry || s.currentPrice, sl = s.stopLoss, t = s.target;
  if (!e || !sl || !t || sl >= e || t <= e) return null;
  return { risk: ((e - sl) / e * 100).toFixed(1), reward: ((t - e) / e * 100).toFixed(1) };
};
const fmtReason = (s) => s.reason ? s.reason.replace(/\s*\|\s*R:R\s*1:[\d.]+/i, '').replace(/R:R\s*1:[\d.]+\s*\|\s*/i, '') : s.reason;
const allocation = (s) => {
  const e = s.entry || s.currentPrice, q = s.recommendedQty || 0;
  return e && q ? Math.round(e * q) : null;
};

// Fullscreen detail sheet — sits below ChartModal (z-40 < z-50) so tapping
// "View Chart" can layer the chart on top without losing this modal's state.
export default function StockDetailModal({ stock, open, onClose, onBuy, onViewChart }) {
  if (!open || !stock) return null;

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-slate-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 bg-slate-800 flex-shrink-0">
        <div>
          <h3 className="text-xl font-bold text-white leading-tight">{stock.symbol}</h3>
          <p className="text-slate-400 text-xs">{stock.company}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-lg font-bold text-white">₹{stock.currentPrice}</p>
            <p className={`text-xs font-semibold ${stock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {stock.change >= 0 ? '+' : ''}{stock.change?.toFixed(2)}%
            </p>
          </div>
          <button onClick={onClose} className="p-2 text-slate-300 hover:text-white bg-slate-700 rounded-lg flex-shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="bg-slate-700 rounded-lg p-4">
          {/* Key Metrics */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <p className="text-xs text-slate-400">Target</p>
              <p className="text-lg font-bold text-green-400">₹{stock.target}</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Stop Loss</p>
              <p className="text-lg font-bold text-red-400">₹{stock.stopLoss}</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Confidence</p>
              <p className="text-lg font-bold text-blue-400">{stock.confidence}%</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Upside</p>
              <p className="text-lg font-bold text-purple-400">
                {(((stock.target - stock.currentPrice) / stock.currentPrice) * 100).toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Allocation</p>
              <p className="text-lg font-bold text-amber-300">
                {allocation(stock) != null ? `₹${allocation(stock).toLocaleString('en-IN')}` : '—'}
              </p>
              <p className="text-[9px] text-slate-500">{stock.recommendedQty} × ₹{stock.entry ?? stock.currentPrice}</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Risk:Reward</p>
              <p className="text-lg font-bold text-blue-300">
                {(() => { const rr = riskRewardPct(stock); return rr ? `${rr.risk}%:${rr.reward}%` : (stock.rrRatio != null ? `1:${stock.rrRatio}` : '—'); })()}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Total Risk</p>
              <p className="text-lg font-bold text-red-300">
                {stock.riskPerShare != null && stock.recommendedQty
                  ? `₹${Math.round(stock.riskPerShare * stock.recommendedQty).toLocaleString('en-IN')}`
                  : '—'}
              </p>
              <p className="text-[9px] text-slate-500">if SL hits</p>
            </div>
          </div>

          <p className="mt-3 text-xs text-slate-300">
            <strong>Reason:</strong> {fmtReason(stock)}
          </p>

          {/* AI chart analysis (Gemini v3) */}
          {stock.aiRatings && (
            <div className="mt-3 bg-emerald-900/15 border border-emerald-800/40 rounded-lg p-2.5">
              <p className="text-[10px] font-bold tracking-widest text-emerald-300 mb-1.5">
                🤖 AI CHART ANALYSIS <span className="text-slate-500 font-normal">(rank #{stock.aiRank ?? '—'})</span>
              </p>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                <D label="Volume Pattern" v={stock.aiRatings.volumePattern ?? '—'} />
                <D label="Base Structure" v={stock.aiRatings.baseStructure ?? '—'} />
                <D label="Pullback Depth" v={stock.aiRatings.pullbackDepth ?? '—'} />
                <D label="Base Type" v={stock.aiBaseType ?? '—'} />
                <D label="Extended?" v={stock.aiExtended ? '⚠️ Yes' : 'No'} />
                <D label="AI Reco" v={stock.aiRecommendation ?? '—'} />
                <D label="AI Confidence" v={stock.aiConfidence != null ? `${Math.round(stock.aiConfidence * 100)}%` : '—'} />
                <D label="Quant IFP" v={stock.ifp ?? '—'} />
              </div>
              {stock.aiVerdict && (
                <p className="text-[10px] text-emerald-200/90 mt-1.5 italic">"{stock.aiVerdict}"</p>
              )}
            </div>
          )}

          {/* Full screener detail */}
          {stock.entryType && (
            <div className="mt-3 pt-3 border-t border-slate-600">
              <div className="flex flex-wrap gap-1.5 mb-2 text-[10px]">
                {stock.regime && <span className="px-2 py-0.5 rounded bg-purple-900/60 text-purple-300">🌍 {stock.regime}</span>}
                <span className="px-2 py-0.5 rounded bg-blue-900/60 text-blue-300">🎯 {stock.entryType}</span>
                {stock.signalBarDate && <span className="px-2 py-0.5 rounded bg-slate-600 text-slate-200">📅 {stock.signalBarDate}</span>}
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                <D label="Entry (buy above)" v={`₹${stock.entry ?? '—'}`} />
                <D label="Target" v={`₹${stock.target}`} />
                <D label="Qty" v={`${stock.recommendedQty}${stock.baseStage != null ? ` (st${stock.baseStage} x${stock.stageMultiplier ?? 1})` : ''}`} />
                <D label="Risk/Share" v={stock.riskPerShare != null ? `₹${stock.riskPerShare}` : '—'} />
                <D label="Tick" v={stock.tickSize != null ? `₹${stock.tickSize}` : '—'} />
                <D label="Base Quality" v={stock.baseQuality != null ? stock.baseQuality.toFixed(2) : '—'} />
                <D label="Liquidity" v={stock.liquidityCr != null ? `₹${stock.liquidityCr}cr` : '—'} />
                <D label="IFP" v={stock.ifp ?? '—'} />
                <D label="Base Range" v={stock.baseRangePct != null ? `${stock.baseRangePct}%` : '—'} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Sticky footer actions */}
      <div className="flex-shrink-0 px-4 py-3 border-t border-slate-700 bg-slate-800 space-y-2">
        <button
          onClick={onViewChart}
          className="w-full bg-slate-700 hover:bg-slate-600 active:bg-slate-600 rounded-lg py-3 flex items-center justify-center gap-2 font-semibold text-blue-400 border border-slate-600"
        >
          <BarChart3 className="w-5 h-5" />
          View Chart (Daily / Weekly)
        </button>

        {stock.owned ? (
          <div className="w-full bg-slate-700 border border-purple-600/50 text-purple-200 font-semibold py-3 px-4 rounded-lg text-center text-sm">
            {stock.heldQty > 0
              ? `Already holding ${stock.heldQty} shares — manage from Today tab`
              : stock.positionQty > 0
                ? `Bought today (${stock.positionQty}) — manage from Today tab`
                : 'BUY forever order already resting on Dhan'}
          </div>
        ) : (
          <button
            onClick={() => onBuy(stock)}
            className="w-full bg-gradient-to-r from-green-500 to-green-600 hover:from-green-600 hover:to-green-700 text-white font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-all"
          >
            <ShoppingCart className="w-5 h-5" />
            Buy {stock.symbol}
          </button>
        )}
      </div>
    </div>
  );
}
