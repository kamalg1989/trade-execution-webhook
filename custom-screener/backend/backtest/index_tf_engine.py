"""Index Trend Following (INDEX_TF) — long/flat moving-average trend following
on a single index proxy. Dispatched from engine.run_backtest on
backtest_runs.strategy = 'INDEX_TF'.

WHY THIS EXISTS. It is not here because index trend following is the best
standalone strategy available; it is here because it is deliberately
UNCORRELATED with the WEEKLY_BREAKOUT composite book (measured monthly-return
rho = 0.015 over 2011-2026). It takes no stock-selection risk whatsoever, and
it is FLAT during sustained index downtrends — which is precisely when a
long-only breakout book bleeds. Its purpose is to let the pair be re-levered
back to the same drawdown budget, converting a volatility reduction into
return. See the multi-strategy study (2026-08-17).

MECHANICS
  signal : proxy level > its own itf_ma_days simple moving average, evaluated
           on the PREVIOUS close and acted on at today's open. The shift is
           what makes it executable — comparing today's close to an MA that
           includes today's close, then trading at that same close, is a
           look-ahead the equity curve would silently reward.
  long   : deploy itf_capital_pct of equity into the proxy.
  flat   : hold cash, accruing itf_cash_annual_pct per annum (Indian
           risk-free is ~6%, not ~0, and a trend system that sits in cash for
           ~30% of the period would be materially misvalued at 0%).
  no shorting: shorting Indian equity indices carries borrow/roll costs and
           constraints this account size would not clear, so the flat leg is
           cash rather than short. Modelling a short leg would flatter the
           backtest with returns that are not accessible in practice.

PROXY SERIES. Read from index_proxy_daily (proxy, d, level), populated by
build_index_proxies() below:
  SYNTH_EQW  — equal-weight daily return of every stock with >= Rs.5cr 1-month
               average turnover, chained into a level series. Used because the
               tradeable index ETFs in ohlcv_data only start in 2019, and a
               15-year history is required to validate alongside the breakout
               book. Equal-weight (not cap-weight) is also the honest benchmark
               here, since the breakout book has a documented small-cap tilt.
  NIFTYBEES / JUNIORBEES / SETFNIF50 — real tradeable ETFs, 2019 onward.
               Used to CHECK the synthetic proxy over the overlapping window;
               a live implementation would trade these.

Writes ordinary backtest_trades rows (one per long stretch, quant_rank=1) so
the existing run list, trade log, and equity-curve surfaces work unchanged.
"""
from __future__ import annotations

import logging
from datetime import date

from .simulator import _buy_fill, _leg_costs, _sell_fill

logger = logging.getLogger(__name__)

TURNOVER_FLOOR_CR = 5.0
RETURN_CLIP = 0.25   # drop |daily return| > 25% as bad ticks / unadjusted splits


