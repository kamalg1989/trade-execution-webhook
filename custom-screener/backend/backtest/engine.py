"""Backtest orchestrator — one run, day by day, chronological. See
/BACKTEST_ENGINE_SPEC.md (repo root) for the full design and §4 for the
exact per-day flow this implements.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from bisect import bisect_left, bisect_right
from datetime import date

import pandas as pd

from ai_analysis import config as ai_config
from ai_analysis.features.swings import last_swing_low
from ai_analysis.pipeline import analyze_symbols
from app.db import PgRepo

from . import funnel
from . import funnel_v2
from . import funnel_stage2
from . import weekly_breakout as wb
from .ai_repo import BacktestAiRepo
from .position_sizing import PositionSizer
from .simulator import SimTrade, close_trade, step_exit, try_fill

SWING_LOOKBACK_DAYS = 80  # trailing window fed to last_swing_low() — plenty for a 5-bar pivot

logger = logging.getLogger(__name__)


def _days_to_earnings_outer(earnings_by_symbol: dict, sym: str, ref_day) -> int | None:
    """Module-level twin of the per-run closure, for use in the daily
    management loop. Returns None when the symbol has no upcoming filing on
    record so that missing calendar data never forces an exit."""
    dates = earnings_by_symbol.get(sym)
    if not dates:
        return None
    i = bisect_left(dates, ref_day)
    return (dates[i] - ref_day).days if i < len(dates) else None


async def run_backtest(run_id: int, pool) -> None:
    run = await pool.fetchrow("SELECT * FROM backtest_runs WHERE id = $1", run_id)
    if run is None:
        return

    try:
        r = dict(run)
        if (r.get("strategy") or "BREAKOUT") == "PORTFOLIO":
            # Continuous compounding book with portfolio-level risk controls.
            # Reports path metrics (CAGR, ulcer, worst rolling 12m) that the
            # summary endpoint cannot derive from trade rows, so it stores them
            # on the run row itself — see sql/022.
            from .portfolio_run import run_portfolio_persisted
            await run_portfolio_persisted(r, pool)
            return
        if (r.get("strategy") or "BREAKOUT") == "POSITIONAL":
            # Different strategy shape entirely (a rebalancing portfolio, not
            # one-signal-per-symbol-per-day), so it has its own engine. It
            # writes the same backtest_trades rows, so every review surface —
            # run list, trade log, equity curve, P&L — works unchanged.
            from .positional_engine import run_positional
            await run_positional(r, pool)
            return
        if (r.get("strategy") or "BREAKOUT") == "WEEKLY_BREAKOUT":
            # Weekly Consolidation Breakout strategy — entirely separate
            # timeframe/indicators/exit mechanism, see weekly_engine.py.
            # Writes the same backtest_trades rows (quant_rank=1), so it
            # shows up in the existing run/trade-log/equity-curve surfaces.
            from .weekly_engine import run_weekly_backtest
            await run_weekly_backtest(r, pool)
            return
        if (r.get("strategy") or "BREAKOUT") == "INDEX_TF":
            # Index trend following (2026-08-17) — takes no stock-selection
            # risk at all, holds one index proxy long or sits in cash. Built
            # specifically as a diversifier for the single-stock breakout book
            # (measured monthly-return correlation 0.015), see index_tf_engine.
            from .index_tf_engine import run_index_tf
            await run_index_tf(r, pool)
            return
        await _run(r, pool)
    except Exception as e:
        logger.exception("Backtest run %s failed", run_id)
        await pool.execute(
            "UPDATE backtest_runs SET status='FAILED', error=$2, completed_at=NOW() WHERE id=$1",
            run_id, f"{e}\n{traceback.format_exc()[-2000:]}",
        )


def _prepare_screen_gpt_for_backtest() -> None:
    """Two backtest-only speedups applied to the in-memory screen_gpt module
    (never to screen_gpt.py on disk, and never to the live screener — this
    runs inside the run's own throwaway subprocess).

    1. Warm the tick-size cache ONCE, single-threaded, before anything else.
       screen_gpt.load_tick_sizes() downloads a ~10k-row CSV over the network
       (images.dhan.co/api-data/api-scrip-master.csv) and builds its cache with
       a row-by-row iterrows() loop. It's guarded by `if TICK_SIZE_CACHE:` —
       fine single-threaded, but _compute_signals_concurrent() calls into it
       from a thread pool, so on a cold cache several threads all see it empty
       at once and each kicks off its own download+parse. Profiling a cold run
       showed that banner printing repeatedly, i.e. the CSV was being fetched
       and parsed several times per run. Priming it here makes the guard hit
       on every subsequent call.

    2. Silence screen_gpt's verbose per-symbol debug logging. DEBUG=True emits
       several print() lines per symbol per day (base-stage counts, entry
       detection, tick rounding, target). Across ~70 survivors x ~150 days
       that's tens of thousands of lines written to the run's log file, all of
       it noise for a backtest — the run's results live in Postgres, not the
       log. Errors/tracebacks still surface: they go through the logging
       module and engine-level exception handling, not dbg().
    """
    # Imported lazily, NOT at module scope: funnel.py is what inserts
    # /root/trade-execution-webhook onto sys.path, so a top-level
    # `import screen_gpt` here runs before that path exists and dies with
    # ModuleNotFoundError. By this point funnel has been imported.
    import screen_gpt

    try:
        screen_gpt.load_tick_sizes()
    except Exception:
        logger.warning("tick-size cache warm-up failed; continuing", exc_info=True)
    screen_gpt.DEBUG = False


async def _run(run: dict, pool) -> None:
    _prepare_screen_gpt_for_backtest()
    run_id = run["id"]
    start_date, end_date = run["start_date"], run["end_date"]

    # Mark run as started with timestamp
    import time
    exec_start_time = time.time()
    await pool.execute(
        "UPDATE backtest_runs SET started_at=NOW() WHERE id=$1", run_id
    )
    track_mode = run["track_mode"]
    capital = float(run["capital"])
    resting_window_days = run["resting_window_days"]
    stacking_guard = run["stacking_guard"]
    stacking_guard_mode = run["stacking_guard_mode"]
    min_position_value = float(run.get("min_position_value") or 0)
    max_picks_per_track = int(run.get("max_picks_per_track") or 3)
    # Stage 1 gate-threshold overrides (sql/007_backtest_stage1_gates.sql) --
    # any subset of the 8 SQL gate thresholds, NULL = production default.
    # An empty dict means funnel_v2's gate reproduces funnel.py's exactly, so
    # we only pay the funnel_v2 code path when there's something to override
    # (a gate override and/or the separately-tested v2 ranking experiment).
    gate_overrides = {
        k: float(run[f"gate_{k}"]) for k in (
            "min_turnover_cr", "max_base_range_pct", "min_vol_mult",
            "min_prior_upmove_pct", "max_giveback_pct", "max_vol_dryup_ratio",
            "max_dist_from_high_pct", "min_ifp_score",
        ) if run.get(f"gate_{k}") is not None
    }
    use_v2_ranking = run.get("quant_funnel_variant") == "v2"
    use_funnel_v2 = bool(gate_overrides) or use_v2_ranking
    # Backtest-only AI re-ranking (sql/009) — see _rank_by_recommendation_then_confidence
    # docstring. Applied downstream of pipeline.analyze_symbols(), which is left
    # completely untouched, so this can never affect production/live trading.
    ai_respect_recommendation = bool(run.get("ai_respect_recommendation"))
    # Market-breadth entry filter (sql/011) — skip NEW entries on days where
    # too much of the market is already above its 200SMA (late-cycle entries
    # measurably underperform in both validation windows; see the migration's
    # comment for the numbers). Gates entries only — open positions keep
    # being managed/exited normally, so this can never strand a position.
    entry_breadth_max_pct = (float(run["entry_breadth_max_pct"])
                             if run.get("entry_breadth_max_pct") is not None else None)
    # sql/012 — direction half of the same filter: only enter while breadth is
    # at/above its own trailing 20-session average (early in a recovery leg)
    # rather than still falling. See that migration for the measured split.
    entry_breadth_require_rising = bool(run.get("entry_breadth_require_rising"))
    # Position sizing overrides (sql/013) — None keys fall back to production's
    # hardcoded 0.25% risk / 10% max-capital per trade inside funnel._size_qty.
    sizing = {
        "risk_per_trade_pct": (float(run["risk_per_trade_pct"])
                               if run.get("risk_per_trade_pct") is not None else None),
        "max_capital_per_trade_pct": (float(run["max_capital_per_trade_pct"])
                                      if run.get("max_capital_per_trade_pct") is not None else None),
    }
    # Base-stage allocation ladder (sql/024). 'prod' leaves sizing to
    # screen_gpt's live dict; 'v2' is the monotonic ladder — only Base 2 differs
    # (1.00 -> 0.75) and the 1.00 ceiling is unchanged, so no position can
    # exceed the 10%-of-capital / 0.25%-risk limits production already runs.
    if (run.get("base_stage_ladder") or "prod") == "v2":
        sizing["stage_multipliers"] = {1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25,
                                       "default": 0.25}

    # Create unified position sizer with compounding support
    sizer = PositionSizer(
        initial_capital=capital,
        risk_per_trade_pct=float(run.get("risk_per_trade_pct") or 0.25),
        max_capital_per_trade_pct=float(run.get("max_capital_per_trade_pct") or 10),
        compounding_enabled=bool(run.get("compounding_enabled") or False),
        compounding_mode=str(run.get("compounding_mode") or "profit_only"),
        compounding_min_capital=float(run.get("compounding_min_capital") or capital),
        min_position_value=min_position_value,
        # 2026-08-17 risk-audit guards, previously wired only into the weekly
        # engine — both None by default so every pre-audit run reproduces.
        compounding_max_capital=(float(run["compounding_max_capital"])
                                  if run.get("compounding_max_capital") is not None else None),
        adv_position_cap_pct=(float(run["adv_position_cap_pct"])
                               if run.get("adv_position_cap_pct") is not None else None),
    )
    logger.info("run %s: PositionSizer initialized: %s", run_id, sizer.get_capital_status())
    entry_v2 = bool(run.get("entry_v2_buy_points"))
    # VCP-style contraction gate (sql/014) — see funnel.contraction_ratios()
    # and that migration for the measured, cross-window edge.
    max_contraction_ratio = (float(run["max_contraction_ratio"])
                             if run.get("max_contraction_ratio") is not None else None)
    # sql/015 — skip candidates whose stop distance is too small a fraction of
    # price to clear round-trip costs (measured: the <3%-of-price band has a
    # NEGATIVE net edge). Entry gate only.
    min_risk_pct_of_price = (float(run["min_risk_pct_of_price"])
                             if run.get("min_risk_pct_of_price") is not None else None)
    # sql/017 — earnings-event rules. See that migration for the look-ahead
    # discipline these must be read under: they are only legitimate at short
    # lead times, because that is all the advance notice SEBI actually requires.
    avoid_entry_days_before_earnings = run.get("avoid_entry_days_before_earnings")
    exit_days_before_earnings = run.get("exit_days_before_earnings")
    earnings_by_symbol: dict = {}
    if avoid_entry_days_before_earnings or exit_days_before_earnings:
        # One fetch for the whole window (plus a tail, so a position still open
        # at the window end can still see its upcoming filing), then an in-memory
        # sorted list per symbol — the per-day lookup is a bisect, not a query.
        er = await pool.fetch(
            """
            SELECT symbol, broadcast_date FROM earnings_filings
            WHERE broadcast_date BETWEEN $1::date - 10 AND $2::date + 120
            ORDER BY symbol, broadcast_date
            """,
            start_date, end_date,
        )
        for row in er:
            earnings_by_symbol.setdefault(row["symbol"], []).append(row["broadcast_date"])
    # sql/019 — regime state machine with hysteresis. Precomputed for the whole
    # window into a {day: 'OFFENSIVE'|'DEFENSIVE'} map before the day loop.
    regime_ma_days = run.get("regime_ma_days")
    regime_confirm_days = run.get("regime_confirm_days")
    regime_action = run.get("regime_action")
    regime_by_day: dict = {}
    if regime_ma_days and regime_confirm_days and regime_action:
        rrows = await pool.fetch(
            """
            SELECT snapshot_date, pct200,
                   AVG(pct200) OVER (ORDER BY snapshot_date
                                     ROWS BETWEEN $3 PRECEDING AND CURRENT ROW) AS ma
            FROM (
              SELECT snapshot_date,
                     count_above_200sma::float / NULLIF(eligible_stocks,0) * 100 AS pct200
              FROM market_snapshot
              WHERE snapshot_date BETWEEN $1::date - 400 AND $2
            ) s
            WHERE pct200 IS NOT NULL
            ORDER BY snapshot_date
            """,
            run["start_date"], run["end_date"], int(regime_ma_days),
        )
        # Walk forward maintaining the state. Starts OFFENSIVE: assuming
        # DEFENSIVE at t=0 would silently skip the opening stretch of every
        # window and flatter any config that happens to start in a bad patch.
        state, streak, last = "OFFENSIVE", 0, None
        for rr in rrows:
            healthy = rr["ma"] is not None and rr["pct200"] >= rr["ma"]
            want = "OFFENSIVE" if healthy else "DEFENSIVE"
            streak = streak + 1 if want == last else 1
            last = want
            if want != state and streak >= int(regime_confirm_days):
                state = want
            regime_by_day[rr["snapshot_date"]] = state
    breadth_by_day: dict = {}
    if entry_breadth_max_pct is not None or entry_breadth_require_rising:
        # Window starts well before start_date so the 20-session average is
        # already fully formed on day 1 of the run (otherwise the first ~20
        # days would compare against a partial, biased average).
        breadth_rows = await pool.fetch(
            """
            SELECT snapshot_date, pct200,
                   AVG(pct200) OVER (ORDER BY snapshot_date
                                     ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS ma20
            FROM (
              SELECT snapshot_date,
                     count_above_200sma::float / NULLIF(eligible_stocks, 0) * 100 AS pct200
              FROM market_snapshot
              WHERE snapshot_date BETWEEN $1::date - 90 AND $2
            ) s
            WHERE pct200 IS NOT NULL
            """,
            run["start_date"], run["end_date"],
        )
        breadth_by_day = {r["snapshot_date"]: (r["pct200"], r["ma20"]) for r in breadth_rows}
    # Stage 2 (base-stage + entry-technique) overrides (sql/008) — monkeypatches
    # screen_gpt's constants for this subprocess's lifetime; see funnel_stage2.py
    # docstring for why this must use its own config-hash-keyed cache (sql/010)
    # instead of the shared (non-config-aware) quant-signal cache.
    stage2_active = funnel_stage2.apply_overrides(run)
    stage2_config_hash = funnel_stage2.config_hash() if stage2_active else None
    exit_config = run["exit_config"] if isinstance(run["exit_config"], dict) else {}
    import json as _json
    if isinstance(run["exit_config"], str):
        exit_config = _json.loads(run["exit_config"])

    # Numeric cost/safety-SL settings live as their own backtest_runs columns
    # (see sql/004_backtest_costs_and_rules.sql, sql/005_backtest_dhan_costs.sql)
    # — merge them into the same exit_config dict so simulator.py's functions
    # only need one param.
    exit_config = {
        **exit_config,
        "safety_sl_pct": float(run["safety_sl_pct"]),
        "slippage_pct": float(run["slippage_pct"]),
        "brokerage_per_order": float(run["brokerage_per_order"]),
        "chandelier_atr_mult": float(run["chandelier_atr_mult"]),
        "stt_pct": float(run["stt_pct"]),
        "stamp_duty_pct": float(run["stamp_duty_pct"]),
        "exchange_charges_pct": float(run["exchange_charges_pct"]),
        "dp_charge": float(run["dp_charge"]),
        "max_holding_days": run.get("max_holding_days"),
        # Audit V4 — heavier slippage on sell legs (stressed stop exits).
        # None -> _sell_fill falls back to slippage_pct, pre-audit behaviour.
        "exit_slippage_pct": (float(run["exit_slippage_pct"])
                               if run.get("exit_slippage_pct") is not None else None),
    }
    needs_ema_atr = any(exit_config.get(k) for k in
                         ("ema10_trail", "ema21_trail", "ema50_trail", "chandelier_trail"))
    needs_swing = any(exit_config.get(k) for k in ("swing_trail", "swing_break_exit"))
    # sql/028-era experiment (run #589 analysis, 2026-08-14): reuse the
    # WEEKLY_BREAKOUT strategy's own MACD-crossover trail rule as an option
    # here. Cached per-symbol (not re-fetched every day) since a position can
    # stay OPEN for months — see weekly_breakout.macd_ratchet_series.
    needs_macd_trail = bool(exit_config.get("macd_trail"))
    macd_series_cache: dict[str, list] = {}

    # sql/028 — only allow a new entry if the symbol ALSO had a qualifying
    # weekly consolidation-box breakout signal (same definition as the
    # WEEKLY_BREAKOUT strategy) within the last N days. Precomputed once,
    # up front, for the whole run/universe/window — identical in spirit to
    # breadth_by_day/regime_by_day above, and reuses weekly_engine's own
    # Phase A signal scan so the definition can never drift from the
    # strategy it's borrowed from.
    require_weekly_box = bool(run.get("require_weekly_box_breakout"))
    weekly_box_lookback_days = int(run.get("weekly_box_lookback_days") or 10)
    weekly_box_week_ends: dict[str, list] = {}
    if require_weekly_box:
        from . import weekly_engine
        weekly_box_week_ends = await weekly_engine.raw_breakout_week_ends(pool, start_date, end_date)
        logger.info("run %s: weekly-box entry gate precomputed for %d symbols",
                    run_id, len(weekly_box_week_ends))

    def _has_recent_weekly_breakout(sym: str, ref_day: date) -> bool:
        weeks = weekly_box_week_ends.get(sym)
        if not weeks:
            return False
        i = bisect_right(weeks, ref_day)  # count of weeks with week_end <= ref_day
        if i == 0:
            return False
        return (ref_day - weeks[i - 1]).days <= weekly_box_lookback_days

    # Strategy 2 ("Breakout from Volatility Compression" — user spec,
    # 2026-08-14): entirely different candidate SOURCE, same daily trade
    # lifecycle (SimTrade/try_fill/step_exit, unmodified) — see
    # funnel_squeeze.py. Precomputed once, up front, exactly like
    # weekly_box_week_ends above, so the day loop only ever does an O(1)
    # dict lookup instead of re-scanning the universe every session.
    squeeze_signals_by_day: dict = {}
    if run.get("strategy") == "SQUEEZE_BREAKOUT":
        from . import funnel_squeeze
        squeeze_signals_by_day = await funnel_squeeze.scan_all(
            pool, start_date, end_date,
            volume_multiplier=float(run.get("squeeze_volume_multiplier")
                                     or funnel_squeeze.DEFAULT_VOLUME_MULTIPLIER),
            capital=capital,
            risk_pct=float(run.get("risk_per_trade_pct") or funnel_squeeze.DEFAULT_RISK_PCT),
            max_capital_pct=float(run.get("max_capital_per_trade_pct")
                                   or funnel_squeeze.DEFAULT_MAX_CAPITAL_PCT),
        )
        logger.info("run %s: SQUEEZE_BREAKOUT precomputed %d signal-days",
                    run_id, len(squeeze_signals_by_day))

    # Strategy 3 ("Mean Reversion on High-Quality Stocks" — user spec,
    # 2026-08-14): same idea, see funnel_rsi.py.
    rsi_signals_by_day: dict = {}
    if run.get("strategy") == "RSI_REVERSION":
        from . import funnel_rsi
        rsi_signals_by_day = await funnel_rsi.scan_all(
            pool, start_date, end_date,
            rsi_entry_threshold=float(run.get("rsi_entry_threshold")
                                       or funnel_rsi.DEFAULT_RSI_ENTRY_THRESHOLD),
            stop_pct=float(run.get("rsi_stop_pct") or funnel_rsi.DEFAULT_STOP_PCT),
            target_pct=float(run.get("rsi_target_pct") or funnel_rsi.DEFAULT_TARGET_PCT),
            capital=capital,
            risk_pct=float(run.get("risk_per_trade_pct") or funnel_rsi.DEFAULT_RISK_PCT),
            max_capital_pct=float(run.get("max_capital_per_trade_pct")
                                   or funnel_rsi.DEFAULT_MAX_CAPITAL_PCT),
        )
        logger.info("run %s: RSI_REVERSION precomputed %d signal-days",
                    run_id, len(rsi_signals_by_day))

    day_rows = await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
        start_date, end_date,
    )
    trading_days: list[date] = [r["d"] for r in day_rows]
    total_days = len(trading_days)
    await pool.execute(
        "UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2", total_days, run_id
    )

    screener_repo = PgRepo(pool)
    ai_repo = BacktestAiRepo(pool)

    active: list[SimTrade] = []          # PENDING or OPEN trades, not yet persisted-final

    # Performance optimization: Pre-warm in-process OHLCV cache to avoid repeated
    # database queries. Maps (symbol, date) → {open, high, low, close, ema10, ema21, ema50, atr14}.
    # Safe: read-only immutable data, local to this run process. Reduces per-day query cost by 50-70%.
    ohlcv_cache: dict[tuple, dict] = {}
    logger.info("run %s: pre-warming OHLCV cache for %d days...", run_id, total_days)
    _ohlcv_cache_hits = 0
    _ohlcv_cache_misses = 0

    # ---- which sessions generate signals -----------------------------------
    # 'daily' (production today) scans every session. 'weekly'/'monthly' scan
    # once per calendar period, on either the first or last session of it.
    #
    # Scan day is a PHASE choice, not an information one: a scan on any day
    # sees all data up to that day and none beyond it. Friday's scan has four
    # more sessions than Monday's at that instant, but next Monday's scan has
    # them too. 'last' is the default because it matches how a weekend review
    # actually works — decide with the full period visible, act on the next
    # open. Both are offered so the choice can be shown not to matter.
    cadence = (run.get("signal_cadence") or "daily").lower()
    scan_at = (run.get("signal_scan_day") or "last").lower()
    if cadence == "daily":
        scan_day_idx = set(range(len(trading_days)))
    else:
        buckets: dict[tuple, list[int]] = {}
        for i, d in enumerate(trading_days):
            key = ((d.year, d.isocalendar()[1]) if cadence == "weekly"
                   else (d.year, d.month))
            buckets.setdefault(key, []).append(i)
        scan_day_idx = {(idx[0] if scan_at == "first" else idx[-1])
                        for idx in buckets.values()}
    logger.info("run %s: cadence=%s scan_at=%s -> %d scan days of %d sessions",
                run_id, cadence, scan_at, len(scan_day_idx), len(trading_days))

    for day_idx, day in enumerate(trading_days):
        symbols_today = {t.symbol for t in active}
        bars_by_symbol = {}
        if symbols_today:
            # Check cache for previously fetched symbols, fetch missing ones
            cached_symbols = [s for s in symbols_today if (s, day) in ohlcv_cache]
            missing_symbols = [s for s in symbols_today if (s, day) not in ohlcv_cache]
            _ohlcv_cache_hits += len(cached_symbols)
            _ohlcv_cache_misses += len(missing_symbols)

            # Fetch only missing symbols from database
            if missing_symbols:
                rows = await pool.fetch(
                    """
                    SELECT o.symbol, o.open, o.high, o.low, o.close,
                           si.ema_10, si.ema_21, si.ema_50, si.atr_14
                    FROM ohlcv_data o
                    LEFT JOIN stock_indicators si
                      ON si.symbol = o.symbol AND si.indicator_date = o.time::date
                    WHERE o.symbol = ANY($1) AND o.time::date = $2
                    """,
                    missing_symbols, day,
                )
                for r in rows:
                    bar_data = {
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "ema10": float(r["ema_10"]) if needs_ema_atr and r["ema_10"] is not None else None,
                        "ema21": float(r["ema_21"]) if needs_ema_atr and r["ema_21"] is not None else None,
                        "ema50": float(r["ema_50"]) if needs_ema_atr and r["ema_50"] is not None else None,
                        "atr14": float(r["atr_14"]) if needs_ema_atr and r["atr_14"] is not None else None,
                    }
                    ohlcv_cache[(r["symbol"], day)] = bar_data

            # Build bars from cache
            bars_by_symbol = {}
            for sym in symbols_today:
                if (sym, day) in ohlcv_cache:
                    bars_by_symbol[sym] = ohlcv_cache[(sym, day)]

            if needs_swing:
                swing_rows = await pool.fetch(
                    """
                    SELECT symbol, d, high, low FROM (
                      SELECT symbol, time::date AS d, high, low,
                             row_number() OVER (PARTITION BY symbol ORDER BY time DESC) AS rn
                      FROM ohlcv_data
                      WHERE symbol = ANY($1) AND time::date <= $2
                    ) sub
                    WHERE rn <= $3
                    ORDER BY symbol, d
                    """,
                    list(symbols_today), day, SWING_LOOKBACK_DAYS,
                )
                by_symbol: dict[str, list] = {}
                for r in swing_rows:
                    by_symbol.setdefault(r["symbol"], []).append(
                        {"high": float(r["high"]), "low": float(r["low"])}
                    )
                for sym, hist in by_symbol.items():
                    if sym in bars_by_symbol and len(hist) >= 11:  # SWING_WIN=5 each side
                        df = pd.DataFrame(hist)
                        bars_by_symbol[sym]["swing_low"] = last_swing_low(df)

            if needs_macd_trail:
                # Fetched/computed ONCE per symbol for the whole run (a
                # position can stay OPEN for months — see run #589's HEXT
                # trade, 1813 days), not re-derived every day it's active.
                for sym in symbols_today:
                    if sym not in bars_by_symbol:
                        continue
                    if sym not in macd_series_cache:
                        macd_series_cache[sym] = await wb.macd_ratchet_series(
                            pool, sym, end_date,
                            fast=int(exit_config.get("macd_fast") or 12),
                            slow=int(exit_config.get("macd_slow") or 26),
                            sig=int(exit_config.get("macd_sig") or 9),
                            level_mode=str(exit_config.get("macd_level") or "low"))
                    bars_by_symbol[sym]["macd_trail_level"] = wb.macd_trail_level_at(
                        macd_series_cache[sym], day)

        # 1. fills + exits for everything already active. State transitions
        #    (try_fill/step_exit) are synchronous/local, so do those first,
        #    then persist every trade CONCURRENTLY (asyncio.gather) instead
        #    of one sequential awaited DB round-trip per trade — on days
        #    with a dozen+ open positions this was the single biggest
        #    contributor to per-day wall-clock time. Safe to parallelize:
        #    each trade owns its own row (db_id), no shared mutable state.
        still_active = []
        for t in active:
            bar = bars_by_symbol.get(t.symbol)
            if (exit_days_before_earnings and t.status == "OPEN" and bar is not None):
                dte = _days_to_earnings_outer(earnings_by_symbol, t.symbol, day)
                if dte is not None and dte <= int(exit_days_before_earnings):
                    close_trade(t, day, round(bar["close"], 2), "PRE_EARNINGS", exit_config)
            if bar is not None and t.status == "OPEN":
                step_exit(t, day, bar, exit_config)
            if bar is not None and t.status == "PENDING":
                since = day_idx - t.signal_day_idx
                try_fill(t, day, bar, resting_window_days, since, cfg=exit_config)
                if t.status == "OPEN":
                    step_exit(t, day, bar, exit_config)
            if t.status in ("PENDING", "OPEN"):
                still_active.append(t)
            elif t.status == "CLOSED":
                # Record closed trade's realized P&L for compounding
                sizer.record_trade_closed(realized_pnl=float(t.realized_pnl or 0))
        if active:
            await asyncio.gather(*(_persist(pool, run_id, t) for t in active))
        active = still_active

        # 2. today's candidates — but only on a SCAN day.
        #
        # Production scans nightly. signal_cadence lets the same funnel run
        # weekly or monthly instead, which is the low-turnover question the
        # portfolio work raised: is the breakout edge destroyed by trading it
        # every day rather than by the picks themselves?
        #
        # Only SIGNAL GENERATION is gated. Exits, fills and mark-to-market above
        # continue to run every session — a weekly scan must not mean a weekly
        # stop-loss, which would be a different (and far more dangerous) system.
        if day_idx not in scan_day_idx:
            continue

        # Portfolio-level exposure cap (2026-08-17) — ₹ already committed to
        # OTHER currently-OPEN positions, so today's new entries are sized
        # against what's actually still available rather than the full
        # running capital in isolation. Without this, each new position was
        # sized independently off total capital with no regard for how many
        # other positions were already open, letting total concurrent
        # exposure run to several multiples of the stated capital on a
        # long-lived book. PENDING (not yet filled) trades aren't counted —
        # no capital is actually deployed until fill. Applied identically to
        # every candidate in today's batch (doesn't account for other picks
        # from this SAME day — see build_candidates() docstring), so
        # same-day multi-picks can still collectively overshoot by a bounded
        # amount; cross-day accumulation is fully capped.
        committed_capital = sum(
            (t.entry_fill_price or 0.0) * t.qty_remaining
            for t in active if t.status == "OPEN"
        )

        if run.get("strategy") == "SQUEEZE_BREAKOUT":
            candidates = squeeze_signals_by_day.get(day, [])
        elif run.get("strategy") == "RSI_REVERSION":
            candidates = rsi_signals_by_day.get(day, [])
        elif stage2_active:
            candidates = await funnel_stage2.build_candidates(
                pool, day, capital, stage2_config_hash, gate_overrides, use_v2_ranking, sizing, sizer,
                committed_capital,
            )
        elif use_funnel_v2:
            candidates = await funnel_v2.build_candidates(
                pool, day, capital, gate_overrides, use_v2_ranking, sizing, sizer,
                committed_capital,
            )
        else:
            candidates = await funnel.build_candidates(pool, day, capital, sizing, sizer, committed_capital)
        # sql/028 — require a recent weekly consolidation-box breakout too
        # (see run #589's analysis, 2026-08-14: the weekly strategy's box +
        # volume-expansion + 10-week-closing-high definition of "breakout" is
        # coarser/less noisy than the daily funnel's own stage gates alone).
        # Applied here, right after the funnel dispatch, so it's uniform
        # across all three funnel paths and only ever REMOVES rows.
        if require_weekly_box and candidates:
            candidates = [c for c in candidates if _has_recent_weekly_breakout(c["symbol"], day)]
        # VCP contraction gate — drop candidates whose base isn't actually
        # tightening into the pivot. Applied after the funnel builds/ranks
        # candidates so it's uniform across all three funnel paths, and only
        # ever removes rows (relative ranking of survivors is preserved).
        # A symbol missing from the map (data gap) is kept, not dropped.
        # ---- ENTRY V2: require a BUY POINT as well as a trigger candle.
        # The deck separates WHERE in the base we are from WHETHER today's bar
        # is actionable; production only ever asks the second. Applied here,
        # after ranking, so it is uniform across all three funnel paths and only
        # ever REMOVES rows — relative ranking of survivors is preserved.
        # Measured to cut survivors from ~100% to ~38% (ENTRY_V2_SPEC).
        if entry_v2 and candidates:
            from .buy_points import detect_buy_points
            bp_frames = await funnel.load_ohlcv_frames_batch(
                pool, [c["symbol"] for c in candidates], day)
            kept = []
            for c_ in candidates:
                bps = detect_buy_points(bp_frames.get(c_["symbol"]), c_["symbol"])
                if bps:
                    c_["buy_points"] = ",".join(bps)
                    kept.append(c_)
            candidates = kept

        if max_contraction_ratio is not None and candidates:
            ratios = await funnel.contraction_ratios(
                pool, [c["symbol"] for c in candidates], day)
            candidates = [c for c in candidates
                          if ratios.get(c["symbol"], 0.0) <= max_contraction_ratio]
        def _days_to_earnings(sym, ref_day):
            """Calendar days from ref_day to that symbol's next results
            broadcast, or None if we have no upcoming filing on record.
            Absent data means 'no constraint' — a gap in the harvested
            calendar must never silently block or force trades."""
            dates = earnings_by_symbol.get(sym)
            if not dates:
                return None
            i = bisect_left(dates, ref_day)
            return (dates[i] - ref_day).days if i < len(dates) else None

        # Regime gate: applied to the ranked candidate list, entries only.
        if regime_by_day and candidates:
            if regime_by_day.get(day) == "DEFENSIVE":
                if regime_action == "block":
                    candidates = []
                elif regime_action == "half":
                    candidates = candidates[:max(1, max_picks_per_track // 2)]
        if avoid_entry_days_before_earnings and candidates:
            n_days = int(avoid_entry_days_before_earnings)
            candidates = [c for c in candidates
                          if (lambda d: d is None or d > n_days)(_days_to_earnings(c["symbol"], day))]
        if min_risk_pct_of_price is not None and candidates:
            candidates = [c for c in candidates
                          if c["entry"] > 0
                          and (c["risk_per_share"] / c["entry"] * 100) >= min_risk_pct_of_price]
        cand_by_symbol = {c["symbol"]: c for c in candidates}

        quant_top3 = candidates[:max_picks_per_track] if track_mode in ("QUANT", "BOTH") else []

        ai_top3 = []
        if track_mode in ("AI", "BOTH") and candidates:
            try:
                # chart_scope="daily" (not "both") and a low MAX_CONCURRENT_AI
                # (set via env by the API when it spawns this subprocess) —
                # the VPS is 961MB RAM total; concurrent daily+weekly chart
                # rendering at concurrency 5 OOM-killed the process in testing.
                result = await analyze_symbols(
                    [c["symbol"] for c in candidates], day, screener_repo, ai_repo,
                    ai_mode="gemini", chart_scope="daily", prompt_version=ai_config.PROMPT_VERSION,
                    # Backtest UI never reads backtest_ai_signals' chart_*_path
                    # columns — it shows its own separate per-trade chart (see
                    # backtest/chart.py) — so both the annotated render AND
                    # writing any chart file to disk are pure waste here.
                    persist_charts=False,
                )
                ai_results = result.get("results", [])
                if ai_respect_recommendation:
                    ai_results = _rank_by_recommendation_then_confidence(ai_results)
                for r in ai_results:
                    if r.get("error") or r["symbol"] not in cand_by_symbol:
                        continue
                    ai_top3.append({
                        **cand_by_symbol[r["symbol"]],
                        "ai_confidence": (r.get("analysis") or {}).get("confidence"),
                        "ai_recommendation": (r.get("analysis") or {}).get("recommendation"),
                    })
                    if len(ai_top3) == max_picks_per_track:
                        break
            except Exception:
                logger.exception("AI analysis failed for %s — continuing quant-only that day", day)

        # 3. merge picks (one trade per symbol, tracked in both ranks if it
        #    appears in both lists — spec §8.1)
        picks: dict[str, dict] = {}
        for i, c in enumerate(quant_top3, 1):
            picks.setdefault(c["symbol"], {"candidate": c})["quant_rank"] = i
        for i, c in enumerate(ai_top3, 1):
            d = picks.setdefault(c["symbol"], {"candidate": c})
            d["ai_rank"] = i
            d["ai_confidence"] = c.get("ai_confidence")
            d["ai_recommendation"] = c.get("ai_recommendation")

        # 4. create new trades, respecting the stacking guard.
        #    Breadth gate first: on an over-extended-market day take no new
        #    entries at all (picks are still computed above so the day's
        #    candidate list/ranking is unchanged — only acting on it stops).
        #    A day with no market_snapshot row is treated as "no data, don't
        #    block" so a missing snapshot can't silently halt the whole run.
        if breadth_by_day:
            today_breadth = breadth_by_day.get(day)
            if today_breadth is not None:
                pct200_today, ma20_today = today_breadth
                too_extended = (entry_breadth_max_pct is not None
                                and pct200_today >= entry_breadth_max_pct)
                still_falling = (entry_breadth_require_rising and ma20_today is not None
                                 and pct200_today < ma20_today)
                if too_extended or still_falling:
                    picks = {}
        for sym, info in picks.items():
            c0 = info["candidate"]
            if min_position_value and c0["entry"] * c0["qty"] < min_position_value:
                # Flat per-trade costs (DP charge, stamp duty) disproportionately
                # tax tiny positions — skip signals too small to size efficiently
                # rather than take them anyway (see cost-drag comparison series).
                continue
            existing = [t for t in active if t.symbol == sym]
            if stacking_guard and existing:
                open_ones = [t for t in existing if t.status == "OPEN"]
                pending_ones = [t for t in existing if t.status == "PENDING"]
                if open_ones:
                    continue  # always skip — can't override a filled position
                if pending_ones:
                    if stacking_guard_mode == "OVERRIDE":
                        for t in pending_ones:
                            t.status = "SUPERSEDED"
                            t.exit_reason = "SUPERSEDED"
                            await _persist(pool, run_id, t)
                            active.remove(t)
                    else:  # SKIP (default)
                        continue

            c = info["candidate"]
            trade = SimTrade(
                symbol=sym, signal_date=day, entry_trigger_price=c["entry"],
                structural_sl=c["sl"], target_price=c["target"],
                risk_per_share=c["risk_per_share"], quantity=c["qty"],
                entry_type=c["entry_type"], base_stage=c["base_stage"],
                quant_rank=info.get("quant_rank"), ai_rank=info.get("ai_rank"),
                ai_confidence=info.get("ai_confidence"), ai_recommendation=info.get("ai_recommendation"),
            )
            trade.signal_day_idx = day_idx
            await _persist(pool, run_id, trade)
            active.append(trade)

        await pool.execute(
            "UPDATE backtest_runs SET progress_day=$1 WHERE id=$2", day_idx + 1, run_id
        )

    # Window end: whatever's left in `active` simply stays PENDING/OPEN in
    # the DB (already persisted above every day) — no forced close, per spec.
    cache_hit_rate = (_ohlcv_cache_hits / (_ohlcv_cache_hits + _ohlcv_cache_misses) * 100
                      if (_ohlcv_cache_hits + _ohlcv_cache_misses) > 0 else 0)
    logger.info("run %s: OHLCV cache stats: %d hits, %d misses (%.1f%% hit rate, size=%d KB)",
                run_id, _ohlcv_cache_hits, _ohlcv_cache_misses, cache_hit_rate,
                len(ohlcv_cache) * 100 // 1024)  # rough estimate of cache size

    # Compute and store CAGR/MaxDD for non-PORTFOLIO runs (PORTFOLIO has these pre-computed)
    strategy = run.get("strategy") or "BREAKOUT"
    if strategy != "PORTFOLIO":
        try:
            # 2026-08-17: MARK-TO-MARKET path stats (path_stats.py) replace the
            # old realized-only reconstruction that used to live here — the
            # risk audit measured realized-only MaxDD understating true
            # drawdown by 12-25pts (open positions carried at cost until
            # exit). Also fills worst-12m / Martin / underwater-duration,
            # which this engine never produced at all.
            from .path_stats import compute_and_store_mtm_stats
            await compute_and_store_mtm_stats(pool, run_id)
        except Exception as e:
            logger.warning("run %s: failed to compute CAGR/maxDD: %s", run_id, e)

    # Calculate execution time and update completion
    exec_time_seconds = int(time.time() - exec_start_time)
    await pool.execute(
        "UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW(), exec_seconds=$1 WHERE id=$2",
        exec_time_seconds, run_id
    )
    logger.info("run %s: completed in %d seconds (%.1f minutes)", run_id, exec_time_seconds, exec_time_seconds / 60)


_AI_REC_TIER = {"SETUP_READY": 0, "EARLY_STAGE": 1, "NOT_READY": 2, "AVOID": 3}


def _rank_by_recommendation_then_confidence(results: list[dict]) -> list[dict]:
    """Re-rank pipeline.analyze_symbols()'s output by Gemini's own stated
    recommendation tier first, confidence as the tie-break within a tier —
    instead of production's current behavior (pipeline.py sorts by raw
    confidence alone, recommendation-blind). Only ever called when a run
    opts into ai_respect_recommendation=True; pipeline.py itself is
    untouched, so this cannot affect production/live trading, only this
    backtest run's own AI-track picks."""
    def key(r):
        rec = (r.get("analysis") or {}).get("recommendation")
        conf = (r.get("analysis") or {}).get("confidence") or 0
        return (_AI_REC_TIER.get(rec, 4), -conf)
    return sorted(results, key=key)


async def _persist(pool, run_id: int, t: SimTrade) -> None:
    """Insert on first sight of a trade, update on every subsequent state
    change. Tracked via t.db_id (set on the object itself once INSERTed) —
    NOT a dict keyed by Python's id(t)/object memory address, which was a
    real bug: a garbage-collected trade's address can be reused by a later,
    unrelated trade, silently redirecting its updates into the old trade's
    row instead of getting a new one (confirmed to have actually happened —
    merged two different symbols' data into a single backtest_trades row)."""
    if t.db_id is None:
        row = await pool.fetchrow(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, quant_rank, ai_rank, signal_date, entry_trigger_price,
               structural_sl, target_price, risk_per_share, quantity, entry_type,
               base_stage, ai_confidence, ai_recommendation, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            RETURNING id
            """,
            run_id, t.symbol, t.quant_rank, t.ai_rank, t.signal_date, t.entry_trigger_price,
            t.structural_sl, t.target_price, t.risk_per_share, t.quantity, t.entry_type,
            t.base_stage, t.ai_confidence, t.ai_recommendation, t.status,
        )
        t.db_id = row["id"]
        return

    await pool.execute(
        """
        UPDATE backtest_trades SET
          status=$2, entry_fill_date=$3, entry_fill_price=$4, half_booked=$5, trail_sl=$6,
          exit_date=$7, exit_price=$8, exit_reason=$9, realized_pnl=$10,
          r_multiple=$11, holding_days=$12, gross_pnl=$13
        WHERE id=$1
        """,
        t.db_id, t.status, t.entry_fill_date, t.entry_fill_price, t.half_booked, t.current_sl,
        t.exit_date, t.exit_price, t.exit_reason, t.realized_pnl,
        (round(t.realized_pnl / (t.risk_per_share * t.quantity), 3)
         if t.status == "CLOSED" and t.risk_per_share and t.quantity else None),
        ((t.exit_date - t.entry_fill_date).days
         if t.status == "CLOSED" and t.exit_date and t.entry_fill_date else None),
        t.gross_pnl,
    )
