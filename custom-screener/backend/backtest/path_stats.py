"""Post-run path statistics for WEEKLY_BREAKOUT / INDEX_TF runs.

WHY THIS EXISTS (2026-08-17 UI bug root cause): the run-history table renders
CAGR / maxDD / w12m / Martin from backtest_runs columns that only the
PORTFOLIO engine ever wrote (pf_*) — the daily engine writes cagr_pct/
max_dd_pct for its own path, and the weekly/index engines wrote NOTHING. So
every WEEKLY_BREAKOUT and INDEX_TF row showed em-dashes forever. The frontend
"mapping bug" was actually a missing producer.

Curves here are MARK-TO-MARKET (2026-08-17 risk audit V2): open positions are
marked at each Friday's close, not carried at cost until exit. The audit
measured realized-only MaxDD understating true drawdown by 12-25 points on
these books, so publishing realized-only numbers to the UI would embed a known
falsehood in the product surface.

Price source per strategy:
  WEEKLY_BREAKOUT -> ohlcv_weekly (symbol, week_end, close)
  INDEX_TF        -> index_proxy_daily (the traded proxy's own level series;
                     SYNTH_EQW has no ohlcv_weekly rows, and even for real
                     ETFs the proxy table is the series the engine traded)
"""
from __future__ import annotations

import logging
import math
from datetime import date

logger = logging.getLogger(__name__)


async def compute_and_store_mtm_stats(pool, run_id: int) -> None:
    """Compute MtM path stats and persist onto the run row. Never raises —
    a stats failure must not fail (or roll back) a completed backtest."""
    try:
        await _compute(pool, run_id)
    except Exception:
        logger.exception("path_stats failed for run %s (run itself unaffected)", run_id)


async def build_mtm_curve(pool, run_id: int):
    """Weekly-grid mark-to-market equity for a run: (weeks, equity, run_row)
    or (None, None, None) when it can't be built. Shared by the stats writer
    below and the /backtest/blend endpoint, so a blended figure can never be
    computed off a different curve definition than the table shows."""
    run = await pool.fetchrow(
        "SELECT id, strategy, capital, start_date, end_date, itf_cash_annual_pct "
        "FROM backtest_runs WHERE id=$1", run_id)
    if run is None:
        return None, None, None
    capital = float(run["capital"])
    trades = await pool.fetch(
        """
        SELECT symbol, entry_fill_date, entry_fill_price, quantity, exit_date,
               realized_pnl, status
        FROM backtest_trades
        WHERE run_id=$1 AND entry_fill_date IS NOT NULL
        """, run_id)
    if not trades:
        return None, None, None

    is_itf = (run["strategy"] or "") == "INDEX_TF"
    symbols = list({t["symbol"] for t in trades})
    if is_itf:
        px_rows = await pool.fetch(
            "SELECT proxy AS symbol, d AS week_end, level AS close FROM index_proxy_daily "
            "WHERE proxy = ANY($1) AND d BETWEEN $2 AND $3 "
            "AND EXTRACT(dow FROM d) = 5",  # Fridays, to match weekly cadence
            symbols, run["start_date"], run["end_date"])
    else:
        px_rows = await pool.fetch(
            "SELECT symbol, week_end, close FROM ohlcv_weekly "
            "WHERE symbol = ANY($1) AND week_end BETWEEN $2 AND $3",
            symbols, run["start_date"], run["end_date"])
    px: dict[tuple[str, date], float] = {
        (r["symbol"], r["week_end"]): float(r["close"]) for r in px_rows}
    weeks = sorted({w for (_, w) in px})
    if len(weeks) < 8:
        return None, None, None

    # realized P&L keyed by exit date, walked forward cumulatively
    realized = sorted(
        ((t["exit_date"], float(t["realized_pnl"] or 0)) for t in trades
         if t["status"] == "CLOSED" and t["exit_date"] is not None),
        key=lambda x: x[0])
    open_spans = [
        (t["symbol"], t["entry_fill_date"],
         t["exit_date"] or date.max, float(t["entry_fill_price"] or 0), int(t["quantity"] or 0))
        for t in trades]

    # INDEX_TF cash-yield accrual (2026-08-17 fix): the engine compounds idle
    # equity at itf_cash_annual_pct on FLAT days, but that interest lives only
    # inside the engine's own equity variable — backtest_trades has no row for
    # it, so a trades-only reconstruction silently drops ~1.5-2 CAGR points on
    # a book that sits in cash ~30% of the time. Reconstructed here: interest
    # compounds on (capital + realized P&L + prior interest) across every gap
    # between grid points during which no position is open.
    cash_rate = (float(run["itf_cash_annual_pct"]) / 100.0
                 if is_itf and run["itf_cash_annual_pct"] is not None else
                 (0.06 if is_itf else 0.0))

    equity: list[float] = []
    cum, ri = 0.0, 0
    cash_credit = 0.0
    prev_w = None
    for w in weeks:
        while ri < len(realized) and realized[ri][0] <= w:
            cum += realized[ri][1]
            ri += 1
        unreal = 0.0
        n_open = 0
        for sym, ein, eout, ep, q in open_spans:
            if ein <= w < eout:
                n_open += 1
                c = px.get((sym, w))
                if c is not None:
                    unreal += (c - ep) * q
        if cash_rate > 0 and prev_w is not None and n_open == 0:
            gap_days = (w - prev_w).days
            base = capital + cum + cash_credit
            cash_credit += base * ((1 + cash_rate) ** (gap_days / 365.25) - 1)
        prev_w = w
        equity.append(capital + cum + unreal + cash_credit)
    return weeks, equity, run


