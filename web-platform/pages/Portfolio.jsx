import React, { useState, useEffect } from 'react';
import { Download } from 'lucide-react';

const fmtMoney = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const fmtPct = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
const fmtDate = (v) => v ? new Date(v).toLocaleDateString() : '—';
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

export default function Portfolio() {
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
    const headers = Object.keys(data[0] || {});
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

  if (loading) return <div className="p-8">Loading portfolio data...</div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-start mb-6 lg:mb-8">
          <div>
            <h1 className="text-2xl lg:text-4xl font-bold mb-1 lg:mb-2">💼 Portfolio</h1>
            <p className="text-xs lg:text-base text-slate-400">Open positions, closed trade history & insights</p>
          </div>
          <button
            onClick={exportCSV}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg font-semibold transition-all"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>

        {/* Insights */}
        {insights && insights.totalClosed > 0 && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4 mb-6 lg:mb-8">
            <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-xs mb-1">Win Rate</p>
              <p className="text-lg lg:text-2xl font-bold">{insights.winRate}%</p>
              <p className="text-[11px] text-slate-500">{insights.totalClosed} closed</p>
            </div>
            <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-xs mb-1">Realized P&L</p>
              <p className={`text-lg lg:text-2xl font-bold ${pnlClass(insights.totalRealizedPnL)}`}>{fmtMoney(insights.totalRealizedPnL)}</p>
            </div>
            <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-xs mb-1">Avg R-Multiple</p>
              <p className={`text-lg lg:text-2xl font-bold ${insights.avgRMultiple >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {insights.avgRMultiple != null ? `${insights.avgRMultiple >= 0 ? '+' : ''}${insights.avgRMultiple}R` : '—'}
              </p>
            </div>
            <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-xs mb-1">Avg Holding Days</p>
              <p className="text-lg lg:text-2xl font-bold">{insights.avgHoldingDays ?? '—'}</p>
            </div>
          </div>
        )}

        {insights && insights.totalClosed > 0 && (insights.quantOnly.count > 0 || insights.aiReviewed.count > 0) && (
          <div className="grid grid-cols-2 gap-2 lg:gap-4 mb-6 lg:mb-8">
            <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-xs mb-1">📐 Quant-Only Trades</p>
              <div className="flex items-baseline gap-2">
                <span className="text-lg lg:text-xl font-bold">{insights.quantOnly.winRate}%</span>
                <span className="text-[11px] text-slate-500">win · {insights.quantOnly.count} trades</span>
              </div>
              <p className={`text-xs mt-0.5 ${pnlClass(insights.quantOnly.avgPnL)}`}>avg {fmtMoney(insights.quantOnly.avgPnL)}</p>
            </div>
            <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
              <p className="text-slate-400 text-xs mb-1">🤖 AI-Reviewed Trades</p>
              <div className="flex items-baseline gap-2">
                <span className="text-lg lg:text-xl font-bold">{insights.aiReviewed.winRate}%</span>
                <span className="text-[11px] text-slate-500">win · {insights.aiReviewed.count} trades</span>
              </div>
              <p className={`text-xs mt-0.5 ${pnlClass(insights.aiReviewed.avgPnL)}`}>avg {fmtMoney(insights.aiReviewed.avgPnL)}</p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-4 mb-6 border-b border-slate-700">
          <button
            onClick={() => setActiveTab('holdings')}
            className={`px-4 py-3 font-semibold border-b-2 transition-all ${
              activeTab === 'holdings'
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-slate-400 hover:text-white'
            }`}
          >
            Open Positions ({holdings.length})
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

        {/* Open Positions — compact */}
        {activeTab === 'holdings' && (
          <div className="bg-slate-700 rounded-lg overflow-hidden overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-600">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold">Symbol</th>
                  <th className="px-4 py-3 text-right font-semibold">Qty</th>
                  <th className="px-4 py-3 text-right font-semibold">Avg Cost</th>
                  <th className="px-4 py-3 text-right font-semibold">Current</th>
                  <th className="px-4 py-3 text-right font-semibold">P&L</th>
                  <th className="px-4 py-3 text-right font-semibold">% Return</th>
                  <th className="px-4 py-3 text-left font-semibold">Tags</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-600">
                {holdings.map(h => (
                  <tr key={h.securityId || h.symbol} className="hover:bg-slate-600 transition-colors">
                    <td className="px-4 py-2.5 font-bold">{h.symbol}</td>
                    <td className="px-4 py-2.5 text-right">{h.quantity}</td>
                    <td className="px-4 py-2.5 text-right">₹{h.avgCost?.toFixed(2)}</td>
                    <td className="px-4 py-2.5 text-right">₹{h.currentPrice?.toFixed(2)}</td>
                    <td className={`px-4 py-2.5 text-right font-bold ${pnlClass(h.pnl)}`}>{fmtMoney(h.pnl)}</td>
                    <td className={`px-4 py-2.5 text-right font-semibold ${pnlClass(h.returnPercent)}`}>{fmtPct(h.returnPercent)}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex gap-1 flex-wrap">
                        <Tag color="blue">{h.entryType}</Tag>
                        <Tag color="purple">{h.regime}</Tag>
                        {h.aiReviewed && <Tag color="emerald">AI</Tag>}
                      </div>
                    </td>
                  </tr>
                ))}
                {holdings.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-6 text-center text-slate-400">No open positions</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Closed Trades — rich */}
        {activeTab === 'closed' && (
          <div className="bg-slate-700 rounded-lg overflow-hidden overflow-x-auto">
            <table className="w-full text-sm whitespace-nowrap">
              <thead className="bg-slate-600">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold">Symbol</th>
                  <th className="px-4 py-3 text-right font-semibold">Qty</th>
                  <th className="px-4 py-3 text-right font-semibold">Entry</th>
                  <th className="px-4 py-3 text-right font-semibold">Exit</th>
                  <th className="px-4 py-3 text-right font-semibold">P&L</th>
                  <th className="px-4 py-3 text-right font-semibold">R</th>
                  <th className="px-4 py-3 text-right font-semibold">Days</th>
                  <th className="px-4 py-3 text-left font-semibold">Closed Via</th>
                  <th className="px-4 py-3 text-left font-semibold">Reason / Context</th>
                  <th className="px-4 py-3 text-left font-semibold">Exit Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-600">
                {closedTrades.map(t => (
                  <tr key={t.id} className="hover:bg-slate-600 transition-colors">
                    <td className="px-4 py-2.5 font-bold">{t.symbol}</td>
                    <td className="px-4 py-2.5 text-right">{t.quantity}</td>
                    <td className="px-4 py-2.5 text-right">₹{t.entryPrice?.toFixed(2)}</td>
                    <td className="px-4 py-2.5 text-right">₹{t.exitPrice?.toFixed(2)}</td>
                    <td className={`px-4 py-2.5 text-right font-bold ${pnlClass(t.pnl)}`}>{fmtMoney(t.pnl)}</td>
                    <td className={`px-4 py-2.5 text-right font-semibold ${t.rMultiple >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {t.rMultiple != null ? `${t.rMultiple >= 0 ? '+' : ''}${t.rMultiple}R` : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-slate-300">{t.holdingDays ?? '—'}</td>
                    <td className="px-4 py-2.5 text-slate-300">{t.closedVia || '—'}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex gap-1 flex-wrap items-center max-w-xs">
                        {t.reason && <span className="text-xs text-slate-300 truncate">{t.reason}</span>}
                        <Tag color="blue">{t.entryType}</Tag>
                        {t.baseStage != null && <Tag color="amber">Base {t.baseStage}</Tag>}
                        <Tag color="purple">{t.regime}</Tag>
                        {t.aiReviewed && <Tag color="emerald">AI #{t.aiRank ?? ''}</Tag>}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-slate-400 text-xs">{fmtDate(t.exitDate)}</td>
                  </tr>
                ))}
                {closedTrades.length === 0 && (
                  <tr><td colSpan={10} className="px-4 py-6 text-center text-slate-400">No closed trades yet</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
