import React, { useState, useEffect } from 'react';
import { Save, RotateCcw, Play, CheckCircle, AlertCircle, Loader, Copy, Eye, EyeOff } from 'lucide-react';

const NUMBER_FIELDS = [
  { key: 'capital', label: 'Trading Capital (₹)', step: 10000, hint: 'Total capital used for position sizing' },
  { key: 'maxAlertsPerRun', label: 'Max Picks Per Scan', step: 1, hint: 'Top N ranked setups sent as alerts' },
  { key: 'minTurnoverCr', label: 'Min Daily Turnover (₹ cr)', step: 1, hint: 'Liquidity gate threshold' },
  { key: 'targetRMultiple', label: 'Target R Multiple', step: 0.5, hint: 'Target = Entry + R × Risk' },
  { key: 'techMaxBaseRangePct', label: 'Max Base Range (%)', step: 1, hint: 'Tightness of consolidation base' },
  { key: 'baseMinPriorUpmovePct', label: 'Min Prior Upmove (%)', step: 1, hint: 'Required advance before base' },
  { key: 'baseMaxGivebackPct', label: 'Max Giveback (%)', step: 5, hint: 'Max retracement of prior move' },
  { key: 'maxBaseStage', label: 'Max Base Stage', step: 1, hint: 'Reject late-stage bases above this' },
  { key: 'ifpMinScore', label: 'Min IFP Score', step: 0.05, hint: 'Institutional footprint threshold (0-1)' },
  { key: 'fundMaxPE', label: 'Max P/E', step: 5, hint: 'Fundamental gate: max valuation' },
  { key: 'fundMinROEPct', label: 'Min ROE (%)', step: 1, hint: 'Fundamental gate: min return on equity' },
];

const SELECT_FIELDS = [
  { key: 'targetStrategy', label: 'Target Strategy', options: ['FIXED_R', 'BASE_HEIGHT', 'TRAIL_ONLY'] },
  { key: 'trendAlignmentMode', label: 'Trend Alignment Mode', options: ['strict', 'medium', 'loose'] },
];

