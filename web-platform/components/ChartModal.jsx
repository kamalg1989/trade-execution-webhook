import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, ZoomIn, ZoomOut, Loader } from 'lucide-react';
import { useSwipeToClose } from '../utils/useSwipeToClose';

// Make the upstream SVG responsive: ensure a viewBox, strip fixed width/height
// so CSS controls sizing, and force it to fill its container exactly (no
// letterboxing). We now request a viewBox sized to match the actual
// on-screen container (see width/height query params below), so
// preserveAspectRatio="none" is mostly a safety net for rounding, not a
// real distortion.
export function makeResponsive(svg) {
  if (!svg || svg[0] !== '<') return svg;
  if (!/viewBox=/i.test(svg)) {
    const w = svg.match(/width="(\d+(?:\.\d+)?)"/);
    const h = svg.match(/height="(\d+(?:\.\d+)?)"/);
    if (w && h) svg = svg.replace(/<svg/i, `<svg viewBox="0 0 ${w[1]} ${h[1]}"`);
  }
  return svg
    .replace(/(<svg[^>]*?)\s+width="[^"]*"/i, '$1')
    .replace(/(<svg[^>]*?)\s+height="[^"]*"/i, '$1')
    .replace(/<svg([^>]*)>/i, (m, attrs) =>
      /preserveAspectRatio=/i.test(attrs) ? m : `<svg${attrs} preserveAspectRatio="none">`);
}

const RANGES = [
  { key: '3M', days: 92 },
  { key: '6M', days: 183 },
  { key: '1Y', days: 366 },
  { key: '2Y', days: 731 },
  { key: '5Y', days: 1827 },
];

