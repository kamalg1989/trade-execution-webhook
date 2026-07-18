import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, AlertCircle, ShoppingCart, BarChart3, RefreshCw, Loader, Database } from 'lucide-react';
import ChartModal from '../components/ChartModal';

export default function DashboardMobile() {
  const [recommendations, setRecommendations] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);
  const [loading, setLoading] = useState(true);
  const [chartOpen, setChartOpen] = useState(false);
  const [status, setStatus] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [updating, setUpdating] = useState(false);

  const refreshStatus = () => fetch('/api/data-status').then(r => r.ok && r.json()).then(d => d && setStatus(d)).catch(() => {});

  useEffect(() => {
    fetchRecommendations();
    refreshStatus();
  }, []);

  const runScan = async () => {
    if (!window.confirm('Run the screener now? Takes a few minutes.')) return;
    setScanning(true);
    try {
      const r = await fetch('/api/recommendations/refresh', { method: 'POST' });
      const d = await r.json();
      alert(d.message || 'Scan started');
    } catch { alert('Failed to start scan'); }
    setScanning(false);
  };

  const updateData = async () => {
    if (!window.confirm('Pull latest candles from Dhan into the DB? Takes ~1–2 min.')) return;
    setUpdating(true);
    try {
      const r = await fetch('/api/data/update', { method: 'POST' });
      const d = await r.json();
      alert(d.message || 'Data update started');
      const poll = setInterval(async () => {
        try {
          const s = await (await fetch('/api/data/update-status')).json();
          if (!s.updating) { clearInterval(poll); setUpdating(false); refreshStatus(); }
        } catch { clearInterval(poll); setUpdating(false); }
      }, 5000);
    } catch { alert('Failed to start data update'); setUpdating(false); }
  };

  const fetchRecommendations = async () => {
    try {
      const response = await fetch('/api/recommendations', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await response.json();
      setRecommendations(data.stocks || []);
      if (data.stocks?.length > 0) {
        selectStock(data.stocks[0]);
      }
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch recommendations:', error);
      setLoading(false);
    }
  };

  const selectStock = (stock) => {
    setSelectedStock(stock);
  };

  const handleBuy = async (stock) => {
    const qty = stock.recommendedQty || stock.qty || 1;
    const entry = stock.entry || stock.currentPrice;
    const sl = stock.stopLoss;
    if (!window.confirm(`Place a REAL Dhan BUY forever order?\n\n${stock.symbol}\nQty: ${qty}\nBuy above (trigger): ₹${entry}\n\nRests on Dhan, triggers when price crosses ₹${entry}. Set SL from SL tab after fill.`)) return;
    try {
      const apiKey = localStorage.getItem('trading_api_key');
      if (!apiKey || apiKey === 'undefined' || apiKey === 'null') {
        localStorage.removeItem('trading_api_key');
        alert('❌ API key not set on this device.\n\nGo to Settings → Trading Protection → Load API Key (enter your PIN) once, then retry.');
        return;
      }
      const response = await fetch('/api/buy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ symbol: stock.symbol, quantity: qty, price: entry, stopLoss: sl })
      });
      const result = await response.json();
      if (response.ok && result.success) {
        alert(`✅ ${result.message}\nOrder ID: ${result.orderId}`);
      } else if (response.status === 401 || response.status === 403) {
        localStorage.removeItem('trading_api_key');
        alert('❌ Stored API key is invalid — it has been cleared.\n\nGo to Settings → Trading Protection → Load API Key (enter your PIN), then retry.');
      } else {
        alert(`❌ Order failed: ${result.detail || result.error || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Order placement failed:', error);
      alert('❌ Order placement failed (network error)');
    }
  };

  if (loading) return <div className="p-4 text-center text-slate-400">Loading...</div>;

  const fmtD = (s) => { if (!s) return '—'; try { return new Date(s).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }); } catch { return s; } };

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen">
      {/* Status + actions */}
      {status && (
        <div className="px-4 pt-3 pb-2 border-b border-slate-800">
          <div className="text-[11px] text-slate-400 leading-tight mb-2">
            <div>📅 Signals: <b className="text-slate-200">{fmtD(status.signalBarDate)}</b> · 🗄️ DB: <b className="text-slate-200">{fmtD(status.dbLatestCandle)}</b>{status.regime && <> · Regime: <b className="text-purple-300">{status.regime}</b></>}</div>
          </div>
          <div className="flex gap-2">
            <button onClick={updateData} disabled={updating}
              className="flex-1 flex items-center justify-center gap-1.5 bg-emerald-600 active:bg-emerald-700 disabled:opacity-50 text-xs font-semibold px-3 py-2 rounded-lg">
              {updating ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
              {updating ? 'Updating DB' : 'Update Data'}
            </button>
            <button onClick={runScan} disabled={scanning || status.scanRunning}
              className="flex-1 flex items-center justify-center gap-1.5 bg-blue-600 active:bg-blue-700 disabled:opacity-50 text-xs font-semibold px-3 py-2 rounded-lg">
              {(scanning || status.scanRunning) ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {status.scanRunning ? 'Scanning' : 'Run Screener'}
            </button>
          </div>
        </div>
      )}

      {/* Recommendations List */}
      <div className="px-4 py-4">
        <h2 className="text-lg font-bold mb-3 flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-green-400" />
          Today's Picks
        </h2>

        <div className="space-y-2">
          {recommendations.length === 0 ? (
            <div className="text-center py-8">
              <AlertCircle className="w-8 h-8 mx-auto mb-2 text-slate-400 opacity-50" />
              <p className="text-slate-400 text-sm">No recommendations</p>
            </div>
          ) : (
            recommendations.map((stock) => (
              <button
                key={stock.symbol}
                onClick={() => selectStock(stock)}
                className={`w-full text-left p-3 rounded-lg transition-all flex items-center justify-between ${
                  selectedStock?.symbol === stock.symbol
                    ? 'bg-blue-600 border border-blue-400'
                    : 'bg-slate-700 border border-slate-600'
                }`}
              >
                <div className="flex-1">
                  <p className="font-bold text-base flex items-center gap-1.5 flex-wrap">
                    {stock.symbol}
                    {stock.heldQty > 0 && (
                      <span className="text-[9px] font-semibold bg-purple-700 text-purple-100 px-1.5 py-0.5 rounded">HOLDING {stock.heldQty}</span>
                    )}
                    {!stock.heldQty && stock.positionQty > 0 && (
                      <span className="text-[9px] font-semibold bg-purple-700 text-purple-100 px-1.5 py-0.5 rounded">TODAY {stock.positionQty}</span>
                    )}
                    {stock.hasForeverBuy && (
                      <span className="text-[9px] font-semibold bg-amber-700 text-amber-100 px-1.5 py-0.5 rounded">ORDER RESTING</span>
                    )}
                  </p>
                  <div className="flex gap-2 mt-1">
                    <span className="text-xs bg-green-700 px-2 py-0.5 rounded">T: ₹{stock.target}</span>
                    <span className="text-xs bg-red-700 px-2 py-0.5 rounded">SL: ₹{stock.stopLoss}</span>
                  </div>
                </div>
                <div className="text-right ml-2">
                  <p className="font-bold text-sm">₹{stock.currentPrice}</p>
                  <p className={`text-xs ${stock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {stock.change >= 0 ? '+' : ''}{stock.change.toFixed(2)}%
                  </p>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Selected Stock Details */}
      {selectedStock && (
        <div className="px-4 py-4 border-t border-slate-700">
          {/* Stock Header */}
          <div className="bg-slate-700 rounded-lg p-4 mb-3">
            <div className="flex justify-between items-start mb-3">
              <div>
                <h3 className="text-2xl font-bold">{selectedStock.symbol}</h3>
                <p className="text-slate-400 text-sm">{selectedStock.company}</p>
              </div>
              <div className="text-right">
                <p className="text-2xl font-bold">₹{selectedStock.currentPrice}</p>
                <p className={`text-sm font-semibold ${selectedStock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {selectedStock.change >= 0 ? '📈' : '📉'} {selectedStock.change >= 0 ? '+' : ''}{selectedStock.change.toFixed(2)}%
                </p>
              </div>
            </div>

            {/* Key Metrics */}
            <div className="grid grid-cols-2 gap-2 pt-3 border-t border-slate-600">
              <div>
                <p className="text-xs text-slate-400">Target</p>
                <p className="text-lg font-bold text-green-400">₹{selectedStock.target}</p>
              </div>
              <div>
                <p className="text-xs text-slate-400">Stop Loss</p>
                <p className="text-lg font-bold text-red-400">₹{selectedStock.stopLoss}</p>
              </div>
              <div>
                <p className="text-xs text-slate-400">Confidence</p>
                <p className="text-lg font-bold text-blue-400">{selectedStock.confidence}%</p>
              </div>
              <div>
                <p className="text-xs text-slate-400">Upside</p>
                <p className="text-lg font-bold text-purple-400">
                  {(((selectedStock.target - selectedStock.currentPrice) / selectedStock.currentPrice) * 100).toFixed(1)}%
                </p>
              </div>
            </div>

            <p className="mt-3 text-xs text-slate-300">
              <strong>Reason:</strong> {selectedStock.reason}
            </p>

            {/* Full screener detail */}
            {selectedStock.entryType && (
              <div className="mt-3 pt-3 border-t border-slate-600">
                <div className="flex flex-wrap gap-1.5 mb-2 text-[10px]">
                  {selectedStock.regime && <span className="px-2 py-0.5 rounded bg-purple-900/60 text-purple-300">🌍 {selectedStock.regime}</span>}
                  <span className="px-2 py-0.5 rounded bg-blue-900/60 text-blue-300">🎯 {selectedStock.entryType}</span>
                  {selectedStock.signalBarDate && <span className="px-2 py-0.5 rounded bg-slate-600 text-slate-200">📅 {selectedStock.signalBarDate}</span>}
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                  <D label="Entry (buy above)" v={`₹${selectedStock.entry ?? '—'}`} />
                  <D label="Target" v={`₹${selectedStock.target}`} />
                  <D label="Qty" v={`${selectedStock.recommendedQty}${selectedStock.baseStage != null ? ` (st${selectedStock.baseStage} x${selectedStock.stageMultiplier ?? 1})` : ''}`} />
                  <D label="Risk/Share" v={selectedStock.riskPerShare != null ? `₹${selectedStock.riskPerShare}` : '—'} />
                  <D label="R:R" v={selectedStock.rrRatio != null ? `1:${selectedStock.rrRatio}` : '—'} />
                  <D label="Tick" v={selectedStock.tickSize != null ? `₹${selectedStock.tickSize}` : '—'} />
                  <D label="Base Quality" v={selectedStock.baseQuality != null ? selectedStock.baseQuality.toFixed(2) : '—'} />
                  <D label="Liquidity" v={selectedStock.liquidityCr != null ? `₹${selectedStock.liquidityCr}cr` : '—'} />
                  <D label="IFP" v={selectedStock.ifp ?? '—'} />
                  <D label="Base Range" v={selectedStock.baseRangePct != null ? `${selectedStock.baseRangePct}%` : '—'} />
                </div>
              </div>
            )}
          </div>

          {/* View Chart button — opens fullscreen chart modal */}
          <button
            onClick={() => setChartOpen(true)}
            className="w-full bg-slate-700 hover:bg-slate-600 active:bg-slate-600 rounded-lg py-3 mb-3 flex items-center justify-center gap-2 font-semibold text-blue-400 border border-slate-600"
          >
            <BarChart3 className="w-5 h-5" />
            View Chart (Daily / Weekly)
          </button>

          {/* Buy Button */}
          {selectedStock.owned ? (
            <div className="w-full bg-slate-700 border border-purple-600/50 text-purple-200 font-semibold py-3 px-4 rounded-lg text-center text-sm">
              {selectedStock.heldQty > 0
                ? `Already holding ${selectedStock.heldQty} shares — manage from SL tab`
                : selectedStock.positionQty > 0
                  ? `Bought today (${selectedStock.positionQty}) — manage from SL tab`
                  : 'BUY forever order already resting on Dhan'}
            </div>
          ) : (
            <button
              onClick={() => handleBuy(selectedStock)}
              className="w-full bg-gradient-to-r from-green-500 to-green-600 hover:from-green-600 hover:to-green-700 text-white font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-all"
            >
              <ShoppingCart className="w-5 h-5" />
              Buy {selectedStock.symbol}
            </button>
          )}
        </div>
      )}

      <ChartModal
        symbol={selectedStock?.symbol}
        open={chartOpen}
        onClose={() => setChartOpen(false)}
      />
    </div>
  );
}

function D({ label, v }) {
  return (
    <div>
      <p className="text-slate-400 text-[9px]">{label}</p>
      <p className="font-semibold text-white">{v}</p>
    </div>
  );
}
