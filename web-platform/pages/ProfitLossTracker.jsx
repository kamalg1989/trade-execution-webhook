import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Eye, EyeOff } from 'lucide-react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

export default function ProfitLossTracker() {
  const [portfolio, setPortfolio] = useState({
    totalInvested: 0,
    totalValue: 0,
    unrealizedPnL: 0,
    realizedPnL: 0,
    positions: [],
    performanceHistory: []
  });
  const [showBalance, setShowBalance] = useState(true);
  const [loading, setLoading] = useState(true);
  const [timeframe, setTimeframe] = useState('1m');

  useEffect(() => {
    fetchPortfolioData();
  }, [timeframe]);

  const fetchPortfolioData = async () => {
    try {
      const response = await fetch(`/api/portfolio?timeframe=${timeframe}`);
      const data = await response.json();
      setPortfolio(data);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch portfolio:', error);
      setLoading(false);
    }
  };

  const totalPnL = portfolio.unrealizedPnL + portfolio.realizedPnL;
  const totalPnLPercent = portfolio.totalInvested > 0
    ? ((totalPnL / portfolio.totalInvested) * 100).toFixed(2)
    : 0;

  const gainers = portfolio.positions?.filter(p => p.pnl > 0) || [];
  const losers = portfolio.positions?.filter(p => p.pnl < 0) || [];

  const COLORS = ['#10b981', '#ef4444', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899'];

  if (loading) return <div className="p-4 lg:p-8">Loading portfolio data...</div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6 lg:mb-8">
          <h1 className="text-2xl lg:text-4xl font-bold mb-1 lg:mb-2">📈 Profit & Loss Tracker</h1>
          <p className="text-xs lg:text-base text-slate-400">Real-time portfolio performance and detailed analytics</p>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-4 mb-6 lg:mb-8">
          <div className="bg-slate-700 rounded-lg p-3 lg:p-6">
            <p className="text-slate-400 text-xs lg:text-sm mb-1 lg:mb-2">Total Invested</p>
            <div className="flex items-center justify-between gap-2">
              <p className="text-lg lg:text-3xl font-bold truncate">
                {showBalance ? `₹${portfolio.totalInvested?.toLocaleString('en-IN', {maximumFractionDigits: 0})}` : '••••'}
              </p>
              <button onClick={() => setShowBalance(!showBalance)} className="text-slate-400 hover:text-white flex-shrink-0">
                {showBalance ? <Eye className="w-4 lg:w-5 h-4 lg:h-5" /> : <EyeOff className="w-4 lg:w-5 h-4 lg:h-5" />}
              </button>
            </div>
          </div>

          <div className="bg-slate-700 rounded-lg p-3 lg:p-6">
            <p className="text-slate-400 text-xs lg:text-sm mb-1 lg:mb-2">Current Value</p>
            <p className="text-lg lg:text-3xl font-bold text-blue-400 truncate">
              {showBalance ? `₹${portfolio.totalValue?.toLocaleString('en-IN', {maximumFractionDigits: 0})}` : '••••'}
            </p>
          </div>

          <div className={`rounded-lg p-3 lg:p-6 ${totalPnL >= 0 ? 'bg-green-900' : 'bg-red-900'}`}>
            <p className="text-slate-300 text-xs lg:text-sm mb-1 lg:mb-2">Unrealized P&L</p>
            <p className={`text-lg lg:text-3xl font-bold truncate ${totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {showBalance ? (
                <>
                  {totalPnL >= 0 ? '+' : ''}₹{portfolio.unrealizedPnL?.toLocaleString('en-IN', {maximumFractionDigits: 0})}
                </>
              ) : '••••'}
            </p>
            <p className="text-xs lg:text-sm text-slate-300 mt-0.5 lg:mt-1">{totalPnLPercent}%</p>
          </div>

          <div className={`rounded-lg p-3 lg:p-6 ${portfolio.realizedPnL >= 0 ? 'bg-blue-900' : 'bg-slate-700'}`}>
            <p className="text-slate-300 text-xs lg:text-sm mb-1 lg:mb-2">Realized P&L</p>
            <p className={`text-lg lg:text-3xl font-bold truncate ${portfolio.realizedPnL >= 0 ? 'text-blue-400' : 'text-red-400'}`}>
              {showBalance ? (
                <>
                  {portfolio.realizedPnL >= 0 ? '+' : ''}₹{portfolio.realizedPnL?.toLocaleString('en-IN', {maximumFractionDigits: 0})}
                </>
              ) : '••••'}
            </p>
          </div>
        </div>

        {/* Charts Section */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:gap-8 mb-6 lg:mb-8">
          {/* Performance Over Time */}
          <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
            <div className="flex flex-col lg:flex-row lg:justify-between lg:items-center mb-3 lg:mb-4 gap-2">
              <h3 className="text-base lg:text-xl font-bold">Performance Over Time</h3>
              <div className="flex gap-1 lg:gap-2">
                {['1w', '1m', '3m', '1y'].map(period => (
                  <button
                    key={period}
                    onClick={() => setTimeframe(period)}
                    className={`px-2 lg:px-3 py-1 rounded text-xs lg:text-sm transition-all ${
                      timeframe === period
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-600 text-slate-300 hover:bg-slate-500'
                    }`}
                  >
                    {period}
                  </button>
                ))}
              </div>
            </div>

            <ResponsiveContainer width="100%" height={200} minHeight={200}>
              <LineChart data={portfolio.performanceHistory}>
                <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
                <XAxis dataKey="date" stroke="#94a3b8" />
                <YAxis stroke="#94a3b8" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #475569' }}
                  labelStyle={{ color: '#fff' }}
                />
                <Legend />
                <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} dot={false} name="Portfolio Value" />
                <Line type="monotone" dataKey="pnl" stroke="#10b981" strokeWidth={2} dot={false} name="P&L" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Holdings Breakdown */}
          <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
            <h3 className="text-base lg:text-xl font-bold mb-3 lg:mb-4">Portfolio Distribution</h3>
            <ResponsiveContainer width="100%" height={200} minHeight={200}>
              <PieChart>
                <Pie
                  data={portfolio.positions}
                  dataKey="value"
                  nameKey="symbol"
                  cx="50%"
                  cy="50%"
                  outerRadius={100}
                  label
                >
                  {portfolio.positions?.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #475569' }}
                  labelStyle={{ color: '#fff' }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Open Positions */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:gap-8">
          {/* Gainers */}
          <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
            <h3 className="text-base lg:text-xl font-bold mb-3 lg:mb-4 flex items-center gap-2 text-green-400">
              <TrendingUp className="w-5 lg:w-6 h-5 lg:h-6" />
              Gainers ({gainers.length})
            </h3>

            <div className="space-y-2 lg:space-y-3 max-h-80 overflow-y-auto">
              {gainers.length === 0 ? (
                <p className="text-slate-400 text-center py-8 text-sm">No gainers</p>
              ) : (
                gainers.map(position => (
                  <div key={position.symbol} className="bg-slate-600 rounded p-3 lg:p-4">
                    <div className="flex justify-between items-start mb-1 lg:mb-2 gap-2">
                      <div>
                        <p className="font-bold text-sm lg:text-base">{position.symbol}</p>
                        <p className="text-xs lg:text-sm text-slate-300">{position.quantity} @ ₹{position.avgCost}</p>
                      </div>
                      <div className="text-right flex-shrink-0">
                        <p className="font-bold text-green-400 text-xs lg:text-base">+₹{position.pnl?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                        <p className="text-xs lg:text-sm text-green-400">+{position.pnlPercent?.toFixed(2)}%</p>
                      </div>
                    </div>
                    <div className="w-full bg-slate-700 rounded h-2">
                      <div
                        className="bg-green-500 h-2 rounded"
                        style={{width: `${Math.min((position.pnlPercent / 20) * 100, 100)}%`}}
                      />
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Losers */}
          <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
            <h3 className="text-base lg:text-xl font-bold mb-3 lg:mb-4 flex items-center gap-2 text-red-400">
              <TrendingDown className="w-5 lg:w-6 h-5 lg:h-6" />
              Losers ({losers.length})
            </h3>

            <div className="space-y-2 lg:space-y-3 max-h-80 overflow-y-auto">
              {losers.length === 0 ? (
                <p className="text-slate-400 text-center py-8 text-sm">No losers</p>
              ) : (
                losers.map(position => (
                  <div key={position.symbol} className="bg-slate-600 rounded p-3 lg:p-4">
                    <div className="flex justify-between items-start mb-1 lg:mb-2 gap-2">
                      <div>
                        <p className="font-bold text-sm lg:text-base">{position.symbol}</p>
                        <p className="text-xs lg:text-sm text-slate-300">{position.quantity} @ ₹{position.avgCost}</p>
                      </div>
                      <div className="text-right flex-shrink-0">
                        <p className="font-bold text-red-400 text-xs lg:text-base">-₹{Math.abs(position.pnl)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                        <p className="text-xs lg:text-sm text-red-400">{position.pnlPercent?.toFixed(2)}%</p>
                      </div>
                    </div>
                    <div className="w-full bg-slate-700 rounded h-2">
                      <div
                        className="bg-red-500 h-2 rounded"
                        style={{width: `${Math.min(Math.abs(position.pnlPercent / 20) * 100, 100)}%`}}
                      />
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
