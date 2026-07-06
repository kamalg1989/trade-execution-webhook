import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Eye, EyeOff } from 'lucide-react';

export default function ProfitLossTrackerMobile() {
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

  if (loading) return <div className="p-4 text-center text-slate-400">Loading...</div>;

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen">
      {/* Summary Cards */}
      <div className="px-4 py-4 space-y-2">
        {/* Invested */}
        <div className="bg-slate-700 rounded-lg p-3">
          <div className="flex justify-between items-center">
            <span className="text-xs text-slate-400">Total Invested</span>
            <button onClick={() => setShowBalance(!showBalance)} className="text-slate-400">
              {showBalance ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
            </button>
          </div>
          <p className="text-xl font-bold mt-1">
            {showBalance ? `₹${portfolio.totalInvested?.toLocaleString('en-IN', {maximumFractionDigits: 0})}` : '••••'}
          </p>
        </div>

        {/* Current Value */}
        <div className="bg-slate-700 rounded-lg p-3">
          <p className="text-xs text-slate-400">Current Value</p>
          <p className="text-xl font-bold text-blue-400 mt-1">
            {showBalance ? `₹${portfolio.totalValue?.toLocaleString('en-IN', {maximumFractionDigits: 0})}` : '••••'}
          </p>
        </div>

        {/* P&L */}
        <div className={`rounded-lg p-3 ${totalPnL >= 0 ? 'bg-green-900' : 'bg-red-900'}`}>
          <div className="flex justify-between items-center">
            <span className="text-xs text-slate-300">Unrealized P&L</span>
            <span className={`text-sm font-bold ${totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {totalPnLPercent}%
            </span>
          </div>
          <p className={`text-xl font-bold mt-1 ${totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {showBalance ? (
              <>
                {totalPnL >= 0 ? '+' : ''}₹{Math.abs(totalPnL)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}
              </>
            ) : '••••'}
          </p>
        </div>
      </div>

      {/* Timeframe Filter */}
      <div className="px-4 py-3 border-t border-slate-700 flex gap-1 justify-center">
        {['1w', '1m', '3m', '1y'].map(period => (
          <button
            key={period}
            onClick={() => setTimeframe(period)}
            className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
              timeframe === period
                ? 'bg-blue-600 text-white'
                : 'bg-slate-700 text-slate-300'
            }`}
          >
            {period}
          </button>
        ))}
      </div>

      {/* Gainers */}
      <div className="px-4 py-4">
        <h3 className="text-sm font-bold mb-2 flex items-center gap-2 text-green-400">
          <TrendingUp className="w-4 h-4" />
          Gainers ({gainers.length})
        </h3>
        <div className="space-y-2">
          {gainers.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">No gainers</p>
          ) : (
            gainers.slice(0, 5).map(position => (
              <div key={position.symbol} className="bg-slate-700 rounded p-3">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <p className="font-bold text-sm">{position.symbol}</p>
                    <p className="text-xs text-slate-300">{position.quantity} @ ₹{position.avgCost}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-green-400 text-sm">+₹{position.pnl?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                    <p className="text-xs text-green-400">+{position.pnlPercent?.toFixed(1)}%</p>
                  </div>
                </div>
                <div className="w-full bg-slate-600 rounded h-1.5">
                  <div
                    className="bg-green-500 h-1.5 rounded"
                    style={{width: `${Math.min((position.pnlPercent / 20) * 100, 100)}%`}}
                  />
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Losers */}
      <div className="px-4 py-4 border-t border-slate-700">
        <h3 className="text-sm font-bold mb-2 flex items-center gap-2 text-red-400">
          <TrendingDown className="w-4 h-4" />
          Losers ({losers.length})
        </h3>
        <div className="space-y-2">
          {losers.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">No losers</p>
          ) : (
            losers.slice(0, 5).map(position => (
              <div key={position.symbol} className="bg-slate-700 rounded p-3">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <p className="font-bold text-sm">{position.symbol}</p>
                    <p className="text-xs text-slate-300">{position.quantity} @ ₹{position.avgCost}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-red-400 text-sm">-₹{Math.abs(position.pnl)?.toLocaleString('en-IN', {maximumFractionDigits: 0})}</p>
                    <p className="text-xs text-red-400">{position.pnlPercent?.toFixed(1)}%</p>
                  </div>
                </div>
                <div className="w-full bg-slate-600 rounded h-1.5">
                  <div
                    className="bg-red-500 h-1.5 rounded"
                    style={{width: `${Math.min(Math.abs(position.pnlPercent / 20) * 100, 100)}%`}}
                  />
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
