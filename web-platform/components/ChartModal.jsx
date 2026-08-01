import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, Loader } from 'lucide-react';
import { useSwipeToClose } from '../utils/useSwipeToClose';

// Make an upstream SVG responsive: ensure a viewBox, strip fixed width/height
// so CSS controls sizing, and force it to fill its container exactly (no
// letterboxing). We request a viewBox sized to match the actual on-screen
// panel (see width/height query params below), so preserveAspectRatio="none"
// is mostly a safety net for rounding, not a real distortion.
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

// The plot panel is requested wider than the on-screen space available to
// it so candles get real breathing room instead of being crammed together -
// you pan/scroll horizontally to see the rest. The Y-axis (price scale)
// panel is requested at its natural width and never scrolls, so panning
// the plot never drags the price labels out of view or off to the side.
const PLOT_ZOOM = 1.6;
// Rough guess for the Y-axis panel's width, used only to size the plot
// request sensibly before we know the backend's actual (slightly
// screen-scaled) value; a few px of slop here is harmless.
const YAXIS_ESTIMATE = 62;

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
  // null = loading, '' = unavailable, else {yaxis, plot, yaxisWidth, plotWidth, height}
  const [chartData, setChartData] = useState(null);

  const chartAreaRef = useRef(null);
  const sizeRef = useRef({ width: null, height: null });
  // Always-current snapshot of the values load() needs, so the ResizeObserver
  // callback (set up once per "open" session) never reloads using a stale
  // chartType/range/theme from whenever it was first attached.
  const latestRef = useRef({ chartType, range, theme });
  useEffect(() => {
    latestRef.current = { chartType, range, theme };
  });

  const load = useCallback(async (type, rangeKey, thm) => {
    if (!symbol) return;
    setChartData(null);
    const days = (RANGES.find(r => r.key === rangeKey) || RANGES[1]).days;
    const { width, height } = sizeRef.current;
    const plotWidth = width ? Math.max(300, Math.round((width - YAXIS_ESTIMATE) * PLOT_ZOOM)) : 900;
    try {
      const params = new URLSearchParams({
        symbol, theme: thm, from_date: fromDate(days),
        split: 'true', width: String(plotWidth),
      });
      if (height) params.set('height', String(Math.round(height)));
      const r = await fetch(`/api/charts/${type}?${params.toString()}`);
      if (!r.ok) { setChartData(''); return; }
      const data = await r.json();
      if (!data || !data.yaxis) { setChartData(''); return; }
      setChartData({
        yaxis: makeResponsive(data.yaxis),
        plot: makeResponsive(data.plot),
        yaxisWidth: data.yaxisWidth,
        plotWidth,
        height: data.height,
      });
    } catch {
      setChartData('');
    }
  }, [symbol]);

  // On open: measure the container SYNCHRONOUSLY first, then load once with
  // the correct size already known - avoids a visible flash from a
  // wrong-sized chart to the correctly-sized one a moment later. The
  // ResizeObserver set up here only reacts to genuine later size changes
  // (e.g. orientation change) - panning the plot panel horizontally does
  // NOT resize this outer container, so it never re-fires from that.
  useEffect(() => {
    if (!open) return;
    const appTheme = localStorage.getItem('theme') === 'light' ? 'light' : 'dark';
    setTheme(appTheme);

    const el = chartAreaRef.current;
    if (el) {
      const rect = el.getBoundingClientRect();
      sizeRef.current = {
        width: Math.max(280, Math.round(rect.width)),
        height: Math.max(280, Math.round(rect.height)),
      };
    }
    load(chartType, range, appTheme);

    if (!el || typeof ResizeObserver === 'undefined') return;
    const measureAndReload = () => {
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
      ro = new ResizeObserver(measureAndReload);
      ro.observe(el);
    } catch {
      // ResizeObserver unsupported - the synchronous measurement above still covers first paint
    }
    return () => ro && ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Swipe-to-close stays bound to the header only (not the whole screen) -
  // the chart area's primary gesture is horizontal panning, and letting a
  // downward drag there compete with that would make panning unreliable.
  const { handlers, panelStyle } = useSwipeToClose(onClose);

  if (!open) return null;

  const setType = (t) => {
    setChartType(t);
    load(t, t === 'weekly' ? weeklyRange : dailyRange, theme);
  };
  const setRangeAndLoad = (rk) => {
    if (chartType === 'weekly') setWeeklyRange(rk); else setDailyRange(rk);
    load(chartType, rk, theme);
  };
  const setThemeAndLoad = (thm) => { setTheme(thm); load(chartType, range, thm); };

  return (
    <div
      className={`fixed inset-0 z-50 flex flex-col ${theme === 'light' ? 'bg-white' : 'bg-slate-950'}`}
      style={panelStyle}
    >
      {/* Header — swipe down anywhere here to close, or tap the X */}
      <div {...handlers} className="flex-shrink-0 border-b border-slate-700 bg-slate-900" style={{ touchAction: 'none' }}>
        <div className="flex justify-center pt-3 pb-2.5">
          <div className="w-24 h-2 rounded-full bg-slate-500" />
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

      {/* Chart area — fixed Y-axis panel (price scale never moves) beside a
          separately, horizontally-scrollable plot panel (candles/volume/
          EMA/date labels pan underneath it). */}
      <div ref={chartAreaRef} className="flex-1 min-h-0 flex overflow-hidden">
        {chartData === null ? (
          <div className="w-full h-full flex items-center justify-center text-slate-400 gap-2">
            <Loader className="w-5 h-5 animate-spin" /> Loading chart…
          </div>
        ) : chartData === '' ? (
          <div className="w-full h-full flex items-center justify-center text-slate-400">Chart unavailable for this symbol</div>
        ) : (
          <>
            <div
              style={{ width: chartData.yaxisWidth, flexShrink: 0 }}
              className="[&_svg]:block [&_svg]:w-full [&_svg]:h-full"
              dangerouslySetInnerHTML={{ __html: chartData.yaxis }}
            />
            <div
              className="flex-1 overflow-x-auto overflow-y-hidden"
              style={{ WebkitOverflowScrolling: 'touch', overscrollBehaviorX: 'contain' }}
            >
              <div
                style={{ width: chartData.plotWidth, height: '100%' }}
                className="[&_svg]:block [&_svg]:w-full [&_svg]:h-full"
                dangerouslySetInnerHTML={{ __html: chartData.plot }}
              />
            </div>
          </>
        )}
      </div>

      {chartData && chartData !== '' && (
        <div className="flex-shrink-0 py-1.5 text-center text-[11px] text-slate-500 bg-slate-900 border-t border-slate-800">
          swipe left/right to pan · price scale stays fixed
        </div>
      )}
    </div>
  );
}
