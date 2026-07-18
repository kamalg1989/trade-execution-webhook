import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, AlertCircle, ShoppingCart, Maximize2, RefreshCw, Loader, Calendar, Database } from 'lucide-react';
import ChartModal, { makeResponsive } from '../components/ChartModal';

export default function Dashboard() {
  const [recommendations, setRecommendations] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [chartType, setChartType] = useState('daily'); // 'daily' | 'weekly'
  const [chartOpen, setChartOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [updating, setUpdating] = useState(false);

  useEffect(() => {
    fetchRecommendations();
    fetchStatus();
  }, []);

  const fetchStatus = async () => {
    try {
      const r = await fetch('/api/data-status');
      if (r.ok) setStatus(await r.json());
    } catch { /* ignore */ }
  };

  const updateData = async () => {
    if (!window.confirm('Pull the latest daily candles from Dhan into the database?\n\nThis takes ~1–2 minutes. Do it before running the screener so it uses fresh data.')) return;
    setUpdating(true);
    try {
      const r = await fetch('/api/data/update', { method: 'POST' });
      const d = await r.json();
      alert(d.message || 'Data update started');
      // Poll until the updater finishes, then refresh the DB date
      const poll = setInterval(async () => {
        try {
          const s = await (await fetch('/api/data/update-status')).json();
          if (!s.updating) { clearInterval(poll); setUpdating(false); fetchStatus(); }
        } catch { clearInterval(poll); setUpdating(false); }
      }, 5000);
    } catch {
      alert('Failed to start data update');
      setUpdating(false);
    }
  };

  const runScan = async () => {
    if (!window.confirm('Run the screener now? This scans all NSE stocks (EQ series, ~2000) and may take several minutes.')) return;
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
    // Poll until the background scan finishes, then reload picks automatically
    let polls = 0;
    const poll = setInterval(async () => {
      polls += 1;
      try {
        const s = await (await fetch('/api/data-status')).json();
        if (!s.scanRunning || polls > 60) {   // 60 × 15s = 15 min safety cap
          clearInterval(poll);
          setScanning(false);
          await fetchRecommendations();
          fetchStatus();
        }
      } catch {
        clearInterval(poll);
        setScanning(false);
      }
    }, 15000);
  };

  const fmtDate = (s) => {
    if (!s) return '—';
    try { return new Date(s).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }); }
    catch { return s; }
  };

  const fetchRecommendations = async () => {
    try {
      // Try real endpoint first, fallback to mock
      let response = await fetch('/api/recommendations', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' }
      });

      if (!response.ok) {
        console.log('Using mock data (real API not available)');
        response = await fetch('/api/recommendations-mock', {
          method: 'GET',
          headers: { 'Content-Type': 'application/json' }
        });
      }

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

  const loadChart = async (stock, type) => {
    if (!stock) return;
    setChartData(null);
    try {
      const chartTheme = localStorage.getItem('theme') === 'light' ? 'light' : 'dark';
      const response = await fetch(`/api/charts/${type}?symbol=${encodeURIComponent(stock.symbol)}&theme=${chartTheme}`);
      setChartData(response.ok ? makeResponsive(await response.text()) : '');
    } catch (error) {
      console.error('Failed to fetch chart:', error);
      setChartData('');
    }
  };

  const selectStock = (stock) => {
    setSelectedStock(stock);
    loadChart(stock, chartType);
  };

  const switchChartType = (type) => {
    setChartType(type);
    loadChart(selectedStock, type);
  };

  const handleBuy = async (stock) => {
    const qty = stock.recommendedQty || stock.qty || 1;
    const entry = stock.entry || stock.currentPrice;
    const sl = stock.stopLoss;
    if (!window.confirm(
      `Place a REAL Dhan BUY forever order?\n\n${stock.symbol}\nQty: ${qty}\nBuy above (trigger): ₹${entry}\n\nThis rests on Dhan and triggers when price crosses ₹${entry}.\nSet a stop loss from the SL tab once it fills.`
    )) return;
    try {
      const apiKey = localStorage.getItem('trading_api_key');
      if (!apiKey || apiKey === 'undefined' || apiKey === 'null') {
        localStorage.removeItem('trading_api_key');
        alert('❌ API key not set. Go to Settings → Trading Protection → Load API Key (enter PIN) once, then retry.');
        return;
      }
      const response = await fetch('/api/buy', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': apiKey
        },
        body: JSON.stringify({ symbol: stock.symbol, quantity: qty, price: entry, stopLoss: sl })
      });
      const result = await response.json();
      if (response.ok && result.success) {
        alert(`✅ ${result.message}\nOrder ID: ${result.orderId}`);
      } else if (response.status === 401 || response.status === 403) {
        localStorage.removeItem('trading_api_key');
        alert('❌ Stored API key is invalid — it has been cleared.\n\nGo to Settings → Trading Protection → Load API Key (enter PIN), then retry.');
      } else {
        alert(`❌ Order failed: ${result.detail || result.error || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Order placement failed:', error);
      alert('❌ Order placement failed (network error)');
    }
  };

  // Risk% : Reward% from entry/SL/target — replaces the abstract "R:R 1:2" in the reason line
  const riskRewardPct = (s) => {
    const e = s.entry || s.currentPrice, sl = s.stopLoss, t = s.target;
    if (!e || !sl || !t || sl >= e || t <= e) return null;
    return { risk: ((e - sl) / e * 100).toFixed(1), reward: ((t - e) / e * 100).toFixed(1) };
  };
  // Strip R:R from the reason line — it lives in the Allocation & Risk section now
  const fmtReason = (s) => s.reason ? s.reason.replace(/\s*\|\s*R:R\s*1:[\d.]+/i, '').replace(/R:R\s*1:[\d.]+\s*\|\s*/i, '') : s.reason;
  const allocation = (s) => {
    const e = s.entry || s.currentPrice, q = s.recommendedQty || 0;
    return e && q ? Math.round(e * q) : null;
  };

  if (loading) return <div className="p-4 lg:p-8">Loading recommendations...</div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-4 lg:mb-6 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
          <div>
            <h1 className="text-2xl lg:text-4xl font-bold mb-1">📊 Trading Dashboard</h1>
            <p className="text-xs lg:text-base text-slate-400">Daily stock recommendations powered by AI screening</p>
          </div>
          <div className="flex flex-wrap items-center gap-2 self-start">
            <button
              onClick={updateData}
              disabled={updating}
              title="Fetch latest candles from Dhan into the DB"
              className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 font-semibold px-4 py-2 rounded-lg"
            >
              {updating ? <Loader className="w-4 h-4 animate-spin" /> : <Database className="w-4 h-4" />}
              {updating ? 'Updating DB…' : 'Update Data'}
            </button>
            <button
              onClick={runScan}
              disabled={scanning || status?.scanRunning}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 font-semibold px-4 py-2 rounded-lg"
            >
              {(scanning || status?.scanRunning) ? <Loader className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {status?.scanRunning ? 'Scanning…' : 'Run Screener Now'}
            </button>
          </div>
        </div>

        {/* Data freshness bar */}
        {status && (
          <div className="mb-4 lg:mb-6 bg-slate-800/70 border border-slate-700 rounded-lg px-4 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-1 text-xs lg:text-sm">
            <span className="flex items-center gap-1.5 text-slate-300">
              <Calendar className="w-4 h-4 text-blue-400" /> Signals from: <b className="text-white">{fmtDate(status.signalBarDate)}</b>
            </span>
            <span className="flex items-center gap-1.5 text-slate-300">
              <Database className="w-4 h-4 text-green-400" /> Data in DB through: <b className="text-white">{fmtDate(status.dbLatestCandle)}</b>
            </span>
            <span className="text-slate-400">Picks generated: <b className="text-slate-200">{status.recsGeneratedAt ? new Date(status.recsGeneratedAt).toLocaleString('en-IN') : '—'}</b></span>
            {status.regime && <span className="text-slate-400">Regime: <b className="text-purple-300">{status.regime}</b></span>}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 lg:gap-8">
          {/* Left: Recommendations List */}
          <div className="lg:col-span-1 order-2 lg:order-1">
            <div className="bg-slate-700 rounded-lg p-4 lg:p-6 h-full overflow-y-auto max-h-[400px] lg:max-h-[600px]">
              <h2 className="text-base lg:text-xl font-bold mb-3 lg:mb-4 flex items-center gap-2">
                <TrendingUp className="w-5 lg:w-6 h-5 lg:h-6 text-green-400" />
                Today's Recommendations
              </h2>

              <div className="space-y-2 lg:space-y-3">
                {recommendations.length === 0 ? (
                  <div className="text-slate-400 text-center py-8">
                    <AlertCircle className="w-6 lg:w-8 h-6 lg:h-8 mx-auto mb-2 opacity-50" />
                    <p className="text-xs lg:text-sm">No recommendations available</p>
                  </div>
                ) : (
                  recommendations.map((stock) => (
                    <button
                      key={stock.symbol}
                      onClick={() => selectStock(stock)}
                      className={`w-full text-left p-3 lg:p-4 rounded-lg transition-all ${
                        selectedStock?.symbol === stock.symbol
                          ? 'bg-blue-600 border-2 border-blue-400'
                          : 'bg-slate-600 hover:bg-slate-500 border-2 border-transparent'
                      }`}
                    >
                      <div className="flex justify-between items-start gap-2">
                        <div>
                          <p className="font-bold text-base lg:text-lg flex items-center gap-1.5 flex-wrap">
                            {stock.symbol}
                            {stock.heldQty > 0 && (
                              <span className="text-[10px] font-semibold bg-purple-700 text-purple-100 px-1.5 py-0.5 rounded" title={`Already holding ${stock.heldQty} shares`}>
                                HOLDING {stock.heldQty}
                              </span>
                            )}
                            {!stock.heldQty && stock.positionQty > 0 && (
                              <span className="text-[10px] font-semibold bg-purple-700 text-purple-100 px-1.5 py-0.5 rounded" title={`Bought today: ${stock.positionQty} shares`}>
                                BOUGHT TODAY {stock.positionQty}
                              </span>
                            )}
                            {stock.hasForeverBuy && (
                              <span className="text-[10px] font-semibold bg-amber-700 text-amber-100 px-1.5 py-0.5 rounded" title="A BUY forever order is already resting on Dhan">
                                ORDER RESTING
                              </span>
                            )}
                          </p>
                          <p className="text-xs lg:text-sm text-slate-300 truncate">{stock.company}</p>
                        </div>
                        <div className="text-right flex-shrink-0">
                          <p className="font-bold text-sm lg:text-base">₹{stock.currentPrice}</p>
                          <p className={`text-xs lg:text-sm ${stock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {stock.change >= 0 ? '+' : ''}{stock.change.toFixed(2)}%
                          </p>
                        </div>
                      </div>
                      <div className="mt-2 pt-2 border-t border-slate-500">
                        <p className="text-xs text-slate-300 space-y-1">
                          <span className="inline-block bg-green-700 px-1.5 py-0.5 rounded mr-1 text-xs">
                            T: ₹{stock.target}
                          </span>
                          <span className="inline-block bg-red-700 px-1.5 py-0.5 rounded text-xs">
                            SL: ₹{stock.stopLoss}
                          </span>
                        </p>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Right: Chart & Buy Section */}
          <div className="lg:col-span-2 order-1 lg:order-2">
            {selectedStock && (
              <div className="space-y-4 lg:space-y-6">
                {/* Stock Details Card */}
                <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
                  <div className="flex justify-between items-start mb-4 gap-2">
                    <div>
                      <h3 className="text-xl lg:text-2xl font-bold">{selectedStock.symbol}</h3>
                      <p className="text-xs lg:text-base text-slate-400">{selectedStock.company}</p>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <p className="text-2xl lg:text-3xl font-bold">₹{selectedStock.currentPrice}</p>
                      <p className={`text-sm lg:text-lg font-semibold ${selectedStock.change >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {selectedStock.change >= 0 ? '📈' : '📉'} {selectedStock.change >= 0 ? '+' : ''}{selectedStock.change.toFixed(2)}%
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 lg:gap-4 mt-4 pt-4 border-t border-slate-600">
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Target Price</p>
                      <p className="text-lg lg:text-2xl font-bold text-green-400">₹{selectedStock.target}</p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Stop Loss</p>
                      <p className="text-lg lg:text-2xl font-bold text-red-400">₹{selectedStock.stopLoss}</p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Confidence</p>
                      <p className="text-lg lg:text-2xl font-bold text-blue-400">{selectedStock.confidence}%</p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Upside</p>
                      <p className="text-lg lg:text-2xl font-bold text-purple-400">
                        {(((selectedStock.target - selectedStock.currentPrice) / selectedStock.currentPrice) * 100).toFixed(1)}%
                      </p>
                      <p className="text-[10px] text-slate-500">current → target</p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Allocation</p>
                      <p className="text-lg lg:text-2xl font-bold text-amber-300">
                        {allocation(selectedStock) != null ? `₹${allocation(selectedStock).toLocaleString('en-IN')}` : '—'}
                      </p>
                      <p className="text-[10px] text-slate-500">{selectedStock.recommendedQty} × ₹{selectedStock.entry ?? selectedStock.currentPrice}</p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Risk : Reward</p>
                      <p className="text-lg lg:text-2xl font-bold text-blue-300">
                        {(() => { const rr = riskRewardPct(selectedStock); return rr ? `${rr.risk}% : ${rr.reward}%` : (selectedStock.rrRatio != null ? `1 : ${selectedStock.rrRatio}` : '—'); })()}
                      </p>
                      <p className="text-[10px] text-slate-500">of entry price</p>
                    </div>
                    <div>
                      <p className="text-slate-400 text-xs lg:text-sm">Total Risk</p>
                      <p className="text-lg lg:text-2xl font-bold text-red-300">
                        {selectedStock.riskPerShare != null && selectedStock.recommendedQty
                          ? `₹${Math.round(selectedStock.riskPerShare * selectedStock.recommendedQty).toLocaleString('en-IN')}`
                          : '—'}
                      </p>
                      <p className="text-[10px] text-slate-500">if SL hits</p>
                    </div>
                  </div>

                  <p className="mt-3 lg:mt-4 text-slate-300 text-xs lg:text-sm">
                    <strong>Reason:</strong> {fmtReason(selectedStock)}
                  </p>

                  {/* Full screener detail (matches Telegram alert) */}
                  {selectedStock.entryType && (
                    <div className="mt-4 pt-4 border-t border-slate-600">
                      <div className="flex flex-wrap gap-2 mb-3 text-xs">
                        {selectedStock.regime && <span className="px-2 py-1 rounded bg-purple-900/60 text-purple-300">🌍 {selectedStock.regime}</span>}
                        <span className="px-2 py-1 rounded bg-blue-900/60 text-blue-300">🎯 {selectedStock.entryType}</span>
                        {selectedStock.signalBarDate && <span className="px-2 py-1 rounded bg-slate-600 text-slate-200">📅 Signal: {selectedStock.signalBarDate}</span>}
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2 text-xs lg:text-sm">
                        <Detail label="Entry (buy above)" value={`₹${selectedStock.entry ?? '—'}`} />
                        <Detail label="Stop Loss (close below)" value={`₹${selectedStock.stopLoss}`} />
                        <Detail label={`Target (${selectedStock.targetStrategy || 'FIXED_R'})`} value={`₹${selectedStock.target}`} />
                        <Detail label="Qty" value={`${selectedStock.recommendedQty}${selectedStock.baseStage != null ? ` (stage ${selectedStock.baseStage}, x${selectedStock.stageMultiplier ?? 1})` : ''}`} />
                        <Detail label="Risk / Share" value={selectedStock.riskPerShare != null ? `₹${selectedStock.riskPerShare}` : '—'} />
                        <Detail label="Tick" value={selectedStock.tickSize != null ? `₹${selectedStock.tickSize}` : '—'} />
                        <Detail label="Base Stage" value={selectedStock.baseStage ?? '—'} />
                        <Detail label="Base Quality" value={selectedStock.baseQuality != null ? selectedStock.baseQuality.toFixed(2) : '—'} />
                        <Detail label="Liquidity" value={selectedStock.liquidityCr != null ? `₹${selectedStock.liquidityCr} cr/day` : '—'} />
                        <Detail label="IFP" value={selectedStock.ifp ?? '—'} />
                        <Detail label="Base Range" value={selectedStock.baseRangePct != null ? `${selectedStock.baseRangePct}%` : '—'} />
                      </div>
                    </div>
                  )}
                </div>

                {/* Chart Section — Daily / Weekly toggle */}
                <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
                  <div className="flex items-center justify-between mb-3 lg:mb-4">
                    <h4 className="text-base lg:text-lg font-bold capitalize">{chartType} Chart</h4>
                    <div className="flex items-center gap-2">
                      <div className="flex bg-slate-800 rounded-lg p-1">
                        {['daily', 'weekly'].map(t => (
                          <button
                            key={t}
                            onClick={() => switchChartType(t)}
                            className={`px-3 lg:px-4 py-1 rounded-md text-xs lg:text-sm font-semibold capitalize transition-all ${
                              chartType === t ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white'
                            }`}
                          >
                            {t}
                          </button>
                        ))}
                      </div>
                      <button
                        onClick={() => setChartOpen(true)}
                        title="Expand chart"
                        className="p-1.5 bg-slate-800 rounded-lg text-slate-400 hover:text-white"
                      >
                        <Maximize2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  {chartData === null ? (
                    <div className="h-48 lg:h-64 flex items-center justify-center text-slate-400">Loading chart…</div>
                  ) : chartData === '' ? (
                    <div className="h-48 lg:h-64 flex items-center justify-center text-slate-400">Chart unavailable for this symbol</div>
                  ) : (
                    <div className="rounded overflow-x-auto [&_svg]:w-full [&_svg]:h-auto" dangerouslySetInnerHTML={{ __html: chartData }} />
                  )}
                </div>

                {/* Buy Button */}
                {selectedStock.owned ? (
                  <div className="w-full bg-slate-700 border border-purple-600/50 text-purple-200 font-semibold py-3 lg:py-4 px-4 rounded-lg text-center text-sm lg:text-base">
                    {selectedStock.heldQty > 0
                      ? `Already holding ${selectedStock.heldQty} shares — manage from the SL tab`
                      : selectedStock.positionQty > 0
                        ? `Bought today (${selectedStock.positionQty} shares) — manage from the SL tab`
                        : 'A BUY forever order is already resting on Dhan'}
                  </div>
                ) : (
                  <button
                    onClick={() => handleBuy(selectedStock)}
                    className="w-full bg-gradient-to-r from-green-500 to-green-600 hover:from-green-600 hover:to-green-700 text-white font-bold py-3 lg:py-4 px-4 lg:px-6 rounded-lg flex items-center justify-center gap-2 lg:gap-3 text-base lg:text-lg transition-all transform hover:scale-105"
                  >
                    <ShoppingCart className="w-5 lg:w-6 h-5 lg:h-6" />
                    Buy {selectedStock.symbol}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <ChartModal
        symbol={selectedStock?.symbol}
        open={chartOpen}
        onClose={() => setChartOpen(false)}
      />
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <div>
      <p className="text-slate-400 text-[10px] lg:text-xs">{label}</p>
      <p className="font-semibold text-white">{value}</p>
    </div>
  );
}