async def build_index_proxies(pool, force: bool = False) -> None:
    """Materialise index_proxy_daily. Idempotent: skips the expensive synthetic
    rebuild if the table already covers data through the latest ohlcv_data date,
    so a parameter sweep pays this cost once rather than per run."""
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS index_proxy_daily (
            proxy TEXT NOT NULL,
            d DATE NOT NULL,
            level DOUBLE PRECISION NOT NULL,
            n_constituents INT,
            PRIMARY KEY (proxy, d)
        )
        """
    )
    latest_src = await pool.fetchval("SELECT MAX(time::date) FROM ohlcv_data")
    latest_have = await pool.fetchval(
        "SELECT MAX(d) FROM index_proxy_daily WHERE proxy = 'SYNTH_EQW'")
    if not force and latest_have is not None and latest_src is not None and latest_have >= latest_src:
        logger.info("index_proxy_daily already current through %s — skipping rebuild", latest_have)
        return

    logger.info("building SYNTH_EQW index proxy (this is the expensive step)...")
    await pool.execute("DELETE FROM index_proxy_daily WHERE proxy = 'SYNTH_EQW'")
    # Equal-weight daily mean return over the liquid universe, then chained.
    # The liquidity join is against stock_indicators as-of that same date, so
    # membership is point-in-time and a stock that was illiquid in 2013 is not
    # retroactively included because it is liquid now.
    await pool.execute(
        f"""
        INSERT INTO index_proxy_daily (proxy, d, level, n_constituents)
        WITH rets AS (
            SELECT o.time::date AS d, o.symbol,
                   o.close / NULLIF(LAG(o.close) OVER (PARTITION BY o.symbol
                                                        ORDER BY o.time), 0) - 1 AS r
            FROM ohlcv_data o
        ),
        daily AS (
            SELECT r.d, AVG(r.r) AS eqw_ret, COUNT(*) AS n
            FROM rets r
            JOIN stock_indicators si
              ON si.symbol = r.symbol AND si.indicator_date = r.d
            WHERE r.r IS NOT NULL AND r.r BETWEEN {-RETURN_CLIP} AND {RETURN_CLIP}
              AND si.turnover_1m_avg_cr >= {TURNOVER_FLOOR_CR}
            GROUP BY r.d
            HAVING COUNT(*) >= 100
        )
        SELECT 'SYNTH_EQW', d,
               100.0 * EXP(SUM(LN(1 + eqw_ret)) OVER (ORDER BY d)) AS level,
               n
        FROM daily
        """
    )
    # Real ETFs, straight from their own close series (already tradeable levels).
    for etf in ("NIFTYBEES", "JUNIORBEES", "SETFNIF50"):
        await pool.execute(
            """
            INSERT INTO index_proxy_daily (proxy, d, level, n_constituents)
            SELECT $1, time::date, close, 1 FROM ohlcv_data
            WHERE symbol = $1 AND close > 0
            ON CONFLICT (proxy, d) DO UPDATE SET level = EXCLUDED.level
            """,
            etf,
        )
    counts = await pool.fetch(
        "SELECT proxy, COUNT(*) n, MIN(d) lo, MAX(d) hi FROM index_proxy_daily GROUP BY proxy")
    for c in counts:
        logger.info("  proxy %-12s %5d days  %s .. %s", c["proxy"], c["n"], c["lo"], c["hi"])


async def run_index_tf(run: dict, pool) -> None:
    run_id = run["id"]
    start_date, end_date = run["start_date"], run["end_date"]
    capital = float(run["capital"])
    proxy = str(run.get("itf_proxy") or "SYNTH_EQW")
    ma_days = int(run.get("itf_ma_days") or 200)
    deploy_pct = float(run.get("itf_capital_pct") or 95.0)
    cash_annual = float(run.get("itf_cash_annual_pct") or 6.0)
    compounding = bool(run.get("compounding_enabled"))

    cfg = {
        "slippage_pct": float(run["slippage_pct"]),
        "brokerage_per_order": float(run["brokerage_per_order"]),
        "stt_pct": float(run["stt_pct"]),
        "stamp_duty_pct": float(run["stamp_duty_pct"]),
        "exchange_charges_pct": float(run["exchange_charges_pct"]),
        "dp_charge": float(run["dp_charge"]),
    }

    await build_index_proxies(pool)

    # Pull enough history BEFORE start_date to have a warm MA on day one --
    # otherwise the first ma_days of the run would trade on a half-formed
    # average (or not trade at all), which is an artifact of where the window
    # was cut rather than anything about the strategy.
    rows = await pool.fetch(
        """
        SELECT d, level FROM index_proxy_daily
        WHERE proxy = $1 AND d <= $2
        ORDER BY d
        """,
        proxy, end_date,
    )
    if len(rows) < ma_days + 10:
        raise RuntimeError(
            f"INDEX_TF: proxy {proxy} has only {len(rows)} days through {end_date}, "
            f"need > {ma_days + 10} for a {ma_days}-day MA. "
            f"(The tradeable ETFs only start 2019 — use SYNTH_EQW for long windows.)"
        )

    days = [r["d"] for r in rows]
    levels = [float(r["level"]) for r in rows]

    # Rolling SMA via running sum -- O(n), and more importantly numerically
    # identical for every run regardless of window, unlike recomputing slices.
    sma: list[float | None] = [None] * len(levels)
    run_sum = 0.0
    for i, lv in enumerate(levels):
        run_sum += lv
        if i >= ma_days:
            run_sum -= levels[i - ma_days]
        if i >= ma_days - 1:
            sma[i] = run_sum / ma_days

    in_window = [i for i, d in enumerate(days) if start_date <= d <= end_date]
    if not in_window:
        raise RuntimeError(f"INDEX_TF: no proxy data inside {start_date}..{end_date}")
    await pool.execute("UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2",
                       len(in_window), run_id)

    cash_daily = cash_annual / 100.0 / 252.0
    equity = capital           # realized equity (cash + closed P&L)
    position = None            # dict(entry_date, entry_price, qty, db_id)
    n_trades = 0
    logger.info("INDEX_TF run %s: proxy=%s ma=%d deploy=%.0f%% cash=%.1f%% compounding=%s "
                "(%d days in window)", run_id, proxy, ma_days, deploy_pct, cash_annual,
                compounding, len(in_window))

    for step, i in enumerate(in_window):
        d, lv = days[i], levels[i]
        # Signal from the PREVIOUS bar only (see module docstring).
        prev_sma = sma[i - 1] if i > 0 else None
        prev_lv = levels[i - 1] if i > 0 else None
        want_long = (prev_sma is not None and prev_lv is not None and prev_lv > prev_sma)

        if position is None and want_long:
            sizing_base = equity if compounding else capital
            gross = lv
            fill = _buy_fill(gross, cfg)
            qty = int(sizing_base * deploy_pct / 100.0 / fill)
            if qty > 0:
                row = await pool.fetchrow(
                    """
                    INSERT INTO backtest_trades
                      (run_id, symbol, quant_rank, signal_date, entry_trigger_price,
                       structural_sl, target_price, risk_per_share, quantity,
                       entry_type, status, entry_fill_date, entry_fill_price)
                    VALUES ($1,$2,1,$3,$4,0.0,0.0,$5,$6,$7,'OPEN',$3,$8)
                    RETURNING id
                    """,
                    run_id, proxy, d, round(gross, 2),
                    # risk_per_share has no meaning for a trend system with no
                    # stop; store the MA distance so r_multiple stays finite
                    # and interpretable rather than dividing by zero.
                    round(max(0.01, prev_lv - prev_sma), 2), qty, "INDEX_TF_LONG",
                    round(fill, 2),
                )
                entry_cost = _leg_costs(fill * qty, cfg, is_sell=False)
                equity -= entry_cost
                position = dict(entry_date=d, entry_price=fill, qty=qty, db_id=row["id"],
                                gross_entry=gross, costs=entry_cost)
                n_trades += 1

        elif position is not None and not want_long:
            gross = lv
            net = _sell_fill(gross, cfg)
            qty = position["qty"]
            exit_cost = _leg_costs(net * qty, cfg, is_sell=True)
            pnl = (net - position["entry_price"]) * qty - exit_cost
            equity += pnl
            rps = 0.0
            await pool.execute(
                """
                UPDATE backtest_trades SET status='CLOSED', exit_date=$2, exit_price=$3,
                  exit_reason=$4, realized_pnl=$5, gross_pnl=$6, r_multiple=$7,
                  holding_days=$8, trail_sl=$9
                WHERE id=$1
                """,
                position["db_id"], d, round(net, 2), "MA_EXIT",
                round(pnl - position["costs"], 2),
                round((gross - position["gross_entry"]) * qty, 2),
                None, (d - position["entry_date"]).days, round(prev_sma or 0.0, 2),
            )
            position = None

        elif position is None:
            # Flat: idle capital earns the cash rate. Applied only when truly
            # flat, so it never double-counts alongside a deployed position.
            equity *= (1 + cash_daily)

        if step % 100 == 0 or step == len(in_window) - 1:
            await pool.execute("UPDATE backtest_runs SET progress_day=$1 WHERE id=$2",
                               step + 1, run_id)

    # Mark-to-market any still-open position at the final bar so the run's
    # equity is complete rather than truncated mid-trade.
    if position is not None:
        gross = levels[in_window[-1]]
        net = _sell_fill(gross, cfg)
        qty = position["qty"]
        pnl = (net - position["entry_price"]) * qty - _leg_costs(net * qty, cfg, is_sell=True)
        await pool.execute(
            """
            UPDATE backtest_trades SET status='CLOSED', exit_date=$2, exit_price=$3,
              exit_reason=$4, realized_pnl=$5, gross_pnl=$6, holding_days=$7
            WHERE id=$1
            """,
            position["db_id"], days[in_window[-1]], round(net, 2), "END_OF_RUN",
            round(pnl - position["costs"], 2),
            round((gross - position["gross_entry"]) * qty, 2),
            (days[in_window[-1]] - position["entry_date"]).days,
        )
        equity += pnl

    logger.info("INDEX_TF run %s complete: %d trades, final realized equity Rs.%.0f (%.2fx)",
                run_id, n_trades, equity, equity / capital)
    from .path_stats import compute_and_store_mtm_stats
    await compute_and_store_mtm_stats(pool, run_id)
    await pool.execute(
        "UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() WHERE id=$1", run_id)
