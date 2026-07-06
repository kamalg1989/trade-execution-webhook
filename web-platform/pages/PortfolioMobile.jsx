import React, { useState, useEffect } from 'react';
import { Download, TrendingUp, TrendingDown } from 'lucide-react';

export default function PortfolioMobile() {
  const [holdings, setHoldings] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [activeTab, setActiveTab] = useState('holdings');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPortfolioData();
  }, []);

  const fetchPortfolioData = async () => {
    try {
      const response = await fetch('/api/portfolio/full');
      const data = await response.json();
      setHoldings(data.holdings || []);
      setClosedTrades(data.closedTrades || []);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch portfolio:', error);
      setLoading(false);
    }
  };

  const exportCSV = () => {
    const data = activeTab === 'holdings' ? holdings : closedTrades;
    if (data.length === 0) return;

    const headers = Object.keys(data[0]);
    const csv = [
      headers.join(','),
      ...data.map(row => headers.map(h => `"${row[h]}"`).join(','))
    ].join('\n');

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `portfolio-${activeTab}-${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
  };

  if (loading) return <div className="p-4 text-center text-slate-400">Loading...</div>;

  const data = activeTab === 'holdings' ? holdings : closedTrades;

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen">
      {/* Tabs */}
      <div className="flex border-b border-slate-700 sticky top-0 bg-slate-800 z-10">
        <button
          onClick={() => setActiveTab('holdings')}
          className={`flex-1 px-4 py-3 font-semibold text-sm text-center border-b-2 transition-all ${
            activeTab === 'holdings'
              ? 'border-blue-500 text-blue-400'
              : 'border-transparent text-slate-400'
          }`}
        >
          Holdings ({holdings.length})
        </button>
        <button
          onClick={() => setActiveTab('closed')}
          className={`flex-1 px-4 py-3 font-semibold text-sm text-center border-b-2 transition-all ${
            activeTab === 'closed'
              ? 'border-blue-500 text-blue-400'
              : 'border-transparent text-slate-400'
          }`}
        >
          Closed ({closedTrades.length})
        </button>
      </div>

      {/* Export Button */}
      <div className="px-4 py-2 bg-slate-700/50 flex justify-end">
        <button
          onClick={exportCSV}
          className="flex items-center gap-1 bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded text-xs font-semibold transition-all"
        >
          <Download className="w-3 h-3" />
          Export
        </button>
      </div>

      {/* Data Cards */}
      <div className="px-4 py-4">
        {data.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-slate-400 text-sm">No {activeTab} data</p>
          </div>
        ) : (
          <div className="space-y-2">
            {data.map((item, idx) => (
              <div key={idx} className="bg-slate-700 rounded-lg p-3">
                {/* Symbol & Quantity */}
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <p className="font-bold text-base">{item.symbol}</p>
                    <p className="text-xs text-slate-400">{item.quantity} units</p>
                  </div>
                  <div className="text-right">
                    <p className={`font-bold text-sm ${item.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {item.pnl >= 0 ? '+' : ''}₹{Math.abs(item.pnl)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}
                    </p>
                    <p className={`text-xs ${item.returnPercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {item.returnPercent >= 0 ? '+' : ''}{item.returnPercent?.toFixed(2)}%
                    </p>
                  </div>
                </div>

                {/* Prices */}
                <div className="bg-slate-600/50 rounded p-2 space-y-1 text-xs">
                  {activeTab === 'holdings' ? (
                    <>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Avg Cost:</span>
                        <span>₹{item.avgCost?.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Current:</span>
                        <span>₹{item.currentPrice?.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Total Value:</span>
                        <span className="font-bold">₹{item.totalValue?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</span>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Entry:</span>
                        <span>₹{item.entryPrice?.toFixed(2)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Exit:</span>
                        <span>₹{item.exitPrice?.toFixed(2)}</span>
                      </div>
                    </>
                  )}
                </div>

                {/* Date */}
                <div className="mt-2 text-xs text-slate-400">
                  {activeTab === 'holdings'
                    ? `Entered: ${new Date(item.entryDate).toLocaleDateString()}`
                    : `Exited: ${new Date(item.exitDate).toLocaleDateString()}`
                  }
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
