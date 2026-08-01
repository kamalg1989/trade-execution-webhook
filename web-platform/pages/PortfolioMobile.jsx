import React, { useState, useEffect } from 'react';
import { Download } from 'lucide-react';

const fmtMoney = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const pnlClass = (v) => v == null ? 'text-slate-300' : v >= 0 ? 'text-green-400' : 'text-red-400';

const TAG_COLORS = {
  slate: 'bg-slate-700/60 text-slate-300',
  blue: 'bg-blue-900/50 text-blue-300',
  purple: 'bg-purple-900/50 text-purple-300',
  amber: 'bg-amber-900/50 text-amber-300',
  emerald: 'bg-emerald-900/50 text-emerald-300',
};
const Tag = ({ children, color = 'slate' }) => children ? (
  <span className={`text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap ${TAG_COLORS[color] || TAG_COLORS.slate}`}>{children}</span>
) : null;

export default function PortfolioMobile() {
  const [holdings, setHoldings] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [insights, setInsights] = useState(null);
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
      setInsights(data.insights || null);
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
      ...data.map(row => headers.map(h => `"${row[h] ?? ''}"`).join(','))
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
      {/* Insights */}
      {insights && insights.totalClosed > 0 && (
        <div className="px-4 pt-4">
          <div className="grid grid-cols-4 gap-1.5 mb-2">
            <div className="bg-slate-700 rounded-lg p-2">
              <p className="text-slate-400 text-[9px]">Win Rate</p>
              <p className="text-sm font-bold">{insights.winRate}%</p>
            </div>
            <div className="bg-slate-700 rounded-lg p-2">
              <p className="text-slate-400 text-[9px]">Realized</p>
              <p className={`text-sm font-bold truncate ${pnlClass(insights.totalRealizedPnL)}`}>{fmtMoney(insights.totalRealizedPnL)}</p>
            </div>
            <div className="bg-slate-700 rounded-lg p-2">
              <p className="text-slate-400 text-[9px]">Avg R</p>
              <p className={`text-sm font-bold ${insights.avgRMultiple >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {insights.avgRMultiple != null ? `${insights.avgRMultiple >= 0 ? '+' : ''}${insights.avgRMultiple}R` : '—'}
              </p>
            </div>
            <div className="bg-slate-700 rounded-lg p-2">
              <p className="text-slate-400 text-[9px]">Avg Days</p>
              <p className="text-sm font-bold">{insights.avgHoldingDays ?? '—'}</p>
            </div>
          </div>
          {(insights.quantOnly.count > 0 || insights.aiReviewed.count > 0) && (
            <div className="grid grid-cols-2 gap-1.5 mb-2">
              <div className="bg-slate-700 rounded-lg p-2">
                <p className="text-slate-400 text-[9px]">📐 Quant-only</p>
                <p className="text-xs font-bold">{insights.quantOnly.winRate}% <span className="text-slate-500 font-normal">win · {insights.quantOnly.count}</span></p>
              </div>
              <div className="bg-slate-700 rounded-lg p-2">
                <p className="text-slate-400 text-[9px]">🤖 AI-reviewed</p>
                <p className="text-xs font-bold">{insights.aiReviewed.winRate}% <span className="text-slate-500 font-normal">win · {insights.aiReviewed.count}</span></p>
              </div>
            </div>
          )}
        </div>
      )}

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
          Open ({holdings.length})
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
            <p className="text-slate-400 text-sm">No {activeTab === 'holdings' ? 'open positions' : 'closed trades yet'}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {data.map((item, idx) => (
              <div key={item.id || item.securityId || idx} className="bg-slate-700 rounded-lg p-3">
                {/* Symbol & Quantity */}
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <p className="font-bold text-base">{item.symbol}</p>
                    <p className="text-xs text-slate-400">{item.quantity} units</p>
                  </div>
                  <div className="text-right">
                    <p className={`font-bold text-sm ${pnlClass(item.pnl)}`}>{fmtMoney(item.pnl)}</p>
                    <p className={`text-xs ${pnlClass(item.returnPercent)}`}>
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
                      <div className="flex justify-between">
                        <span className="text-slate-400">R-Multiple:</span>
                        <span className={item.rMultiple >= 0 ? 'text-green-400' : 'text-red-400'}>
                          {item.rMultiple != null ? `${item.rMultiple >= 0 ? '+' : ''}${item.rMultiple}R` : '—'}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Holding Days:</span>
                        <span>{item.holdingDays ?? '—'}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Closed Via:</span>
                        <span>{item.closedVia || '—'}</span>
                      </div>
                    </>
                  )}
                </div>

                {/* Tags */}
                {(item.reason || item.entryType || item.regime || item.aiReviewed) && (
                  <div className="mt-2 flex gap-1 flex-wrap items-center">
                    {item.reason && <span className="text-[10px] text-slate-400 truncate">{item.reason}</span>}
                    <Tag color="blue">{item.entryType}</Tag>
                    {item.baseStage != null && <Tag color="amber">Base {item.baseStage}</Tag>}
                    <Tag color="purple">{item.regime}</Tag>
                    {item.aiReviewed && <Tag color="emerald">AI{item.aiRank ? ` #${item.aiRank}` : ''}</Tag>}
                  </div>
                )}

                {/* Date */}
                <div className="mt-2 text-xs text-slate-400">
                  {activeTab === 'holdings'
                    ? (item.entryDate ? `Entered: ${new Date(item.entryDate).toLocaleDateString()}` : null)
                    : (item.exitDate ? `Exited: ${new Date(item.exitDate).toLocaleDateString()}` : null)
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
