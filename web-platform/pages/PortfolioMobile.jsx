import React, { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip,
  PieChart, Pie, Cell,
} from 'recharts';

const fmtMoney = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const fmtMoneyAbs = (v) => v == null ? '—' : `₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
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

const GRADE_COLORS = {
  'A+': 'bg-emerald-900/50 text-emerald-300 border-emerald-800/40',
  'A': 'bg-blue-900/50 text-blue-300 border-blue-800/40',
  'B': 'bg-amber-900/50 text-amber-300 border-amber-800/40',
  'C': 'bg-red-900/50 text-red-300 border-red-800/40',
  'Quant': 'bg-purple-900/50 text-purple-300 border-purple-800/40',
};
const DONUT_COLORS = ['#60a5fa', '#a78bfa', '#f59e0b', '#34d399', '#f87171', '#94a3b8'];

const KPI = ({ label, value, valueClass = '', sub }) => (
  <div className="bg-slate-700 rounded-lg p-2">
    <p className="text-slate-400 text-[9px]">{label}</p>
    <p className={`text-sm font-bold truncate ${valueClass}`}>{value}</p>
    {sub && <p className="text-[9px] text-slate-500">{sub}</p>}
  </div>
);

function RankedReturnList({ holdings }) {
  if (holdings.length === 0) return null;
  const sorted = [...holdings].sort((a, b) => (b.pnlPercent ?? 0) - (a.pnlPercent ?? 0));
  const maxAbs = Math.max(1, ...sorted.map(h => Math.abs(h.pnlPercent ?? 0)));
  return (
    <div className="bg-slate-700 rounded-lg p-3 mb-2">
      <div className="flex items-center gap-3 mb-2 text-[9px] text-slate-400">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-green-500 inline-block" />Profit</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500 inline-block" />Loss</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />OHM</span>
      </div>
      <div className="space-y-1">
        {sorted.map(h => {
          const pct = h.pnlPercent ?? 0;
          const isPos = pct >= 0;
          const width = Math.abs(pct) / maxAbs * 50;
          return (
            <div key={h.securityId || h.symbol} className="grid items-center gap-1.5 text-[11px]" style={{ gridTemplateColumns: '72px 1fr 56px' }}>
              <span className="flex items-center gap-1 font-semibold truncate">
                {h.reason ? <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block shrink-0" /> : <span className="w-1.5 inline-block shrink-0" />}
                {h.symbol}
              </span>
              <span className="relative h-3 bg-slate-800 rounded overflow-hidden">
                <span className="absolute top-0 bottom-0 w-px bg-slate-500" style={{ left: '50%' }} />
                <span
                  className={`absolute top-0 bottom-0 ${isPos ? 'bg-green-500' : 'bg-red-500'}`}
                  style={isPos ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
                />
              </span>
              <span className={`text-right font-semibold ${pnlClass(pct)}`}>{isPos ? '+' : ''}{pct.toFixed(1)}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EquityCurve({ curve }) {
  if (!curve || curve.length === 0) {
    return (
      <div className="bg-slate-700 rounded-lg p-4 mb-2 text-center text-slate-400 text-xs">
        No closed trades yet — fills in as trades close.
      </div>
    );
  }
  const isUp = curve[curve.length - 1].cumulativePnl >= 0;
  return (
    <div className="bg-slate-700 rounded-lg p-3 mb-2">
      <p className="text-slate-400 text-[9px] mb-1">Equity curve</p>
      <div style={{ width: '100%', height: 150 }}>
        <ResponsiveContainer>
          <AreaChart data={curve} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="equityFillM" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={isUp ? '#4ade80' : '#f87171'} stopOpacity={0.35} />
                <stop offset="100%" stopColor={isUp ? '#4ade80' : '#f87171'} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#94a3b8' }} tickFormatter={(d) => new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} />
            <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} width={44} tickFormatter={(v) => `₹${v}`} />
            <Tooltip
              contentStyle={{ background: '#1e293b', border: '1px solid #475569', borderRadius: 8, fontSize: 11 }}
              labelFormatter={(d) => fmtDate(d)}
              formatter={(v) => [fmtMoney(v), 'Cumulative']}
            />
            <Area type="monotone" dataKey="cumulativePnl" stroke={isUp ? '#4ade80' : '#f87171'} strokeWidth={2} fill="url(#equityFillM)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function QualityGrades({ byQualityGrade }) {
  if (!byQualityGrade || byQualityGrade.length === 0) return null;
  return (
    <div className="bg-slate-700 rounded-lg p-3 mb-2">
      <p className="text-slate-400 text-[9px] mb-1.5">By setup quality</p>
      <div className="grid grid-cols-2 gap-1.5">
        {byQualityGrade.map(g => (
          <div key={g.label} className={`rounded-lg border p-2 ${GRADE_COLORS[g.label] || GRADE_COLORS.Quant}`}>
            <p className="text-xs font-bold">{g.label}</p>
            <p className={`text-xs font-semibold ${pnlClass(g.pnl)}`}>{fmtMoney(g.pnl)}</p>
            <p className="text-[9px] opacity-80">{g.count} · {g.winRate}%</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function RegimeDonut({ byRegime }) {
  if (!byRegime || byRegime.length === 0) return null;
  return (
    <div className="bg-slate-700 rounded-lg p-3 mb-2">
      <p className="text-slate-400 text-[9px] mb-1.5">By market regime</p>
      <div className="flex items-center gap-3">
        <div style={{ width: 84, height: 84 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={byRegime} dataKey="count" nameKey="label" innerRadius={26} outerRadius={40} paddingAngle={2}>
                {byRegime.map((_, i) => <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />)}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1">
          {byRegime.map((r, i) => (
            <div key={r.label} className="flex items-center justify-between text-[10px]">
              <span className="flex items-center gap-1 truncate">
                <span className="w-1.5 h-1.5 rounded-sm inline-block shrink-0" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
                {r.label}
              </span>
              <span className={`font-semibold ${pnlClass(r.pnl)}`}>{fmtMoney(r.pnl)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function PortfolioMobile() {
  const [holdings, setHoldings] = useState([]);
  const [closedTrades, setClosedTrades] = useState([]);
  const [insights, setInsights] = useState(null);
  const [journal, setJournal] = useState(null);
  const [openSummary, setOpenSummary] = useState(null);
  const [view, setView] = useState('open'); // 'open' | 'journal'
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
      setJournal(data.journal || null);
      setOpenSummary(data.openSummary || null);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch portfolio:', error);
      setLoading(false);
    }
  };

  const exportCSV = () => {
    const data = view === 'open' ? holdings : closedTrades;
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
    a.download = `portfolio-${view}-${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
  };

  if (loading) return <div className="p-4 text-center text-slate-400">Loading...</div>;

  const data = view === 'open' ? holdings : closedTrades;

  return (
    <div className="bg-gradient-to-br from-slate-900 to-slate-800 text-white min-h-screen">
      {/* View toggle */}
      <div className="flex border-b border-slate-700 sticky top-0 bg-slate-800 z-10">
        <button
          onClick={() => setView('open')}
          className={`flex-1 px-4 py-3 font-semibold text-sm text-center border-b-2 transition-all ${
            view === 'open' ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400'
          }`}
        >
          Open ({holdings.length})
        </button>
        <button
          onClick={() => setView('journal')}
          className={`flex-1 px-4 py-3 font-semibold text-sm text-center border-b-2 transition-all ${
            view === 'journal' ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400'
          }`}
        >
          Journal ({closedTrades.length})
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

      {/* ===================== OPEN VIEW ===================== */}
      {view === 'open' && (
        <div className="px-4 pt-3">
          {openSummary && (
            <div className="grid grid-cols-4 gap-1.5 mb-2">
              <KPI label="Value" value={fmtMoneyAbs(openSummary.totalValue)} />
              <KPI label="Unrealized" value={fmtMoney(openSummary.unrealizedPnL)} valueClass={pnlClass(openSummary.unrealizedPnL)} />
              <KPI label="Invested" value={fmtMoneyAbs(openSummary.totalInvested)} />
              <KPI label="Positions" value={openSummary.count} />
            </div>
          )}
          <RankedReturnList holdings={holdings} />
        </div>
      )}

      {/* ===================== JOURNAL VIEW ===================== */}
      {view === 'journal' && journal && (
        <div className="px-4 pt-3">
          <div className="grid grid-cols-4 gap-1.5 mb-1.5">
            <KPI label="Total P&L" value={fmtMoney(journal.totalPnl)} valueClass={pnlClass(journal.totalPnl)} />
            <KPI label="Peak Equity" value={fmtMoneyAbs(journal.peakEquity)} />
            <KPI label="Max DD" value={journal.maxDrawdown ? `-${fmtMoneyAbs(journal.maxDrawdown)}` : '₹0'} valueClass={journal.maxDrawdown ? 'text-red-400' : ''} />
            <KPI label="Days" value={journal.tradingDays} />
          </div>
          <div className="grid grid-cols-4 gap-1.5 mb-2">
            <KPI label="Win Rate" value={`${journal.dailyWinRate.pct}%`} valueClass={journal.dailyWinRate.pct >= 50 ? 'text-green-400' : 'text-red-400'} />
            <KPI label="Consistency" value={journal.consistency.pct != null ? `${journal.consistency.pct}%` : '—'} sub={journal.consistency.label} />
            <KPI label="Win Streak" value={journal.bestWinStreak} valueClass="text-green-400" />
            <KPI label="Loss Streak" value={journal.worstLossStreak} valueClass={journal.worstLossStreak > 0 ? 'text-red-400' : ''} />
          </div>
          <EquityCurve curve={journal.equityCurve} />
          <QualityGrades byQualityGrade={journal.byQualityGrade} />
          <RegimeDonut byRegime={journal.byRegime} />
          {insights && insights.totalClosed > 0 && (insights.quantOnly.count > 0 || insights.aiReviewed.count > 0) && (
            <div className="grid grid-cols-2 gap-1.5 mb-2">
              <div className="bg-slate-700 rounded-lg p-2">
                <p className="text-slate-400 text-[9px]">📐 Quant-only</p>
                <p className="text-xs font-bold">{insights.quantOnly.winRate}% <span className="text-slate-500 font-normal">· {insights.quantOnly.count}</span></p>
              </div>
              <div className="bg-slate-700 rounded-lg p-2">
                <p className="text-slate-400 text-[9px]">🤖 AI-reviewed</p>
                <p className="text-xs font-bold">{insights.aiReviewed.winRate}% <span className="text-slate-500 font-normal">· {insights.aiReviewed.count}</span></p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Data Cards */}
      <div className="px-4 py-4">
        {data.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-slate-400 text-sm">No {view === 'open' ? 'open positions' : 'closed trades yet'}</p>
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
                  {view === 'open' ? (
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
                  {view === 'open'
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