async def _compute(pool, run_id: int) -> None:
    weeks, equity, run = await build_mtm_curve(pool, run_id)
    if weeks is None:
        return
    capital = float(run["capital"])

    yrs = (weeks[-1] - weeks[0]).days / 365.25
    if yrs <= 0 or equity[-1] <= 0:
        return
    cagr = ((equity[-1] / capital) ** (1 / yrs) - 1) * 100

    # NOTE: `weeks` is the union of per-symbol week_end dates, and ohlcv_weekly
    # week_ends differ across symbols on holiday-shortened weeks — so the grid
    # has SEVERAL points per calendar week, not one. Underwater duration must
    # therefore be measured as an actual DATE SPAN between the peak-loss date
    # and the recovery date, never as (number of grid points) x 7 days — the
    # step-count version inflated a 36.9-month spell to a nonsense 158 months
    # (caught validating the 2026-08-17 backfill against the audit's numbers).
    peak, max_dd = capital, 0.0
    sq_dd_sum = 0.0
    uw_start: date | None = None
    max_uw_span_days = 0
    for w, eq in zip(weeks, equity):
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        sq_dd_sum += dd * dd
        if dd > 0.01:                      # >=1bp of peak = materially underwater
            if uw_start is None:
                uw_start = w
            max_uw_span_days = max(max_uw_span_days, (w - uw_start).days)
        else:
            uw_start = None
    ulcer = math.sqrt(sq_dd_sum / len(equity))
    martin = (cagr / ulcer) if ulcer > 0.01 else None

    # worst rolling 12-month return — date-based lookback (two-pointer), NOT
    # a fixed step count: the grid has several points per calendar week (see
    # note above), so "52 steps back" would be ~4 months, not 12.
    worst_12m = None
    j = 0
    for i in range(len(weeks)):
        while j < i and (weeks[i] - weeks[j]).days > 365:
            j += 1
        if j > 0 and (weeks[i] - weeks[j - 1]).days >= 365 and equity[j - 1] > 0:
            r = (equity[i] / equity[j - 1] - 1) * 100
            if worst_12m is None or r < worst_12m:
                worst_12m = r

    await pool.execute(
        """
        UPDATE backtest_runs SET
          cagr_pct=$2, max_dd_pct=$3, pf_worst_12m_pct=$4, pf_ulcer=$5,
          pf_martin=$6, max_uw_days=$7
        WHERE id=$1
        """,
        run_id, round(cagr, 2), round(max_dd, 2),
        round(worst_12m, 2) if worst_12m is not None else None,
        round(ulcer, 3), round(martin, 3) if martin is not None else None,
        max_uw_span_days,
    )
    logger.info("path_stats run %s: MtM CAGR %.2f%% maxDD %.2f%% w12m %s martin %s uw %dd",
                run_id, cagr, max_dd,
                f"{worst_12m:.1f}%" if worst_12m is not None else "-",
                f"{martin:.2f}" if martin is not None else "-", max_uw_span_days)
