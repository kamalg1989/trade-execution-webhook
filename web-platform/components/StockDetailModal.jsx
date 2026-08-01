import React, { useRef } from 'react';
import { X, BarChart3, ShoppingCart } from 'lucide-react';
import { useSwipeToClose } from '../utils/useSwipeToClose';

function D({ label, v }) {
  return (
    <div className="min-w-0">
      <p className="text-slate-400 text-[9px] leading-tight truncate">{label}</p>
      <p className="font-semibold text-white text-xs leading-tight truncate">{v}</p>
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
  const scrollRef = useRef(null);
  // Bound to the whole modal (not just the header) so you can swipe up
  // to close from anywhere - it only "arms" the close-drag when the
  // content is already scrolled to the bottom, so it never fights normal
  // scrolling further up the sheet.
  const { handlers, panelStyle } = useSwipeToClose(onClose, 90, scrollRef);

  if (!open || !stock) return null;

  return (
    <div {...handlers} className="fixed inset-0 z-40 flex flex-col bg-slate-900" style={panelStyle}>
      {/* Header — swipe up anywhere on this screen to close, or tap the X.
          The drag-handle pill itself lives at the bottom (see below), next
          to the action buttons, matching the swipe-up gesture direction. */}
      <div className="flex-shrink-0 border-b border-slate-700 bg-slate-800">
        <div className="flex items-center justify-between px-4 pt-4 pb-2.5">
          <div className="min-w-0">
            <h3 className="text-lg font-bold text-white leading-tight truncate">{stock.symbol}</h3>
            <p className="text-slate-400 text-xs truncate">{stock.company}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="text-right">
              <p className="text-base font-bold text-white">₹{stock.currentPrice}</p>
              <p className={`text-xs font-semibold ${stock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {stock.change >= 0 ? '+' : ''}{stock.change?.toFixed(2)}%
              </p>
            </div>
            <button onClick={onClose} className="p-2 text-slate-300 hover:text-white bg-slate-700 rounded-lg flex-shrink-0">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Scrollable content — footer buttons live inside this same scroll
          region (sticky to its bottom) so they sit right after the content
          instead of being pinned to the physical screen edge with a dead
          gap above them when content is short. */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0" style={{ overscrollBehaviorY: 'contain' }}>
        <div className="bg-slate-700 rounded-lg p-3 mx-4 mt-3">
          {/* Key Metrics */}
          <div className="grid grid-cols-3 gap-x-3 gap-y-2.5">
            <div>
              <p className="text-[10px] text-slate-400">Target</p>
              <p className="text-base font-bold text-green-400">₹{stock.target}</p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Stop Loss</p>
              <p className="text-base font-bold text-red-400">₹{stock.stopLoss}</p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Confidence</p>
              <p className="text-base font-bold text-blue-400">{stock.confidence}%</p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Upside</p>
              <p className="text-base font-bold text-purple-400">
                {(((stock.target - stock.currentPrice) / stock.currentPrice) * 100).toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Risk:Reward</p>
              <p className="text-base font-bold text-blue-300">
                {(() => { const rr = riskRewardPct(stock); return rr ? `${rr.risk}:${rr.reward}` : (stock.rrRatio != null ? `1:${stock.rrRatio}` : '—'); })()}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Total Risk</p>
              <p className="text-base font-bold text-red-300">
                {stock.riskPerShare != null && stock.recommendedQty
                  ? `₹${Math.round(stock.riskPerShare * stock.recommendedQty).toLocaleString('en-IN')}`
                  : '—'}
              </p>
            </div>
            <div className="col-span-3 pt-1 border-t border-slate-600">
              <p className="text-[10px] text-slate-400">Allocation</p>
              <p className="text-base font-bold text-amber-300">
                {allocation(stock) != null ? `₹${allocation(stock).toLocaleString('en-IN')}` : '—'}
                <span className="text-[11px] text-slate-500 font-normal ml-1.5">({stock.recommendedQty} × ₹{stock.entry ?? stock.currentPrice})</span>
              </p>
            </div>
          </div>

          <p className="mt-2.5 text-xs text-slate-300 leading-snug">
            <strong>Reason:</strong> {fmtReason(stock)}
          </p>

          {/* AI chart analysis (Gemini v3) */}
          {stock.aiRatings && (
            <div className="mt-2.5 bg-emerald-900/15 border border-emerald-800/40 rounded-lg p-2.5 overflow-hidden">
              <p className="text-[10px] font-bold tracking-widest text-emerald-300 mb-1.5">
                🤖 AI ANALYSIS <span className="text-slate-500 font-normal">(#{stock.aiRank ?? '—'})</span>
              </p>
              <div className="grid grid-cols-3 gap-x-2 gap-y-1.5">
                <D label="Volume" v={stock.aiRatings.volumePattern ?? '—'} />
                <D label="Base" v={stock.aiRatings.baseStructure ?? '—'} />
                <D label="Pullback" v={stock.aiRatings.pullbackDepth ?? '—'} />
                <D label="Base Type" v={stock.aiBaseType ?? '—'} />
                <D label="Extended?" v={stock.aiExtended ? '⚠️ Yes' : 'No'} />
                <D label="AI Reco" v={stock.aiRecommendation ?? '—'} />
              </div>
              {stock.aiVerdict && (
                <p className="text-[11px] text-emerald-300 mt-1.5 italic leading-snug">"{stock.aiVerdict}"</p>
              )}
            </div>
          )}

          {/* Full screener detail */}
          {stock.entryType && (
            <div className="mt-2.5 pt-2.5 border-t border-slate-600">
              <div className="flex flex-wrap gap-1 mb-2 text-[10px]">
                {stock.regime && <span className="px-1.5 py-0.5 rounded bg-purple-900/60 text-purple-300">🌍 {stock.regime}</span>}
                <span className="px-1.5 py-0.5 rounded bg-blue-900/60 text-blue-300">🎯 {stock.entryType}</span>
                {stock.signalBarDate && <span className="px-1.5 py-0.5 rounded bg-slate-600 text-slate-200">📅 {stock.signalBarDate}</span>}
              </div>
              <div className="grid grid-cols-3 gap-x-2 gap-y-1.5">
                <D label="Entry" v={`₹${stock.entry ?? '—'}`} />
                <D label="Qty" v={`${stock.recommendedQty}${stock.baseStage != null ? ` (st${stock.baseStage})` : ''}`} />
                <D label="Risk/Sh" v={stock.riskPerShare != null ? `₹${stock.riskPerShare}` : '—'} />
                <D label="Base Qual" v={stock.baseQuality != null ? stock.baseQuality.toFixed(2) : '—'} />
                <D label="Liquidity" v={stock.liquidityCr != null ? `₹${stock.liquidityCr}cr` : '—'} />
                <D label="IFP" v={stock.ifp ?? '—'} />
              </div>
            </div>
          )}
        </div>

        {/* Action buttons — sticky to the bottom of the scroll area, so they
            sit right after the content (no dead gap for short content) but
            still stay pinned in view once you scroll a taller sheet.
            Buy is last (closest to the natural right-thumb rest position)
            since it's the primary action. */}
        <div className="sticky bottom-0 mt-3 px-4 pb-2.5 border-t border-slate-700 bg-slate-800 space-y-2">
          <div className="flex justify-center pt-2 pb-1">
            <div className="w-24 h-2 rounded-full bg-slate-500" />
          </div>
          <button
            onClick={onViewChart}
            className="w-full bg-slate-700 active:bg-slate-600 rounded-lg py-3 flex items-center justify-center gap-2 font-semibold text-base text-blue-400 border border-slate-600"
          >
            <BarChart3 className="w-5 h-5" />
            View Chart
          </button>

          {stock.owned ? (
            <div className="w-full bg-slate-700 border border-purple-600/50 text-purple-200 font-semibold py-3 px-4 rounded-lg text-center text-sm">
              {stock.heldQty > 0
                ? `Holding ${stock.heldQty} — manage from Today tab`
                : stock.positionQty > 0
                  ? `Bought today (${stock.positionQty}) — manage from Today tab`
                  : 'BUY order already resting on Dhan'}
            </div>
          ) : (
            <button
              onClick={() => onBuy(stock)}
              className="w-full bg-gradient-to-r from-green-500 to-green-600 active:from-green-600 active:to-green-700 text-white font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-all text-base"
            >
              <ShoppingCart className="w-5 h-5" />
              Buy {stock.symbol}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
