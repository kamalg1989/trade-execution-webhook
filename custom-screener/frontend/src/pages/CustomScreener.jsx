import React, { useEffect, useRef, useState } from 'react';
import { getSnapshot, runFilter, computeDate, computeStatus, scoreIfp } from '../api/client.js';
import FilterPanel from '../components/FilterPanel.jsx';
import MarketSnapshot from '../components/MarketSnapshot.jsx';
import ResultsTable from '../components/ResultsTable.jsx';
import ChartModal from '../components/ChartModal.jsx';
import ExportCsvButton from '../components/ExportCsvButton.jsx';
import AiAnalysisPanel from '../components/AiAnalysisPanel.jsx';

const EMPTY = { sma200: 'any', sma50: 'any', ema50: 'any' };

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
  const [fetching, setFetching] = useState(false);
  const [fetchMsg, setFetchMsg] = useState('');
  const pollRef = useRef(null);
  const [ifp, setIfp] = useState({ lookback: 100, volMult: 1.5, closePos: 0.6 });
  const [ifpLoading, setIfpLoading] = useState(false);
  const [ifpMsg, setIfpMsg] = useState('');

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
      // ifp column defaults to the precomputed score; tunable panel can override.
      setRows(res.results.map((r) => ({ ...r, ifp: r.ifpScore, ifpCustom: null })));
      setMatchCount(res.matchCount);
      setIfpMsg('');
    } catch (e) {
      setError(`Filter: ${e.message}`);
      setRows([]);
      setMatchCount(null);
    } finally {
      setLoading(false);
    }
  };

  const reset = () => { setFilters(EMPTY); setIncludeInsufficient(false); };

  // Tunable IFP recompute over the current (filtered) result symbols.
  const scoreIfpOnResults = async () => {
    if (!rows.length) return;
    setIfpLoading(true);
    setIfpMsg('');
    try {
      const res = await scoreIfp({
        symbols: rows.map((r) => r.symbol),
        indicatorDate: date || null,
        lookback: Number(ifp.lookback), volMult: Number(ifp.volMult), closePos: Number(ifp.closePos),
      });
      const map = Object.fromEntries(res.results.map((r) => [r.symbol, r.ifpScore]));
      setRows((rs) => rs.map((r) => {
        const c = map[r.symbol];
        return { ...r, ifpCustom: c ?? null, ifp: c ?? r.ifpScore };
      }));
      setIfpMsg(`IFP recomputed for ${res.count} stocks (lookback ${ifp.lookback}, vol ${ifp.volMult}×, close ${ifp.closePos}). Sort by IFP column; * = custom.`);
    } catch (e) {
      setIfpMsg(`IFP: ${e.message}`);
    } finally {
      setIfpLoading(false);
    }
  };

  const onDateChange = (e) => {
    const d = e.target.value;
    setDate(d);
    loadSnapshot(d);
  };

  // Manual "fetch data for this date" — triggers a compute, polls until ready.
  const fetchThisDate = async () => {
    if (!date || fetching) return;
    setError('');
    setFetching(true);
    setFetchMsg(`Fetching data for ${date}… this takes a few minutes.`);
    try {
      await computeDate(date);
    } catch (e) {
      setFetching(false);
      setFetchMsg('');
      setError(`Fetch: ${e.message}`);
      return;
    }
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await computeStatus(date);
        if (s.ready) {
          clearInterval(pollRef.current);
          setFetching(false);
          setFetchMsg(`Data ready for ${date}.`);
          loadSnapshot(date);
        } else if (!s.running) {
          clearInterval(pollRef.current);
          setFetching(false);
          setFetchMsg('');
          setError(`Fetch finished but no complete data for ${date} (market holiday or partial data?).`);
        }
      } catch { /* keep polling */ }
    }, 8000);
  };

  useEffect(() => () => clearInterval(pollRef.current), []);

  return (
    <div className="min-h-screen text-slate-100 p-4 sm:p-6 max-w-7xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Custom Screener</h1>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            Date
            <input type="date" value={date} onChange={onDateChange}
              className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-100" />
          </label>
          <button onClick={fetchThisDate} disabled={fetching || !date}
            className="px-3 py-1.5 text-sm rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-semibold whitespace-nowrap">
            {fetching ? 'Fetching…' : 'Fetch this date'}
          </button>
        </div>
      </div>

      {fetchMsg && <div className="bg-emerald-900/30 border border-emerald-700 text-emerald-200 text-sm rounded px-3 py-2">{fetchMsg}</div>}
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

      {/* Tunable IFP — recompute on the current filtered subset */}
      {rows.length > 0 && (
        <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="text-[11px] uppercase tracking-wide text-slate-500 w-full sm:w-auto">Tune IFP on these {rows.length} stocks</div>
            <label className="flex flex-col text-xs text-slate-300 gap-1">IFP days
              <input type="number" min="10" max="300" value={ifp.lookback}
                onChange={(e) => setIfp({ ...ifp, lookback: e.target.value })}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 w-24 text-slate-100" />
            </label>
            <label className="flex flex-col text-xs text-slate-300 gap-1">Vol surge ×
              <input type="number" step="0.1" min="1" value={ifp.volMult}
                onChange={(e) => setIfp({ ...ifp, volMult: e.target.value })}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 w-24 text-slate-100" />
            </label>
            <label className="flex flex-col text-xs text-slate-300 gap-1">Close pos (0–1)
              <input type="number" step="0.05" min="0" max="1" value={ifp.closePos}
                onChange={(e) => setIfp({ ...ifp, closePos: e.target.value })}
                className="bg-slate-800 border border-slate-600 rounded px-2 py-1 w-24 text-slate-100" />
            </label>
            <button onClick={scoreIfpOnResults} disabled={ifpLoading}
              className="px-4 py-1.5 text-sm rounded bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white font-semibold">
              {ifpLoading ? 'Scoring…' : 'Recompute IFP'}
            </button>
          </div>
          {ifpMsg && <div className="text-xs text-slate-400 mt-2">{ifpMsg}</div>}
        </div>
      )}

      {rows.length > 0 && (
        <AiAnalysisPanel symbols={rows.map((r) => r.symbol)} date={date} />
      )}

      <ResultsTable rows={rows} onPick={setPicked} />

      <ChartModal symbol={picked?.symbol} open={!!picked} onClose={() => setPicked(null)} />
    </div>
  );
}
