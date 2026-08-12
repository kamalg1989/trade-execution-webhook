"""Backtest orchestrator — one run, day by day, chronological. See
/BACKTEST_ENGINE_SPEC.md (repo root) for the full design and §4 for the
exact per-day flow this implements.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from bisect import bisect_left
from datetime import date

import pandas as pd

from ai_analysis import config as ai_config
from ai_analysis.features.swings import last_swing_low
from ai_analysis.pipeline import analyze_symbols
from app.db import PgRepo

from . import funnel
from . import funnel_v2
from . import funnel_stage2
from .ai_repo import BacktestAiRepo
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
    }
    needs_ema_atr = any(exit_config.get(k) for k in
                         ("ema10_trail", "ema21_trail", "ema50_trail", "chandelier_trail"))
    needs_swing = any(exit_config.get(k) for k in ("swing_trail", "swing_break_exit"))

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
            rows = await pool.fetch(
                """
                SELECT o.symbol, o.open, o.high, o.low, o.close,
                       si.ema_10, si.ema_21, si.ema_50, si.atr_14
                FROM ohlcv_data o
                LEFT JOIN stock_indicators si
                  ON si.symbol = o.symbol AND si.indicator_date = o.time::date
                WHERE o.symbol = ANY($1) AND o.time::date = $2
                """,
                list(symbols_today), day,
            )
            bars_by_symbol = {
                r["symbol"]: {
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "ema10": float(r["ema_10"]) if needs_ema_atr and r["ema_10"] is not None else None,
                    "ema21": float(r["ema_21"]) if needs_ema_atr and r["ema_21"] is not None else None,
                    "ema50": float(r["ema_50"]) if needs_ema_atr and r["ema_50"] is not None else None,
                    "atr14": float(r["atr_14"]) if needs_ema_atr and r["atr_14"] is not None else None,
                }
                for r in rows
            }

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

        if stage2_active:
            candidates = await funnel_stage2.build_candidates(
                pool, day, capital, stage2_config_hash, gate_overrides, use_v2_ranking, sizing
            )
        elif use_funnel_v2:
            candidates = await funnel_v2.build_candidates(
                pool, day, capital, gate_overrides, use_v2_ranking, sizing
            )
        else:
            candidates = await funnel.build_candidates(pool, day, capital, sizing)
        # VCP contraction gate — drop candidates whose base isn't actually
        # tightening into the pivot. Applied after the funnel builds/ranks
        # candidates so it's uniform across all three funnel paths, and only
        # ever removes rows (relative ranking of survivors is preserved).
        # A symbol missing from the map (data gap) is kept, not dropped.
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
    await pool.execute(
        "UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() WHERE id=$1", run_id
    )


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
