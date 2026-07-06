import React, { useState, useEffect, useCallback } from 'react';
import { X, ZoomIn, ZoomOut, Loader } from 'lucide-react';

// Make the upstream SVG responsive: ensure a viewBox, strip fixed width/height
// so CSS controls sizing (otherwise the 1400px-wide chart overflows and clips).
export function makeResponsive(svg) {
  if (!svg || svg[0] !== '<') return svg;
  if (!/viewBox=/i.test(svg)) {
    const w = svg.match(/width="(\d+(?:\.\d+)?)"/);
    const h = svg.match(/height="(\d+(?:\.\d+)?)"/);
    if (w && h) svg = svg.replace(/<svg/i, `<svg viewBox="0 0 ${w[1]} ${h[1]}"`);
  }
  return svg
    .replace(/(<svg[^>]*?)\s+width="[^"]*"/i, '$1')
    .replace(/(<svg[^>]*?)\s+height="[^"]*"/i, '$1');
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
  const [range, setRange] = useState('6M');
  const [theme, setTheme] = useState('dark');
  const [svg, setSvg] = useState(null);   // null=loading, ''=unavailable
  const [zoom, setZoom] = useState(1);     // 1 = fit width

  const load = useCallback(async (type, rangeKey, thm) => {
    if (!symbol) return;
    setSvg(null);
    const days = (RANGES.find(r => r.key === rangeKey) || RANGES[1]).days;
    try {
      const r = await fetch(`/api/charts/${type}?symbol=${encodeURIComponent(symbol)}&theme=${thm}&from_date=${fromDate(days)}`);
      setSvg(r.ok ? makeResponsive(await r.text()) : '');
    } catch {
      setSvg('');
    }
  }, [symbol]);

  useEffect(() => {
    if (open) { setZoom(1); load(chartType, range, theme); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const setType = (t) => { setChartType(t); setZoom(1); load(t, range, theme); };
  const setRangeAndLoad = (rk) => { setRange(rk); setZoom(1); load(chartType, rk, theme); };
  const setThemeAndLoad = (thm) => { setTheme(thm); setZoom(1); load(chartType, range, thm); };

  return (
    <div className={`fixed inset-0 z-50 flex flex-col ${theme === 'light' ? 'bg-white/95' : 'bg-black/95'}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 bg-slate-900">
        <div>
          <p className="font-bold text-white text-lg leading-tight">{symbol?.replace('.NS', '')}</p>
          <p className="text-xs text-slate-400 capitalize">{chartType} chart</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Theme toggle */}
          <div className="flex bg-slate-800 rounded-lg p-1">
            {['dark', 'light'].map(t => (
              <button key={t} onClick={() => setThemeAndLoad(t)}
                className={`px-2.5 py-1 rounded-md text-sm font-semibold capitalize ${
                  theme === t ? 'bg-blue-600 text-white' : 'text-slate-400'
                }`}>{t === 'dark' ? '🌙' : '☀️'}</button>
            ))}
          </div>
          <div className="flex bg-slate-800 rounded-lg p-1">
            {['daily', 'weekly'].map(t => (
              <button key={t} onClick={() => setType(t)}
                className={`px-3 py-1 rounded-md text-sm font-semibold capitalize ${
                  chartType === t ? 'bg-blue-600 text-white' : 'text-slate-400'
                }`}>{t}</button>
            ))}
          </div>
          <button onClick={onClose} className="p-2 text-slate-300 hover:text-white bg-slate-800 rounded-lg">
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Timeframe selector */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-slate-800 bg-slate-900 overflow-x-auto">
        <span className="text-xs text-slate-500 mr-1">Range:</span>
        {RANGES.map(r => (
          <button key={r.key} onClick={() => setRangeAndLoad(r.key)}
            className={`px-3 py-1 rounded-md text-xs font-semibold flex-shrink-0 ${
              range === r.key ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400'
            }`}>{r.key}</button>
        ))}
      </div>

      {/* Chart area (scrollable both axes) */}
      <div className="flex-1 overflow-auto p-2 flex items-start justify-center">
        {svg === null ? (
          <div className="h-full flex items-center justify-center text-slate-400 gap-2">
            <Loader className="w-5 h-5 animate-spin" /> Loading chart…
          </div>
        ) : svg === '' ? (
          <div className="h-full flex items-center justify-center text-slate-400">Chart unavailable for this symbol</div>
        ) : (
          <div
            style={{ width: `${zoom * 100}%`, minWidth: zoom > 1 ? `${zoom * 100}%` : undefined }}
            className="[&_svg]:w-full [&_svg]:h-auto"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        )}
      </div>

      {/* Zoom controls */}
      {svg && svg !== '' && (
        <div className="flex items-center justify-center gap-3 px-4 py-3 border-t border-slate-700 bg-slate-900">
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
