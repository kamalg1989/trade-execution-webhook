import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, AlertCircle, ShoppingCart, BarChart3 } from 'lucide-react';
import ChartModal from '../components/ChartModal';

export default function DashboardMobile() {
  const [recommendations, setRecommendations] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);
  const [loading, setLoading] = useState(true);
  const [chartOpen, setChartOpen] = useState(false);

  useEffect(() => {
    fetchRecommendations();
  }, []);

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
    try {
      const response = await fetch('/api/buy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: stock.symbol,
          quantity: stock.recommendedQty || 1,
          price: stock.currentPrice,
          stopLoss: stock.stopLoss
        })
      });
      const result = await response.json();
      if (result.success) {
        alert(`✅ Order placed for ${stock.symbol}`);
      } else {
        alert(`❌ Order failed: ${result.error}`);
      }
    } catch (error) {
      console.error('Order placement failed:', error);
      alert('❌ Order placement failed');
    }
  };

  if (loading) return <div className="p-4 text-center text-slate-400">Loading...</div>;

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen">
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
                  <p className="font-bold text-base">{stock.symbol}</p>
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

            <p className="mt-3 text-xs text-slate-300 line-clamp-2">
              <strong>Reason:</strong> {selectedStock.reason}
            </p>
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
          <button
            onClick={() => handleBuy(selectedStock)}
            className="w-full bg-gradient-to-r from-green-500 to-green-600 hover:from-green-600 hover:to-green-700 text-white font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-all"
          >
            <ShoppingCart className="w-5 h-5" />
            Buy {selectedStock.symbol}
          </button>
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
