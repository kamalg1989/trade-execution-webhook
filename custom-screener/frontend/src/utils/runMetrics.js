// Run-metric extraction with explicit fallback chains (2026-08-17 table fix).
//
// The API exposes path metrics under different names depending on which
// engine produced them:
//   PORTFOLIO engine        -> pfCagrPct / pfMaxDDPct / pfWorst12mPct / pfMartin
//   weekly & index engines  -> cagrPct / maxDDPct / worst12mPct / martin
//     (MtM, written by backtest/path_stats.py at completion)
// The table previously read ONLY the pf* names, so every non-PORTFOLIO run
// rendered em-dashes. Each metric now tries the generic name first (MtM,
// preferred), then the pf* name, and logs a warning ONCE per run+metric when
// nothing is populated — visible in devtools instead of silently blank.

const warned = new Set();

function pick(run, keys, metricName) {
  for (const k of keys) {
    const v = run?.[k];
    if (v !== null && v !== undefined && !Number.isNaN(v)) return v;
  }
  const tag = `${run?.id}:${metricName}`;
  if (run?.status === 'COMPLETED' && !warned.has(tag)) {
    warned.add(tag);
    // eslint-disable-next-line no-console
    console.warn(
      `[runMetrics] run #${run?.id} (${run?.strategy}) has no value for ${metricName} `
      + `(tried: ${keys.join(', ')}). Older runs need the path-stats backfill.`);
  }
  return null;
}

export const getCagr = (run) => pick(run, ['cagrPct', 'pfCagrPct'], 'CAGR');
export const getMaxDD = (run) => pick(run, ['maxDDPct', 'pfMaxDDPct'], 'MaxDD');
export const getWorst12m = (run) => pick(run, ['worst12mPct', 'pfWorst12mPct'], 'W12M');
export const getMartin = (run) => pick(run, ['martin', 'pfMartin'], 'Martin');
export const getMaxUwDays = (run) => pick(run, ['maxUwDays'], 'UnderwaterDays');

// ---- formatters (single source of truth for the table + KPI bar) ----
export const fmtCagr = (v, short = false) =>
  v == null ? '—' : `${v.toFixed(1)}%${short ? '*' : ''}`;
export const fmtMaxDD = (v) => (v == null ? '—' : `−${Math.abs(v).toFixed(1)}%`);
export const fmtW12m = (v) => (v == null ? '—' : `${v.toFixed(1)}%`);
export const fmtRatio = (v) => (v == null ? '—' : v.toFixed(2));
export const fmtUwMonths = (days) =>
  days == null ? '—' : `${(days / 30.44).toFixed(0)} mo`;

// ---- audit badges — read straight off the run's own config columns, so a
// badge can never claim something the run did not actually enforce ----
export function runBadges(run) {
  const b = [];
  if (run?.exitSlippagePct != null) b.push({ label: 'Audited MtM', tone: 'emerald',
    title: `Stressed-exit slippage ${run.exitSlippagePct}% + MtM path stats` });
  if (run?.advPositionCapPct != null) b.push({ label: 'ADV-Capped', tone: 'sky',
    title: `No position > ${run.advPositionCapPct}% of the stock's 1-month ADV` });
  if (run?.compoundingMaxCapital != null) b.push({ label: '₹Cap', tone: 'amber',
    title: `Compounding sizing capped at ₹${(run.compoundingMaxCapital / 100000).toFixed(0)}L` });
  if (run?.weeklyRankMode === 'composite') b.push({ label: 'Composite', tone: 'purple',
    title: 'Composite factor ranking (low-turnover + 3m momentum + dist-200SMA)' });
  return b;
}