export default function Settings() {
  const [settings, setSettings] = useState(null);
  const [labels, setLabels] = useState({});
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [message, setMessage] = useState(null);
  const [apiKey, setApiKey] = useState(localStorage.getItem('trading_api_key') || null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [loadingApiKey, setLoadingApiKey] = useState(false);
  const [pinInput, setPinInput] = useState('');
  const [showPinDialog, setShowPinDialog] = useState(false);

  useEffect(() => {
    fetch('/api/settings')
      .then(r => r.json())
      .then(data => {
        setSettings({ values: data.values, features: data.features });
        setLabels(data.featureLabels || {});
      })
      .catch(() => setMessage({ type: 'error', text: 'Failed to load settings' }));
  }, []);

  const setValue = (key, val) =>
    setSettings(s => ({ ...s, values: { ...s.values, [key]: val } }));

  const toggleFeature = (key) =>
    setSettings(s => ({ ...s, features: { ...s.features, [key]: !s.features[key] } }));

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
      const data = await r.json();
      if (r.ok) setMessage({ type: 'ok', text: 'Saved! Settings apply on the next scan.' });
      else setMessage({ type: 'error', text: data.detail || 'Save failed' });
    } catch {
      setMessage({ type: 'error', text: 'Save failed' });
    }
    setSaving(false);
  };

  const reset = async () => {
    if (!window.confirm('Reset all settings to defaults?')) return;
    const r = await fetch('/api/settings/reset', { method: 'POST' });
    const data = await r.json();
    setSettings({ values: data.values, features: data.features });
    setMessage({ type: 'ok', text: 'Reset to defaults' });
  };

  const runScan = async () => {
    setScanning(true);
    setMessage(null);
    try {
      const r = await fetch('/api/recommendations/refresh', { method: 'POST' });
      const data = await r.json();
      setMessage({ type: 'ok', text: data.message || 'Scan started' });
    } catch {
      setMessage({ type: 'error', text: 'Failed to start scan' });
    }
    setScanning(false);
  };

  const loadApiKey = async () => {
    if (!pinInput) {
      setMessage({ type: 'error', text: 'Please enter your PIN' });
      return;
    }

    setLoadingApiKey(true);
    try {
      const r = await fetch('/api/security/api-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: pinInput })
      });
      const data = await r.json();

      if (!r.ok) {
        setMessage({ type: 'error', text: `❌ ${data.detail || 'Invalid PIN'}` });
        setLoadingApiKey(false);
        return;
      }

      setApiKey(data.api_key);
      localStorage.setItem('trading_api_key', data.api_key);
      setMessage({ type: 'ok', text: '✅ API key loaded and saved to browser!' });
      setShowApiKey(true);
      setShowPinDialog(false);
      setPinInput('');
    } catch (error) {
      setMessage({ type: 'error', text: 'Failed to load API key' });
    }
    setLoadingApiKey(false);
  };

  const copyApiKey = () => {
    if (apiKey) {
      navigator.clipboard.writeText(apiKey);
      setMessage({ type: 'ok', text: '✅ API key copied to clipboard' });
    }
  };

  if (!settings) return <div className="p-4 lg:p-8 text-slate-400">Loading settings...</div>;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800 text-white p-4 lg:p-8 pb-24">
      <div className="max-w-3xl mx-auto space-y-6">
        <div>
          <h1 className="text-2xl lg:text-4xl font-bold mb-1">⚙️ Screener Settings</h1>
          <p className="text-xs lg:text-base text-slate-400">
            Changes apply on the next scan run
          </p>
        </div>

        {message && (
          <div className={`rounded-lg p-3 flex items-center gap-2 text-sm ${
            message.type === 'ok' ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'
          }`}>
            {message.type === 'ok' ? <CheckCircle className="w-4 h-4 flex-shrink-0" /> : <AlertCircle className="w-4 h-4 flex-shrink-0" />}
            {message.text}
          </div>
        )}

        {/* API Key Security */}
        <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
          <h2 className="text-base lg:text-xl font-bold mb-4">🔐 Trading Protection</h2>
          <p className="text-sm text-slate-300 mb-4">
            Your API key prevents others from accessing your trading functions. It's stored securely in your browser.
          </p>
          {!apiKey ? (
            <>
              {!showPinDialog ? (
                <button
                  onClick={() => setShowPinDialog(true)}
                  className="bg-blue-600 hover:bg-blue-700 font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2"
                >
                  <CheckCircle className="w-4 h-4" />
                  Load API Key
                </button>
              ) : (
                <div className="space-y-3 bg-slate-800 rounded p-4">
                  <label className="text-sm text-slate-300 block">Enter your PIN to access API key:</label>
                  <input
                    type="password"
                    placeholder="Enter PIN (default: 1234)"
                    value={pinInput}
                    onChange={(e) => setPinInput(e.target.value)}
                    onKeyPress={(e) => e.key === 'Enter' && loadApiKey()}
                    className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={loadApiKey}
                      disabled={loadingApiKey || !pinInput}
                      className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 font-bold py-2 px-4 rounded flex items-center justify-center gap-2"
                    >
                      {loadingApiKey ? <Loader className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                      Load Key
                    </button>
                    <button
                      onClick={() => { setShowPinDialog(false); setPinInput(''); }}
                      className="flex-1 bg-slate-600 hover:bg-slate-500 font-bold py-2 px-4 rounded"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="space-y-3">
              <div className="bg-slate-800 rounded p-3 flex items-center gap-2">
                <input
                  type={showApiKey ? 'text' : 'password'}
                  value={apiKey}
                  readOnly
                  className="flex-1 bg-transparent text-sm font-mono text-green-300 focus:outline-none"
                />
                <button
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="text-slate-400 hover:text-slate-300 p-1"
                  title={showApiKey ? 'Hide' : 'Show'}
                >
                  {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
                <button
                  onClick={copyApiKey}
                  className="text-slate-400 hover:text-slate-300 p-1"
                  title="Copy"
                >
                  <Copy className="w-4 h-4" />
                </button>
              </div>
              <p className="text-xs text-slate-400">✅ Stored in browser. All trades are protected.</p>
            </div>
          )}
        </div>

        {/* Feature Toggles */}
        <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
          <h2 className="text-base lg:text-xl font-bold mb-4">Screener Features</h2>
          <div className="space-y-3">
            {Object.entries(settings.features).map(([key, enabled]) => (
              <div key={key} className="flex items-center justify-between">
                <label className="text-sm font-semibold flex-1 cursor-pointer">{labels[key] || key}</label>
                <button
                  onClick={() => toggleFeature(key)}
                  className={`relative inline-flex h-6 w-11 rounded-full flex-shrink-0 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 focus:ring-offset-slate-700 ${
                    enabled ? 'bg-blue-600' : 'bg-slate-600'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform my-auto mx-1 ${
                      enabled ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Numeric Values */}
        <div className="bg-slate-700 rounded-lg p-4 lg:p-6">
          <h2 className="text-base lg:text-xl font-bold mb-4">Screener Parameters</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {NUMBER_FIELDS.map(f => (
              <div key={f.key}>
                <label className="text-xs text-slate-400 block mb-1">{f.label}</label>
                <input
                  type="number"
                  step={f.step}
                  value={settings.values[f.key] ?? ''}
                  onChange={e => setValue(f.key, e.target.value === '' ? '' : Number(e.target.value))}
                  className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                />
                <p className="text-[10px] text-slate-500 mt-0.5">{f.hint}</p>
              </div>
            ))}
            {SELECT_FIELDS.map(f => (
              <div key={f.key}>
                <label className="text-xs text-slate-400 block mb-1">{f.label}</label>
                <select
                  value={settings.values[f.key]}
                  onChange={e => setValue(f.key, e.target.value)}
                  className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                >
                  {f.options.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={save}
            disabled={saving}
            className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2"
          >
            {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save Settings
          </button>
          <button
            onClick={runScan}
            disabled={scanning}
            className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2"
          >
            {scanning ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            Run Scan Now
          </button>
          <button
            onClick={reset}
            className="bg-slate-600 hover:bg-slate-500 font-bold py-3 px-4 rounded-lg flex items-center justify-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}
