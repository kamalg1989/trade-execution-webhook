import React, { useEffect, useState } from 'react';
import { getSnapshot, runFilter } from '../api/client.js';
import FilterPanel from '../components/FilterPanel.jsx';
import MarketSnapshot from '../components/MarketSnapshot.jsx';
import ResultsTable from '../components/ResultsTable.jsx';
import ChartModal from '../components/ChartModal.jsx';
import ExportCsvButton from '../components/ExportCsvButton.jsx';

const EMPTY = { sma200: 'any', sma50: 'any' };

export default function CustomScreener() {
  const [date, setDate] = useState('');
  const [snap, setSnap] = useState(null);
  const [filters, setFilters] = useState(EMPTY);
  const [includeInsufficient, setIncludeInsufficient] = useState(false);
  const [rows, setRows] = useState([]);
  const [matchCount, setMatchCount] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState(null);

  const loadSnapshot = async (d) => {
    setError('');
    try {
      const s = await getSnapshot(d || undefined);
      setSnap(s);
      if (!d) setDate(s.snapshotDate);
    } catch (e) {
      setSnap(null);
      setError(`Snapshot: ${e.message}`);
    }
  };

  useEffect(() => { loadSnapshot(''); }, []);

  const buildPayload = () => {
    const f = { ...filters };
    // normalize % dropdowns ({min}/{max}) already stored as objects; direction defaults
    return {
      indicatorDate: date || null,
      includeInsufficientHistory: includeInsufficient,
      filters: f,
      sort: { by: 'pct_chg_1m', order: 'DESC' },
    };
  };

  const apply = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await runFilter(buildPayload());
      setRows(res.results);
      setMatchCount(res.matchCount);
    } catch (e) {
      setError(`Filter: ${e.message}`);
      setRows([]);
      setMatchCount(null);
    } finally {
      setLoading(false);
    }
  };

  const reset = () => { setFilters(EMPTY); setIncludeInsufficient(false); };

  const onDateChange = (e) => {
    const d = e.target.value;
    setDate(d);
    loadSnapshot(d);
  };

  return (
    <div className="min-h-screen text-slate-100 p-4 sm:p-6 max-w-7xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Custom Screener</h1>
        <label className="flex items-center gap-2 text-sm text-slate-300">
          Date
          <input type="date" value={date} onChange={onDateChange}
            className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100" />
        </label>
      </div>

      {error && <div className="bg-red-900/40 border border-red-700 text-red-200 text-sm rounded px-3 py-2">{error}</div>}

      <MarketSnapshot snap={snap} />

      <FilterPanel
        filters={filters} setFilters={setFilters}
        includeInsufficient={includeInsufficient} setIncludeInsufficient={setIncludeInsufficient}
        onApply={apply} onReset={reset} loading={loading}
      />

      <div className="flex items-center justify-between">
        <div className="text-sm text-slate-300">
          {matchCount == null ? 'Apply filters to see results' : `${matchCount} stocks`}
        </div>
        <ExportCsvButton rows={rows} date={date} />
      </div>

      <ResultsTable rows={rows} onPick={setPicked} />

      <ChartModal symbol={picked?.symbol} open={!!picked} onClose={() => setPicked(null)} />
    </div>
  );
}
