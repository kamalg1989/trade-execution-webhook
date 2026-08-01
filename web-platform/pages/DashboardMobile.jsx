import React, { useState, useEffect } from 'react';
import { TrendingUp, AlertCircle, RefreshCw, Loader, Database, ChevronRight } from 'lucide-react';
import ChartModal from '../components/ChartModal';
import StockDetailModal from '../components/StockDetailModal';

export default function DashboardMobile() {
  const [recommendations, setRecommendations] = useState([]);
  const [aiPicks, setAiPicks] = useState([]);
  const [aiStatus, setAiStatus] = useState(null);
  const [selectedStock, setSelectedStock] = useState(null);
  const [loading, setLoading] = useState(true);
  const [detailOpen, setDetailOpen] = useState(false);
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
    if (!window.confirm('Run the screener now? Scans all NSE stocks — takes several minutes.')) return;
    setScanning(true);
    try {
      const r = await fetch('/api/recommendations/refresh', { method: 'POST' });
      const d = await r.json();
      alert(d.message || 'Scan started');
    } catch {
      alert('Failed to start scan');
      setScanning(false);
      return;
    }
    // Poll until the scan finishes, then reload picks automatically
    let polls = 0;
    const poll = setInterval(async () => {
      polls += 1;
      try {
        const s = await (await fetch('/api/data-status')).json();
        if (!s.scanRunning || polls > 60) {
          clearInterval(poll);
          setScanning(false);
          await fetchRecommendations();
          refreshStatus();
        }
      } catch {
        clearInterval(poll);
        setScanning(false);
      }
    }, 15000);
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
      setAiPicks(data.aiPicks || []);
      setAiStatus(data.aiStatus || null);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch recommendations:', error);
      setLoading(false);
    }
  };

  // Tapping a row opens its details straight away — no scrolling to a
  // shared panel at the bottom of the page.
  const openDetail = (stock) => {
    setSelectedStock(stock);
    setDetailOpen(true);
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
        body: JSON.stringify({ symbol: stock.symbol, quantity: qty, price: entry, stopLoss: sl, recommendation: stock })
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

        <p className="text-[10px] font-bold tracking-widest text-blue-300 mb-2">📐 QUANT PICKS</p>
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
                onClick={() => openDetail(stock)}
                className="w-full text-left p-3 rounded-lg transition-all flex items-center justify-between bg-slate-700 border border-slate-600 active:bg-slate-600"
              >
                <div className="flex-1 min-w-0">
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
                <div className="text-right ml-2 flex items-center gap-1.5 flex-shrink-0">
                  <div>
                    <p className="font-bold text-sm">₹{stock.currentPrice}</p>
                    <p className={`text-xs ${stock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {stock.change >= 0 ? '+' : ''}{stock.change.toFixed(2)}%
                    </p>
                  </div>
                  <ChevronRight className="w-4 h-4 text-slate-500" />
                </div>
              </button>
            ))
          )}
        </div>

        <p className="text-[10px] font-bold tracking-widest text-emerald-300 mt-4 mb-2">🤖 AI CHART PICKS</p>
        <div className="space-y-2">
          {aiPicks.length > 0 ? (
            aiPicks.map((stock) => (
              <button
                key={`ai-${stock.symbol}`}
                onClick={() => openDetail(stock)}
                className="w-full text-left p-3 rounded-lg transition-all bg-slate-700 border border-emerald-800/60 active:bg-slate-600"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <p className="font-bold text-base flex items-center gap-1.5 flex-wrap">
                      {stock.symbol}
                      {stock.alsoQuantPick && (
                        <span className="text-[9px] font-semibold bg-emerald-700 text-emerald-100 px-1.5 py-0.5 rounded">QUANT + AI</span>
                      )}
                      {stock.heldQty > 0 && (
                        <span className="text-[9px] font-semibold bg-purple-700 text-purple-100 px-1.5 py-0.5 rounded">HOLDING {stock.heldQty}</span>
                      )}
                    </p>
                    <div className="flex gap-2 mt-1">
                      <span className="text-xs bg-green-700 px-2 py-0.5 rounded">T: ₹{stock.target}</span>
                      <span className="text-xs bg-red-700 px-2 py-0.5 rounded">SL: ₹{stock.stopLoss}</span>
                    </div>
                  </div>
                  <div className="text-right ml-2 flex items-center gap-1.5 flex-shrink-0">
                    <div>
                      <p className="font-bold text-sm">₹{stock.currentPrice}</p>
                      <p className={`text-xs ${stock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {stock.change >= 0 ? '+' : ''}{stock.change?.toFixed(2)}%
                      </p>
                    </div>
                    <ChevronRight className="w-4 h-4 text-slate-500" />
                  </div>
                </div>
                {stock.aiRatings && (
                  <p className="text-[9px] text-emerald-200 mt-1.5">
                    Vol: {stock.aiRatings.volumePattern} · Base: {stock.aiRatings.baseStructure} · PB: {stock.aiRatings.pullbackDepth}
                    {stock.aiExtended ? ' · ⚠️ extended' : ''}
                  </p>
                )}
              </button>
            ))
          ) : (
            <p className="text-[11px] text-slate-500 py-1">
              {aiStatus === 'pending' ? '⏳ AI analysis running…' : aiStatus === 'error' ? '⚠️ AI analysis unavailable' : 'AI picks appear after the next scan.'}
            </p>
          )}
        </div>
      </div>

      <StockDetailModal
        stock={selectedStock}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        onBuy={handleBuy}
        onViewChart={() => setChartOpen(true)}
      />

      <ChartModal
        symbol={selectedStock?.symbol}
        open={chartOpen}
        onClose={() => setChartOpen(false)}
      />
    </div>
  );
}