function fromDate(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function ChartModal({ symbol, open, onClose }) {
  const [chartType, setChartType] = useState('daily');
  // Daily and weekly keep independent range choices — 6M of daily candles
  // is a sensible default view, but 6M of *weekly* candles is only ~26
  // bars, too few to be useful, so weekly defaults to 1Y instead.
  const [dailyRange, setDailyRange] = useState('6M');
  const [weeklyRange, setWeeklyRange] = useState('1Y');
  const range = chartType === 'weekly' ? weeklyRange : dailyRange;
  // Follow the app's global light/dark mode by default (manual override still available)
  const [theme, setTheme] = useState(localStorage.getItem('theme') === 'light' ? 'light' : 'dark');
  const [svg, setSvg] = useState(null);   // null=loading, ''=unavailable
  const [zoom, setZoom] = useState(1);     // 1 = fit width

  const chartAreaRef = useRef(null);
  const sizeRef = useRef({ width: null, height: null });
  // Always-current snapshot of the values load() needs, so the ResizeObserver
  // callback (set up once per "open" session) never reloads using a stale
  // chartType/range/theme from whenever it was first attached — that bug is
  // what made a resize event silently revert "weekly" back to "daily".
  const latestRef = useRef({ chartType, range, theme });
  useEffect(() => {
    latestRef.current = { chartType, range, theme };
  });

  const load = useCallback(async (type, rangeKey, thm) => {
    if (!symbol) return;
    setSvg(null);
    const days = (RANGES.find(r => r.key === rangeKey) || RANGES[1]).days;
    try {
      const params = new URLSearchParams({
        symbol, theme: thm, from_date: fromDate(days),
      });
      // Request a chart sized to match the actual on-screen container so
      // the backend renders fonts/candles at a legible scale for this
      // device, instead of a fixed 1400x780 desktop canvas getting
      // squeezed (and its text shrunk) into a small mobile viewport.
      const { width, height } = sizeRef.current;
      if (width) params.set('width', String(Math.round(width)));
      if (height) params.set('height', String(Math.round(height)));
      const r = await fetch(`/api/charts/${type}?${params.toString()}`);
      setSvg(r.ok ? makeResponsive(await r.text()) : '');
    } catch {
      setSvg('');
    }
  }, [symbol]);

  // Measure the chart container's real pixel size and (re)load whenever it
  // changes (open, orientation change, resize) — always using the latest
  // chartType/range/theme, never a stale snapshot.
  useEffect(() => {
    if (!open) return;
    const el = chartAreaRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const measureAndLoad = () => {
      const rect = el.getBoundingClientRect();
      const w = Math.max(280, Math.round(rect.width));
      const h = Math.max(280, Math.round(rect.height));
      const changed = sizeRef.current.width !== w || sizeRef.current.height !== h;
      sizeRef.current = { width: w, height: h };
      if (changed) {
        const { chartType: t, range: rk, theme: thm } = latestRef.current;
        load(t, rk, thm);
      }
    };
    let ro;
    try {
      ro = new ResizeObserver(measureAndLoad);
      ro.observe(el);
    } catch {
      // ResizeObserver unsupported — fall back to a single measurement
      measureAndLoad();
    }
    return () => ro && ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (open) {
      // Re-sync with the app theme each time the modal opens. Reuse
      // whatever chartType/range the user last had selected (state
      // persists across opens/closes within a page session).
      const appTheme = localStorage.getItem('theme') === 'light' ? 'light' : 'dark';
      setTheme(appTheme);
      setZoom(1);
      load(chartType, range, appTheme);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const { handlers, panelStyle } = useSwipeToClose(onClose);

  if (!open) return null;

  const setType = (t) => {
    setChartType(t);
    setZoom(1);
    load(t, t === 'weekly' ? weeklyRange : dailyRange, theme);
  };
  const setRangeAndLoad = (rk) => {
    if (chartType === 'weekly') setWeeklyRange(rk); else setDailyRange(rk);
    setZoom(1);
    load(chartType, rk, theme);
  };
  const setThemeAndLoad = (thm) => { setTheme(thm); setZoom(1); load(chartType, range, thm); };

  return (
    <div
      className={`fixed inset-0 z-50 flex flex-col ${theme === 'light' ? 'bg-white' : 'bg-slate-950'}`}
      style={panelStyle}
    >
      {/* Header — swipe down anywhere here to close, or tap the X */}
      <div {...handlers} className="flex-shrink-0 border-b border-slate-700 bg-slate-900" style={{ touchAction: 'none' }}>
        <div className="flex justify-center pt-2.5 pb-2">
          <div className="w-20 h-1.5 rounded-full bg-slate-500" />
        </div>
        <div className="flex items-center justify-between px-4 pb-2">
          <div className="min-w-0">
            <p className="font-bold text-white text-base leading-tight truncate">{symbol?.replace('.NS', '')}</p>
            <p className="text-[11px] text-slate-400 capitalize">{chartType} chart</p>
          </div>
          <button onClick={onClose} className="p-2 text-slate-300 hover:text-white bg-slate-800 rounded-lg flex-shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>
        {/* Theme + Daily/Weekly toggles — own row, scrolls instead of clipping on narrow screens */}
        <div className="flex items-center gap-2 px-4 pb-2 overflow-x-auto">
          <div className="flex bg-slate-800 rounded-lg p-1 flex-shrink-0">
            {['dark', 'light'].map(t => (
              <button key={t} onClick={() => setThemeAndLoad(t)}
                className={`px-2.5 py-1 rounded-md text-sm font-semibold capitalize ${
                  theme === t ? 'bg-blue-600 text-white' : 'text-slate-400'
                }`}>{t === 'dark' ? '🌙' : '☀️'}</button>
            ))}
          </div>
          <div className="flex bg-slate-800 rounded-lg p-1 flex-shrink-0">
            {['daily', 'weekly'].map(t => (
              <button key={t} onClick={() => setType(t)}
                className={`px-3 py-1 rounded-md text-sm font-semibold capitalize ${
                  chartType === t ? 'bg-blue-600 text-white' : 'text-slate-400'
                }`}>{t}</button>
            ))}
          </div>
        </div>
      </div>

      {/* Timeframe selector */}
      <div className="flex-shrink-0 flex items-center gap-1 px-4 py-2 border-b border-slate-800 bg-slate-900 overflow-x-auto">
        <span className="text-xs text-slate-500 mr-1 flex-shrink-0">Range:</span>
        {RANGES.map(r => (
          <button key={r.key} onClick={() => setRangeAndLoad(r.key)}
            className={`px-3 py-1 rounded-md text-xs font-semibold flex-shrink-0 ${
              range === r.key ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400'
            }`}>{r.key}</button>
        ))}
      </div>

      {/* Chart area: zoom 1 = fit entire chart (incl. volume) on one screen; zoom >1 = pan/scroll */}
      <div ref={chartAreaRef} className={`flex-1 min-h-0 p-2 ${zoom > 1 ? 'overflow-auto flex items-start justify-center' : 'overflow-hidden'}`}>
        {svg === null ? (
          <div className="h-full flex items-center justify-center text-slate-400 gap-2">
            <Loader className="w-5 h-5 animate-spin" /> Loading chart…
          </div>
        ) : svg === '' ? (
          <div className="h-full flex items-center justify-center text-slate-400">Chart unavailable for this symbol</div>
        ) : zoom > 1 ? (
          <div
            style={{ width: `${zoom * 100}%`, minWidth: `${zoom * 100}%` }}
            className="[&_svg]:w-full [&_svg]:h-auto"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : (
          <div
            className="w-full h-full [&_svg]:w-full [&_svg]:h-full"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        )}
      </div>

      {/* Zoom controls */}
      {svg && svg !== '' && (
        <div className="flex-shrink-0 flex items-center justify-center gap-3 px-4 py-3 border-t border-slate-700 bg-slate-900">
          <button onClick={() => setZoom(z => Math.max(1, z - 0.5))} disabled={zoom <= 1}
            className="p-2 bg-slate-800 rounded-lg text-slate-300 disabled:opacity-40">
            <ZoomOut className="w-5 h-5" />
          </button>
          <span className="text-sm text-slate-400 w-16 text-center">{zoom.toFixed(1)}×</span>
          <button onClick={() => setZoom(z => Math.min(4, z + 0.5))} disabled={zoom >= 4}
            className="p-2 bg-slate-800 rounded-lg text-slate-300 disabled:opacity-40">
            <ZoomIn className="w-5 h-5" />
          </button>
          <span className="text-xs text-slate-500 ml-2">swipe to pan</span>
        </div>
      )}
    </div>
  );
}
