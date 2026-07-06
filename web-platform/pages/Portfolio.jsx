import React, { useState, useEffect } from 'react';
import { Download, Filter } from 'lucide-react';

export default function Portfolio() {
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
    const headers = Object.keys(data[0] || {});
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

  if (loading) return <div className="p-8">Loading portfolio data...</div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-start mb-8">
          <div>
            <h1 className="text-4xl font-bold mb-2">💼 Portfolio</h1>
            <p className="text-slate-400">Complete holdings and trade history</p>
          </div>
          <button
            onClick={exportCSV}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg font-semibold transition-all"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-4 mb-8 border-b border-slate-700">
          <button
            onClick={() => setActiveTab('holdings')}
            className={`px-4 py-3 font-semibold border-b-2 transition-all ${
              activeTab === 'holdings'
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            Current Holdings ({holdings.length})
          </button>
          <button
            onClick={() => setActiveTab('closed')}
            className={`px-4 py-3 font-semibold border-b-2 transition-all ${
              activeTab === 'closed'
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            Closed Trades ({closedTrades.length})
          </button>
        </div>

        {/* Holdings Table */}
        {activeTab === 'holdings' && (
          <div className="bg-slate-700 rounded-lg overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-600">
                <tr>
                  <th className="px-6 py-4 text-left font-semibold">Symbol</th>
                  <th className="px-6 py-4 text-left font-semibold">Quantity</th>
                  <th className="px-6 py-4 text-left font-semibold">Avg Cost</th>
                  <th className="px-6 py-4 text-left font-semibold">Current Price</th>
                  <th className="px-6 py-4 text-left font-semibold">Total Value</th>
                  <th className="px-6 py-4 text-left font-semibold">P&L</th>
                  <th className="px-6 py-4 text-left font-semibold">% Return</th>
                  <th className="px-6 py-4 text-left font-semibold">Entry Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-600">
                {holdings.map(holding => (
                  <tr key={holding.symbol} className="hover:bg-slate-600 transition-colors">
                    <td className="px-6 py-4 font-bold">{holding.symbol}</td>
                    <td className="px-6 py-4">{holding.quantity}</td>
                    <td className="px-6 py-4">₹{holding.avgCost?.toFixed(2)}</td>
                    <td className="px-6 py-4">₹{holding.currentPrice?.toFixed(2)}</td>
                    <td className="px-6 py-4 font-semibold">₹{holding.totalValue?.toLocaleString('en-IN', {maximumFractionDigits: 2})}</td>
                    <td className={`px-6 py-4 font-bold ${holding.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {holding.pnl >= 0 ? '+' : ''}₹{holding.pnl?.toLocaleString('en-IN', {maximumFractionDigits: 2})}
                    </td>
                    <td className={`px-6 py-4 font-semibold ${holding.returnPercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {holding.returnPercent >= 0 ? '+' : ''}{holding.returnPercent?.toFixed(2)}%
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-300">
                      {new Date(holding.entryDate).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Closed Trades Table */}
        {activeTab === 'closed' && (
          <div className="bg-slate-700 rounded-lg overflow-hidden">
            <table className="w-full">
              <thead className="bg-slate-600">
                <tr>
                  <th className="px-6 py-4 text-left font-semibold">Symbol</th>
                  <th className="px-6 py-4 text-left font-semibold">Quantity</th>
                  <th className="px-6 py-4 text-left font-semibold">Entry Price</th>
                  <th className="px-6 py-4 text-left font-semibold">Exit Price</th>
                  <th className="px-6 py-4 text-left font-semibold">P&L</th>
                  <th className="px-6 py-4 text-left font-semibold">% Return</th>
                  <th className="px-6 py-4 text-left font-semibold">Duration</th>
                  <th className="px-6 py-4 text-left font-semibold">Exit Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-600">
                {closedTrades.map(trade => (
                  <tr key={trade.id} className="hover:bg-slate-600 transition-colors">
                    <td className="px-6 py-4 font-bold">{trade.symbol}</td>
                    <td className="px-6 py-4">{trade.quantity}</td>
                    <td className="px-6 py-4">₹{trade.entryPrice?.toFixed(2)}</td>
                    <td className="px-6 py-4">₹{trade.exitPrice?.toFixed(2)}</td>
                    <td className={`px-6 py-4 font-bold ${trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.pnl >= 0 ? '+' : ''}₹{trade.pnl?.toLocaleString('en-IN', {maximumFractionDigits: 2})}
                    </td>
                    <td className={`px-6 py-4 font-semibold ${trade.returnPercent >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.returnPercent >= 0 ? '+' : ''}{trade.returnPercent?.toFixed(2)}%
                    </td>
                    <td className="px-6 py-4 text-sm">
                      {trade.duration || '—'}
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-300">
                      {new Date(trade.exitDate).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
