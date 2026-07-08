import React, { useEffect, useState } from 'react';
import { chartUrl } from '../api/client.js';

// Own chart modal — fetches SVG from the existing Market Data charts API.
export default function ChartModal({ symbol, open, onClose }) {
  const [type, setType] = useState('daily');
  const [svg, setSvg] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !symbol) return;
    setLoading(true);
    setSvg('');
    fetch(chartUrl(symbol, type, 'dark'))
      .then((r) => r.text())
      .then((t) => setSvg(t))
      .catch(() => setSvg('<div style="color:#f87171">Failed to load chart</div>'))
      .finally(() => setLoading(false));
  }, [open, symbol, type]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-lg max-w-6xl w-full max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
          <div className="font-bold text-slate-100">{symbol}</div>
          <div className="flex items-center gap-2">
            {['daily', 'weekly'].map((t) => (
              <button key={t} onClick={() => setType(t)}
                className={`px-3 py-1 text-sm rounded ${type === t ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300'}`}>
                {t}
              </button>
            ))}
            <button onClick={onClose} className="px-3 py-1 text-sm rounded bg-slate-700 text-slate-300 hover:bg-slate-600">✕</button>
          </div>
        </div>
        <div className="p-3 [&_svg]:w-full [&_svg]:h-auto">
          {loading ? <div className="text-slate-400 text-center py-10">Loading chart…</div>
                   : <div dangerouslySetInnerHTML={{ __html: svg }} />}
        </div>
      </div>
    </div>
  );
}
