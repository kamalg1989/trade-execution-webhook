import React, { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  PieChart, Pie, Cell,
} from 'recharts';

const fmtMoney = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
const fmtMoneyAbs = (v) => v == null ? '—' : `₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
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

const GRADE_COLORS = {
  'A+': 'bg-emerald-900/50 text-emerald-300 border-emerald-800/40',
  'A': 'bg-blue-900/50 text-blue-300 border-blue-800/40',
  'B': 'bg-amber-900/50 text-amber-300 border-amber-800/40',
  'C': 'bg-red-900/50 text-red-300 border-red-800/40',
  'Quant': 'bg-purple-900/50 text-purple-300 border-purple-800/40',
};
const DONUT_COLORS = ['#60a5fa', '#a78bfa', '#f59e0b', '#34d399', '#f87171', '#94a3b8'];

const KPI = ({ label, value, valueClass = '', sub }) => (
  <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
    <p className="text-slate-400 text-xs mb-1">{label}</p>
    <p className={`text-lg lg:text-2xl font-bold ${valueClass}`}>{value}</p>
    {sub && <p className="text-[11px] text-slate-500 mt-0.5">{sub}</p>}
  </div>
);

function RankedReturnList({ holdings }) {
  if (holdings.length === 0) return null;
  const sorted = [...holdings].sort((a, b) => (b.pnlPercent ?? 0) - (a.pnlPercent ?? 0));
  const maxAbs = Math.max(1, ...sorted.map(h => Math.abs(h.pnlPercent ?? 0)));
  return (
    <div className="bg-slate-700 rounded-lg p-3 lg:p-4 mb-6">
      <div className="flex items-center gap-4 mb-3 text-[11px] text-slate-400">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-green-500 inline-block" />Profit</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-red-500 inline-block" />Loss</span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />OHM-tracked entry</span>
      </div>
      <div className="space-y-1">
        {sorted.map(h => {
          const pct = h.pnlPercent ?? 0;
          const isPos = pct >= 0;
          const width = Math.abs(pct) / maxAbs * 50;
          return (
            <div key={h.securityId || h.symbol} className="grid items-center gap-2 text-xs" style={{ gridTemplateColumns: '96px 1fr 72px' }}>
              <span className="flex items-center gap-1 font-semibold truncate">
                {h.reason ? <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block shrink-0" /> : <span className="w-1.5 inline-block shrink-0" />}
                {h.symbol}
              </span>
              <span className="relative h-3.5 bg-slate-800 rounded overflow-hidden">
                <span className="absolute top-0 bottom-0 w-px bg-slate-500" style={{ left: '50%' }} />
                <span
                  className={`absolute top-0 bottom-0 ${isPos ? 'bg-green-500' : 'bg-red-500'}`}
                  style={isPos ? { left: '50%', width: `${width}%` } : { right: '50%', width: `${width}%` }}
                />
              </span>
              <span className={`text-right font-semibold ${pnlClass(pct)}`}>{fmtPct(pct)}</span>
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
      <div className="bg-slate-700 rounded-lg p-6 mb-6 text-center text-slate-400 text-sm">
        No closed trades yet — the equity curve fills in as trades close.
      </div>
    );
  }
  const isUp = curve[curve.length - 1].cumulativePnl >= 0;
  return (
    <div className="bg-slate-700 rounded-lg p-3 lg:p-4 mb-6">
      <p className="text-slate-400 text-xs mb-2">Equity curve (cumulative realized P&L)</p>
      <div style={{ width: '100%', height: 220 }}>
        <ResponsiveContainer>
          <AreaChart data={curve} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={isUp ? '#4ade80' : '#f87171'} stopOpacity={0.35} />
                <stop offset="100%" stopColor={isUp ? '#4ade80' : '#f87171'} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#475569" vertical={false} />
            <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={(d) => new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} />
            <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={(v) => `₹${v}`} width={64} />
            <Tooltip
              contentStyle={{ background: '#1e293b', border: '1px solid #475569', borderRadius: 8, fontSize: 12 }}
              labelFormatter={(d) => fmtDate(d)}
              formatter={(v, name) => [fmtMoney(v), name === 'cumulativePnl' ? 'Cumulative P&L' : 'Day P&L']}
            />
            <Area type="monotone" dataKey="cumulativePnl" stroke={isUp ? '#4ade80' : '#f87171'} strokeWidth={2} fill="url(#equityFill)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function RegimeDonut({ byRegime }) {
  if (!byRegime || byRegime.length === 0) return null;
  const total = byRegime.reduce((s, r) => s + r.pnl, 0);
  return (
    <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
      <p className="text-slate-400 text-xs mb-2">P&L by market regime</p>
      <div className="flex items-center gap-4">
        <div style={{ width: 120, height: 120 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={byRegime} dataKey="count" nameKey="label" innerRadius={36} outerRadius={56} paddingAngle={2}>
                {byRegime.map((_, i) => <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />)}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex-1 space-y-1.5">
          {byRegime.map((r, i) => (
            <div key={r.label} className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 truncate">
                <span className="w-2 h-2 rounded-sm inline-block shrink-0" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
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

function QualityGrades({ byQualityGrade }) {
  if (!byQualityGrade || byQualityGrade.length === 0) return null;
  return (
    <div className="bg-slate-700 rounded-lg p-3 lg:p-4">
      <p className="text-slate-400 text-xs mb-2">Performance by setup quality</p>
      <div className="grid grid-cols-2 gap-2">
        {byQualityGrade.map(g => (
          <div key={g.label} className={`rounded-lg border p-2.5 ${GRADE_COLORS[g.label] || GRADE_COLORS.Quant}`}>
            <p className="text-sm font-bold">{g.label}</p>
            <p className={`text-sm font-semibold ${pnlClass(g.pnl)}`}>{fmtMoney(g.pnl)}</p>
            <p className="text-[10px] opacity-80">{g.count} trade{g.count === 1 ? '' : 's'} · {g.winRate}% win</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Portfolio() {
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
    const headers = Object.keys(data[0] || {});
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

  if (loading) return <div className="p-8">Loading portfolio data...</div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex flex-wrap justify-between items-start gap-4 mb-6 lg:mb-8">
          <div>
            <h1 className="text-2xl lg:text-4xl font-bold mb-1 lg:mb-2">💼 Portfolio</h1>
            <p className="text-xs lg:text-base text-slate-400">Open positions & closed-trade journal</p>
          </div>
          <button
            onClick={exportCSV}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg font-semibold transition-all"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>

        {/* View toggle */}
        <div className="inline-flex bg-slate-700 rounded-lg p-1 mb-6 lg:mb-8">
          <button
            onClick={() => setView('open')}
            className={`px-4 py-2 rounded-md text-sm font-semibold transition-all ${view === 'open' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white'}`}
          >
            Open Positions ({holdings.length})
          </button>
          <button
            onClick={() => setView('journal')}
            className={`px-4 py-2 rounded-md text-sm font-semibold transition-all ${view === 'journal' ? 'bg-blue-600 text-white' : 'text-slate-300 hover:text-white'}`}
          >
            Journal — Closed ({closedTrades.length})
          </button>
        </div>

        {/* ===================== OPEN POSITIONS VIEW ===================== */}
        {view === 'open' && (
          <>
            {openSummary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4 mb-6 lg:mb-8">
                <KPI label="Portfolio Value" value={fmtMoneyAbs(openSummary.totalValue)} />
                <KPI
                  label="Unrealized P&L"
                  value={fmtMoney(openSummary.unrealizedPnL)}
                  valueClass={pnlClass(openSummary.unrealizedPnL)}
                  sub={fmtPct(openSummary.unrealizedPnLPct)}
                />
                <KPI label="Invested" value={fmtMoneyAbs(openSummary.totalInvested)} />
                <KPI label="Open Positions" value={openSummary.count} />
              </div>
            )}

            <RankedReturnList holdings={holdings} />

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
          </>
        )}

        {/* ===================== JOURNAL / CLOSED VIEW ===================== */}
        {view === 'journal' && journal && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4 mb-4">
              <KPI label="Total P&L" value={fmtMoney(journal.totalPnl)} valueClass={pnlClass(journal.totalPnl)} />
              <KPI label="Peak Equity" value={fmtMoneyAbs(journal.peakEquity)} />
              <KPI label="Max Drawdown" value={journal.maxDrawdown ? `-${fmtMoneyAbs(journal.maxDrawdown)}` : '₹0'} valueClass={journal.maxDrawdown ? 'text-red-400' : ''} sub={journal.maxDrawdownPct ? `${journal.maxDrawdownPct}%` : null} />
              <KPI label="Trading Days" value={journal.tradingDays} />
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4 mb-6 lg:mb-8">
              <KPI
                label="Daily Win Rate"
                value={`${journal.dailyWinRate.pct}%`}
                valueClass={journal.dailyWinRate.pct >= 50 ? 'text-green-400' : 'text-red-400'}
                sub={`${journal.dailyWinRate.wins}W / ${journal.dailyWinRate.losses}L`}
              />
              <KPI
                label="Consistency"
                value={journal.consistency.pct != null ? `${journal.consistency.pct}%` : '—'}
                sub={journal.consistency.label}
              />
              <KPI label="Best Win Streak" value={journal.bestWinStreak} valueClass="text-green-400" sub="Consecutive wins" />
              <KPI label="Worst Loss Streak" value={journal.worstLossStreak} valueClass={journal.worstLossStreak > 0 ? 'text-red-400' : ''} sub="Consecutive losses" />
            </div>

            <EquityCurve curve={journal.equityCurve} />

            {(journal.byQualityGrade.length > 0 || journal.byRegime.length > 0) && (
              <div className="grid md:grid-cols-2 gap-4 mb-6 lg:mb-8">
                <QualityGrades byQualityGrade={journal.byQualityGrade} />
                <RegimeDonut byRegime={journal.byRegime} />
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
          </>
        )}
      </div>
    </div>
  );
}
