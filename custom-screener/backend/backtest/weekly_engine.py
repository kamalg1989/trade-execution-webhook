"""Orchestrator for the Weekly Consolidation Breakout strategy backtest —
see weekly_breakout.py (signal generation) and weekly_simulator.py (trade
lifecycle). Runs on a WEEKLY cadence, driven off the precomputed
ohlcv_weekly table (sql/025) — completely separate from engine.py's daily
loop, dispatched from engine.run_backtest() based on backtest_runs.strategy.

Two-phase design for efficiency: naively re-scanning the whole ~2800-symbol
universe for a fresh breakout signal on every one of ~550 weeks (a ~1.5M-
call inner loop) would be needlessly slow, since the box-search + indicator
checks only ever need each symbol's OWN price history, never information
from other symbols or from "today". So instead:
  Phase A: for each symbol (in parallel, thread pool — CPU-bound pandas
           work), scan its ENTIRE weekly history ONCE for every valid
           breakout signal, independent of any other symbol.
  Phase B: run the (point-in-time) fundamentals filter only over that much
           smaller signal shortlist, not the whole universe every week.
  Phase C: the actual week-by-week trade-lifecycle loop (fills, MACD-trail
           exits, opening new positions) just looks up
           signals_by_week[week] — O(1), no re-scanning.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date

import pandas as pd

from . import weekly_breakout as wb
from .position_sizing import PositionSizer
from .weekly_simulator import (
    WeeklyTrade, _close_weekly, check_daily_stop_breach, step_exit_weekly, try_fill_weekly,
    update_macd_ratchet,
)

logger = logging.getLogger(__name__)

MIN_HISTORY_WEEKS = wb.BOX_MIN_WEEKS + wb.SMA_TREND_WEEKS + 5


async def run_weekly_backtest(run: dict, pool) -> None:
    run_id = run["id"]
    start_date, end_date = run["start_date"], run["end_date"]
    capital = float(run["capital"])
    risk_pct = float(run.get("weekly_risk_pct") or 1.0)
    resting_window_weeks = run.get("resting_window_days") or 2
    stacking_guard = bool(run.get("stacking_guard"))
    max_picks = int(run.get("max_picks_per_track") or 3)
    # Per-trade capital cap — honoured from run config since 2026-08-16; the
    # engine default stays 25 (size_position's original hardcoded default) so
    # runs <= #620, whose column is NULL, reproduce exactly. UI sends 10.
    max_cap_pct = (float(run["max_capital_per_trade_pct"])
                   if run.get("max_capital_per_trade_pct") is not None else 25.0)
    # sql/031 — compounding sizing: size off running equity (capital +
    # cumulative realized P&L of CLOSED trades at entry time) instead of
    # fixed starting capital. Realized-only, anti-martingale.
    # Support both old weekly_compounding_sizing and new compounding_enabled columns
    compounding = bool(run.get("compounding_enabled") or run.get("weekly_compounding_sizing"))

    # Create unified position sizer with compounding support
    sizer = PositionSizer(
        initial_capital=capital,
        risk_per_trade_pct=risk_pct,
        max_capital_per_trade_pct=max_cap_pct,
        compounding_enabled=compounding,
        compounding_mode=str(run.get("compounding_mode") or "profit_only"),
        compounding_min_capital=float(run.get("compounding_min_capital") or capital),
        # 2026-08-17 risk-audit guards (see position_sizing.py) — both None by
        # default, so every pre-audit run reproduces exactly.
        compounding_max_capital=(float(run["compounding_max_capital"])
                                  if run.get("compounding_max_capital") is not None else None),
        adv_position_cap_pct=(float(run["adv_position_cap_pct"])
                               if run.get("adv_position_cap_pct") is not None else None),
    )
    adv_cap_active = run.get("adv_position_cap_pct") is not None

    # 2026-08-17 (user request): two experimental knobs, orthogonal to each
    # other and to compounding/the exposure cap above.
    #   weekly_entry_cadence — 'weekly' (default, every week-end evaluates
    #     new signals) or 'biweekly' (only every OTHER week-end does; exits,
    #     fills, and the MACD ratchet still run every week regardless — only
    #     NEW-signal evaluation is gated, same principle as daily_exit_check
    #     above and signal_cadence in the daily BREAKOUT engine).
    #   weekly_rotation_enabled — when a new pick can't be sized because
    #     capital is fully committed (the portfolio-level cap above returns
    #     qty=0), sell the worst-performing currently-OPEN position (lowest
    #     current R-multiple as of this week's close) to free capital, then
    #     retry; repeat until the pick fits or there's nothing left to sell.
    #     Off (default) reproduces exactly the capped-but-no-rotation
    #     behavior — a starved week just skips the pick, as before.
    entry_cadence = str(run.get("weekly_entry_cadence") or "weekly")
    rotation_enabled = bool(run.get("weekly_rotation_enabled"))

    # 2026-08-17 quant-research finding — CANDIDATE RANKING.
    #   'box_weeks' (default) = the original production rule: prefer the
    #       longest consolidation base. Trade-level research over all 19,120
    #       cached signals showed this is uncorrelated with outcome (quintile
    #       spread +0.11R, non-monotonic) i.e. it ranks essentially at random.
    #   'composite' = cross-sectional z-score, computed WITHIN each signal
    #       week (never against the full-history distribution, which would
    #       leak future information), of three factors that each survived an
    #       out-of-sample check on 2022-2026 after being selected purely on
    #       2011-2021:
    #         low turnover_1m_avg_cr  (inverted)  -- illiquidity/size premium:
    #             small names can travel 10R+, mega-caps structurally cannot.
    #             IS quintile spread -1.47R, Q1 >=5R rate 14.2% vs Q5 4.4%.
    #         high pct_chg_3m                     -- momentum continuation.
    #             IS +0.58R, OOS +0.49R.
    #         high dist_sma_200_pct               -- breakout aligned with the
    #             primary trend rather than counter-trend. IS +0.57R, OOS +0.21R.
    #       Rejected for failing OOS: pct_chg_6m, pct_chg_1y, updown_vol_ratio,
    #       ifp_score, vol_dryup_ratio, vol_ratio_1d.
    # Measured effect, holding every other setting identical (so this isolates
    # the ranking alone): full 15yr Calmar 0.49 -> 0.97, OOS CAGR 7.4% -> 20.8%.
    rank_mode = str(run.get("weekly_rank_mode") or "box_weeks")

    # 2026-08-17 — EQUITY-CURVE CIRCUIT BREAKER. Scales NEW-position risk
    # budgets down (never up) while the realized equity curve is unhealthy.
    # Modes:
    #   'none'     (default) — inert, byte-identical to before this existed.
    #   'dd_peak'  — throttle while realized equity is >= X% below its peak.
    #   'equity_ma'— throttle while realized equity is below its own N-week
    #                moving average (a faster, less-lagging signal than a
    #                fixed drawdown threshold: it can release the throttle
    #                during a recovery that is still technically in drawdown).
    #   'both'     — throttle if EITHER condition holds (most conservative).
    # `cut` is the multiplier applied to the risk budget: 0.5 = half size,
    # 0.0 = pause new entries entirely while unhealthy.
    #
    # This is a pure path/geometry control, not a prediction: it makes no claim
    # that the next trade is worse after a losing streak. It only refuses to
    # compound at full size off an equity peak that no longer exists. Exits,
    # fills and the MACD ratchet are never throttled — an open position is
    # never abandoned because the account had a bad month.
    throttle_mode = str(run.get("weekly_equity_throttle_mode") or "none")
    throttle_dd_pct = float(run.get("weekly_equity_throttle_dd_pct") or 10.0)
    throttle_cut = (float(run["weekly_equity_throttle_cut"])
                    if run.get("weekly_equity_throttle_cut") is not None else 0.5)
    throttle_ma_weeks = int(run.get("weekly_equity_ma_weeks") or 4)
    # Realized-equity history, appended once per week-end unit. Tracked
    # independently of the sizer so the breaker behaves identically whether or
    # not compounding is enabled (with compounding off, the sizer's notion of
    # capital is static but the ACCOUNT still has a real equity curve, and that
    # curve is what the breaker must react to).
    equity_hist: list[float] = []
    realized_equity = capital
    equity_peak = capital
    if throttle_mode != "none":
        logger.info("weekly run %s: equity circuit breaker active (mode=%s dd=%.1f%% "
                    "cut=%.2f ma_weeks=%d)", run_id, throttle_mode, throttle_dd_pct,
                    throttle_cut, throttle_ma_weeks)

    # sql/019 regime state machine, ported to WEEKLY_BREAKOUT (2026-08-17,
    # CAGR-optimization Phase 1) — was already used by the daily BREAKOUT
    # engine (engine.py) but never wired in here despite reusing the exact
    # same run-config columns (regime_ma_days/regime_confirm_days/
    # regime_action) and the exact same market_snapshot-derived breadth
    # data, so the definition of OFFENSIVE/DEFENSIVE can never drift between
    # strategies. Precomputed once for the whole window into a
    # {date: 'OFFENSIVE'|'DEFENSIVE'} map, looked up per week-end — same
    # hysteresis logic as engine.py (starts OFFENSIVE, only flips state
    # after `regime_confirm_days` consecutive daily readings agree, so a
    # single noisy day can't whipsaw it).
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
            start_date, end_date, int(regime_ma_days),
        )
        state, streak, last = "OFFENSIVE", 0, None
        for rr in rrows:
            healthy = rr["ma"] is not None and rr["pct200"] >= rr["ma"]
            want = "OFFENSIVE" if healthy else "DEFENSIVE"
            streak = streak + 1 if want == last else 1
            last = want
            if want != state and streak >= int(regime_confirm_days):
                state = want
            regime_by_day[rr["snapshot_date"]] = state
        logger.info("weekly run %s: regime filter active (ma=%s confirm=%s action=%s)",
                    run_id, regime_ma_days, regime_confirm_days, regime_action)

    # Phase 2 (2026-08-17, CAGR-optimization) — optional breakeven-stop-move
    # and partial profit-booking, ported from the daily engine's Rule 2/3
    # into weekly_simulator.step_exit_weekly. Both default off (an
    # unconfigured run reproduces exactly the old structural+MACD-only
    # ladder). See weekly_simulator.py's step_exit_weekly docstring for the
    # exact ordering vs. the structural-stop and MACD-ratchet checks.
    cfg = {
        "slippage_pct": float(run["slippage_pct"]),
        "brokerage_per_order": float(run["brokerage_per_order"]),
        "stt_pct": float(run["stt_pct"]),
        "stamp_duty_pct": float(run["stamp_duty_pct"]),
        "exchange_charges_pct": float(run["exchange_charges_pct"]),
        "dp_charge": float(run["dp_charge"]),
        "weekly_breakeven_enabled": bool(run.get("weekly_breakeven_enabled")),
        "weekly_half_booking_enabled": bool(run.get("weekly_half_booking_enabled")),
        # Audit V4 — sell legs can carry heavier slippage than buys (stressed
        # stop exits). None -> _sell_fill falls back to slippage_pct.
        "exit_slippage_pct": (float(run["exit_slippage_pct"])
                               if run.get("exit_slippage_pct") is not None else None),
    }

    _t0 = time.time()
    symbols = await _eligible_symbols(pool)
    _t_symbols = time.time()
    frames = await _load_all_weekly_frames(pool, symbols, end_date)
    _t_frames = time.time()
    logger.info("weekly run %s: %d/%d symbols have enough history", run_id, len(frames), len(symbols))
    logger.info("PROFILE run %s: eligible_symbols=%.1fs load_all_weekly_frames=%.1fs (n=%d)",
                run_id, _t_symbols - _t0, _t_frames - _t_symbols, len(symbols))

    raw_signals = await _get_or_scan_signals(pool, run_id, frames, start_date, end_date)
    _t_phaseA = time.time()
    logger.info("weekly run %s: %d raw breakout signals before fundamentals filter", run_id, len(raw_signals))
    logger.info("PROFILE run %s: phaseA_scan_or_cache=%.1fs (n_symbols=%d, n_signals=%d)",
                run_id, _t_phaseA - _t_frames, len(frames), len(raw_signals))

    # Phase B — fundamentals filter, only over the shortlist. Batched
    # (2026-08-17) into one LATERAL-join query instead of one sequential
    # awaited round-trip per signal — profiling (run #653) showed 5843
    # sequential fundamentals_pass() calls cost 24.9s (4.3ms/signal, almost
    # entirely network/round-trip latency, not actual DB work) for what a
    # single batched query does in a couple seconds.
    passed_pairs = await _fundamentals_pass_batch(
        pool, [(sig.symbol, sig.signal_week_end) for sig in raw_signals]
    )
    signals_by_week: dict[date, list] = {}
    for sig in raw_signals:
        if (sig.symbol, sig.signal_week_end) in passed_pairs:
            signals_by_week.setdefault(sig.signal_week_end, []).append(sig)
    _t_phaseB = time.time()
    logger.info("PROFILE run %s: phaseB_fundamentals=%.1fs (n_signals=%d, %.1fms/signal)",
                run_id, _t_phaseB - _t_phaseA, len(raw_signals),
                (_t_phaseB - _t_phaseA) * 1000 / max(1, len(raw_signals)))

    # Phase B2 — optional daily-funnel quality gates (2026-08-15, user
    # request: "add configs from the other best setups"). Reuses the SAME
    # backtest_runs columns the daily BREAKOUT strategy uses for these —
    # gate_min_turnover_cr, gate_min_ifp_score, etc., stage2_base_stage_max_
    # allowed, max_contraction_ratio — so a threshold means the same thing
    # in both strategies with no second column set to keep in sync. Every
    # gate is independently opt-in (None = not applied, at the PER-FIELD
    # level — unlike BREAKOUT's bundled Stage-1 SQL gate) so combinations
    # can be tested freely from the UI. Evaluated using DAILY data as of the
    # breakout week's own Friday close (signal_week_end) — point-in-time
    # correct, no look-ahead — applied only to the shortlist, not the whole
    # universe, so it stays fast. An unconfigured run is byte-identical to
    # before this existed.
    signals_by_week = await apply_daily_quality_gates(pool, signals_by_week, run)
    _t_phaseB2 = time.time()
    logger.info("PROFILE run %s: phaseB2_quality_gates=%.1fs", run_id, _t_phaseB2 - _t_phaseB)

    # Phase B3 — candidate ranking scores. Precomputed once for the whole
    # shortlist in a single batched query (same LATERAL/unnest pattern as
    # _sql_gate_pass) rather than per-week inside the day loop, which would
    # reintroduce the sequential-round-trip problem fixed in the 2026-08-17
    # perf work. Empty dict when rank_mode='box_weeks' -> zero added cost and
    # byte-identical behaviour to before this existed.
    rank_scores: dict[tuple[str, date], float] = {}
    if rank_mode == "composite":
        rank_scores = await _composite_rank_scores(pool, signals_by_week)
        logger.info("weekly run %s: composite ranking active (%d scored candidates)",
                    run_id, len(rank_scores))

    # ADV map for the audit liquidity cap — one batched query, same pattern as
    # the ranking scores. Rupee ADV = turnover_1m_avg_cr * 1e7.
    adv_map: dict[tuple[str, date], float] = {}
    if adv_cap_active:
        adv_pairs = [(sig.symbol, we) for we, sigs in signals_by_week.items() for sig in sigs]
        if adv_pairs:
            adv_rows = await pool.fetch(
                """
                SELECT si.symbol, si.indicator_date, si.turnover_1m_avg_cr
                FROM stock_indicators si
                JOIN (SELECT unnest($1::text[]) AS symbol, unnest($2::date[]) AS d) k
                  ON si.symbol = k.symbol AND si.indicator_date = k.d
                WHERE si.turnover_1m_avg_cr IS NOT NULL
                """,
                [p[0] for p in adv_pairs], [p[1] for p in adv_pairs],
            )
            adv_map = {(r["symbol"], r["indicator_date"]): float(r["turnover_1m_avg_cr"]) * 1e7
                       for r in adv_rows}
        logger.info("weekly run %s: ADV position cap active (%.1f%% of ADV, %d symbols mapped)",
                    run_id, float(run["adv_position_cap_pct"]), len(adv_map))

    # Phase C — trade lifecycle. `daily_exit_check` (sql/030) walks every
    # TRADING DAY instead of every week: on a non-week-end day it only checks
    # OPEN trades' stop-breach against that day's Low (no entries, no MACD
    # ratchet update — that still needs a completed week); on a week-end day
    # it runs the full original weekly step (entries, resting-window expiry,
    # MACD ratchet update) MINUS the stop-breach check, which the daily pass
    # already covers. Off (default) reproduces the exact original week-only
    # loop, byte-for-byte, so existing runs are unaffected.
    all_week_ends = sorted({
        d for df in frames.values() for d in df.index if start_date <= d <= end_date
    })
    week_end_set = set(all_week_ends)
    daily_exit_check = bool(run.get("weekly_daily_exit_check"))

    if daily_exit_check:
        day_rows = await pool.fetch(
            "SELECT DISTINCT time::date AS d FROM ohlcv_data WHERE time::date BETWEEN $1 AND $2 ORDER BY d",
            start_date, end_date,
        )
        units = [r["d"] for r in day_rows]
    else:
        units = all_week_ends
    await pool.execute("UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2", len(units), run_id)

    active: list[WeeklyTrade] = []
    week_idx = -1  # index into all_week_ends — only advances on a week-end unit

    for unit_idx, day in enumerate(units):
        is_week_end = day in week_end_set

        if daily_exit_check:
            # Stop-breach check on OPEN trades against TODAY's daily bar —
            # runs on EVERY trading day, week-end included. This used to be
            # skipped on week-end days (only the ratchet update ran there),
            # which silently missed a breach that should have fired that
            # Friday and let the position run until some later day's check
            # finally caught it — a real bug (see chat 2026-08-15: run #601
            # showed impossible >100% drawdown and 7x-inflated avgR/total
            # P&L vs the equivalent weekly-only run #581, traced to exactly
            # this gap). Checking every day first, then doing the week-end-
            # only steps (fills, ratchet update) below, closes it.
            open_symbols = {t.symbol for t in active if t.status == "OPEN"}
            if open_symbols:
                daily_bars = await _load_daily_bars(pool, open_symbols, day)
                for t in active:
                    if t.status == "OPEN":
                        bar = daily_bars.get(t.symbol)
                        if bar is not None:
                            check_daily_stop_breach(t, day, bar, cfg)

        if daily_exit_check and not is_week_end:
            # Nothing else to do today — entries/ratchet updates/new signals
            # are still weekly-cadence.
            still_active = [t for t in active if t.status in ("PENDING", "OPEN")]
            for t in active:
                if t.status == "CLOSED":
                    # Record closed trade's realized P&L for compounding
                    sizer.record_trade_closed(realized_pnl=float(t.realized_pnl or 0))
            await asyncio.gather(*(_persist(pool, run_id, t) for t in active))
            active = still_active
            await pool.execute("UPDATE backtest_runs SET progress_day=$1 WHERE id=$2", unit_idx + 1, run_id)
            continue

        if is_week_end:
            week_idx += 1
        week_end = day

        still_active = []
        for t in active:
            df = frames.get(t.symbol)
            bar = _bar_at(df, week_end) if df is not None else None
            if bar is not None:
                if t.status == "PENDING":
                    since = week_idx - t.signal_week_idx
                    try_fill_weekly(t, week_end, bar, resting_window_weeks, since, cfg)
                if t.status == "OPEN":
                    if daily_exit_check:
                        update_macd_ratchet(t, week_end, bar)  # breach already checked daily above
                    else:
                        step_exit_weekly(t, week_end, bar, cfg)
            if t.status in ("PENDING", "OPEN"):
                still_active.append(t)
            elif t.status == "CLOSED":
                # Record closed trade's realized P&L for compounding
                sizer.record_trade_closed(realized_pnl=float(t.realized_pnl or 0))
        if active:
            await asyncio.gather(*(_persist(pool, run_id, t) for t in active))
        active = still_active

        active_symbols = {t.symbol for t in active}
        # Biweekly entry cadence — only every OTHER week-end (by index)
        # evaluates new signals; a skipped week still ran fills/exits/ratchet
        # above, it just contributes zero NEW picks. week_idx only advances
        # on week-end units (see top of loop), so this lines up 1:1 with
        # actual calendar weeks regardless of daily_exit_check.
        entry_allowed_this_week = (entry_cadence != "biweekly") or (week_idx % 2 == 0)
        todays = signals_by_week.get(week_end, []) if entry_allowed_this_week else []
        todays = [s for s in todays if not (stacking_guard and s.symbol in active_symbols)]
        if rank_mode == "composite":
            # Higher composite score first. A candidate with no score (no
            # stock_indicators row for that exact date) sorts last rather than
            # being dropped -- missing data must not silently block a signal,
            # same convention as every gate in this engine, but it also should
            # not outrank a candidate we can actually evaluate.
            todays.sort(key=lambda s: -rank_scores.get((s.symbol, week_end), -9e9))
        else:
            todays.sort(key=lambda s: -s.box_weeks)  # prefer bigger/longer bases first
        # Regime filter (Phase 1, 2026-08-17) — gates NEW entries only, same
        # principle as entry_cadence above; exits/fills/ratchet are unaffected
        # by regime, a position doesn't get orphaned just because the market
        # turned DEFENSIVE after it opened.
        if regime_by_day and todays:
            if regime_by_day.get(week_end) == "DEFENSIVE":
                if regime_action == "block":
                    todays = []
                elif regime_action == "half":
                    todays = todays[:max(1, max_picks // 2)]
        picks = todays[:max_picks]

        # Portfolio-level exposure cap (2026-08-17) — ₹ already committed to
        # OTHER currently-OPEN positions, so this week's new entries are sized
        # against what's actually still available rather than the full
        # running capital in isolation. WEEKLY_BREAKOUT positions can stay
        # open for months, so max_picks alone (which only throttles NEW
        # entries per week) let total concurrent exposure accumulate to
        # several multiples of stated capital on a long-lived book — see
        # 2026-08-17 finding (run #642: 28 concurrent positions worth ~7x
        # the ₹4L starting capital). PENDING (not yet filled) trades from
        # PRIOR weeks aren't counted — no capital is actually deployed until
        # fill — but each pick accepted THIS week is reserved immediately
        # (at its trigger price) so later picks in the same week's loop see
        # the updated commitment, unlike the daily-engine version which only
        # snapshots once per day.
        # Uses qty_remaining (not the original quantity) so a half-booked
        # position correctly frees up its sold-off half's capital for new
        # picks — quantity itself never changes, only qty_remaining does.
        committed_capital = sum(
            (t.entry_fill_price or 0.0) * t.qty_remaining
            for t in active if t.status == "OPEN"
        )

        # Circuit-breaker state for THIS week. realized_equity is derived from
        # the sizer's cumulative closed-trade P&L rather than tracked in a
        # second accumulator, so the two can never drift apart.
        size_scale = 1.0
        if throttle_mode != "none":
            realized_equity = capital + sizer.total_realized_pnl
            equity_peak = max(equity_peak, realized_equity)
            equity_hist.append(realized_equity)
            unhealthy_dd = (equity_peak > 0 and
                            (equity_peak - realized_equity) / equity_peak * 100 >= throttle_dd_pct)
            # MA needs a full window before it means anything; until then the
            # breaker stays open rather than firing on a half-formed average.
            unhealthy_ma = False
            if len(equity_hist) >= throttle_ma_weeks:
                ma = sum(equity_hist[-throttle_ma_weeks:]) / throttle_ma_weeks
                unhealthy_ma = realized_equity < ma
            if throttle_mode == "dd_peak":
                unhealthy = unhealthy_dd
            elif throttle_mode == "equity_ma":
                unhealthy = unhealthy_ma
            else:  # 'both'
                unhealthy = unhealthy_dd or unhealthy_ma
            if unhealthy:
                size_scale = throttle_cut

        for sig in picks:
            risk_per_share_sig = sig.entry_trigger - sig.initial_stop
            if risk_per_share_sig <= 0:
                continue  # invalid signal — rotation can't fix this, don't bother evicting

            # Use sizer for position sizing to support compounding + the
            # portfolio-level exposure cap.
            qty = sizer.size_position(entry_price=sig.entry_trigger,
                                     stop_price=sig.initial_stop,
                                     committed_capital=committed_capital,
                                     size_scale=size_scale,
                                     adv_value=adv_map.get((sig.symbol, week_end)))

            # Capital-rotation fallback (2026-08-17, user request): capital's
            # fully committed — sell the worst-performing OPEN position(s) to
            # make room, then retry. "Worst-performing" = lowest current
            # R-multiple as of THIS week's close ((close - entry_fill) /
            # risk_per_share), evaluated purely on its own merits — this is
            # NOT "only evict if the new signal ranks better than the
            # holding," just a capital-recycling rule. Keeps evicting the
            # next-worst position until the pick fits or there's nothing
            # left to sell.
            if qty <= 0 and rotation_enabled:
                while qty <= 0:
                    evictable = []
                    for t in active:
                        if t.status != "OPEN":
                            continue
                        bar = _bar_at(frames.get(t.symbol), week_end)
                        if bar is None or not t.entry_fill_price or not t.risk_per_share:
                            continue
                        r_mult = (bar["close"] - t.entry_fill_price) / t.risk_per_share
                        evictable.append((r_mult, t, bar["close"]))
                    if not evictable:
                        break  # nothing left to sell — genuinely out of room
                    evictable.sort(key=lambda x: x[0])
                    _, worst, worst_close = evictable[0]
                    # qty_remaining must be captured BEFORE _close_weekly, which
                    # zeroes it as part of closing the position.
                    worst_qty_remaining = worst.qty_remaining
                    _close_weekly(worst, week_end, worst_close, "ROTATED_OUT", cfg)
                    sizer.record_trade_closed(realized_pnl=float(worst.realized_pnl or 0))
                    await _persist(pool, run_id, worst)
                    active.remove(worst)
                    committed_capital -= (worst.entry_fill_price or 0.0) * worst_qty_remaining
                    qty = sizer.size_position(entry_price=sig.entry_trigger,
                                             stop_price=sig.initial_stop,
                                             committed_capital=committed_capital,
                                             size_scale=size_scale,
                                             adv_value=adv_map.get((sig.symbol, week_end)))

            if qty <= 0:
                continue
            trade = WeeklyTrade(
                symbol=sig.symbol, signal_week_end=sig.signal_week_end,
                entry_trigger_price=sig.entry_trigger, structural_sl=sig.initial_stop,
                risk_per_share=round(sig.entry_trigger - sig.initial_stop, 2), quantity=qty,
                box_weeks=sig.box_weeks, box_depth_pct=sig.box_depth_pct,
            )
            trade.signal_week_idx = week_idx
            await _persist(pool, run_id, trade)
            active.append(trade)
            # Reserve this pick's notional immediately so subsequent picks in
            # THIS SAME week's loop see the updated commitment (the trade is
            # still PENDING at this point — not yet filled — but reserving
            # at the trigger price is the conservative, safe direction: it
            # slightly under-sizes rather than over-commits if the position
            # never actually fills within its resting window).
            committed_capital += qty * sig.entry_trigger

        await pool.execute("UPDATE backtest_runs SET progress_day=$1 WHERE id=$2", unit_idx + 1, run_id)

    _t_phaseC = time.time()
    logger.info("PROFILE run %s: phaseC_day_loop=%.1fs (n_units=%d, %.1fms/unit)",
                run_id, _t_phaseC - _t_phaseB2, len(units),
                (_t_phaseC - _t_phaseB2) * 1000 / max(1, len(units)))
    logger.info("PROFILE run %s: TOTAL=%.1fs", run_id, _t_phaseC - _t0)

    # Path stats (MtM CAGR/maxDD/w12m/Martin/underwater) onto the run row —
    # the run-history table reads these columns; without this every weekly
    # run showed em-dashes forever (2026-08-17 UI bug root cause).
    from .path_stats import compute_and_store_mtm_stats
    await compute_and_store_mtm_stats(pool, run_id)

    await pool.execute(
        "UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() WHERE id=$1", run_id
    )


async def _get_or_scan_signals(pool, run_id: int, frames: dict[str, pd.DataFrame],
                                start_date: date, end_date: date) -> list:
    """Phase A, cached (2026-08-17 perf work — see run #653 profiling: this
    phase alone cost 138.7s of a 267.3s total run). Signal generation
    (_scan_symbol_signals / wb.scan_breakout) is a PURE function of (symbol,
    data through some week) — it takes no run-config input at all (no
    compounding/sizing/rotation/cadence/gate knob touches it), and a signal
    for week W is fully determined by data up to week W since the box search
    only ever looks BACKWARD from the breakout candle (never forward) — so
    once a symbol has been scanned through some week, that result can never
    change and is safe to cache forever. This matters a lot in practice:
    every run in a parameter sweep (compounding on/off, rotation on/off,
    gate thresholds, etc.) shares the exact same signal generation, so this
    phase was being repeated byte-for-byte, run after run, for no reason.

    Per symbol: if backtest_weekly_scan_progress already covers this
    symbol's frame through its latest available week, skip scanning
    entirely — signals come straight from backtest_weekly_signals_cache.
    Otherwise (new data since the last scan, or never scanned), do a full
    rescan of that symbol's whole history (same cost as before — cache
    misses aren't free, they're just rare across a sweep) and upsert.
    Returns signals restricted to [start_date, end_date], matching the
    original _scan_symbol_signals(..., start_date, end_date) contract."""
    progress_rows = await pool.fetch("SELECT symbol, scanned_through FROM backtest_weekly_scan_progress")
    scanned_through_by_symbol = {r["symbol"]: r["scanned_through"] for r in progress_rows}

    frame_latest_by_symbol: dict[str, date] = {}
    to_rescan: list[str] = []
    for sym, df in frames.items():
        last_ts = df.index[-1]
        frame_latest_by_symbol[sym] = last_ts.date() if hasattr(last_ts, "date") else last_ts
        cached_through = scanned_through_by_symbol.get(sym)
        if cached_through is None or cached_through < frame_latest_by_symbol[sym]:
            to_rescan.append(sym)

    if to_rescan:
        sem = asyncio.Semaphore(4)

        async def _scan_one(sym: str):
            async with sem:
                # Full rescan from an arbitrarily early start_date -- old,
                # already-cached weeks get recomputed too (simpler/safer than
                # trying to splice an incremental scan at some boundary
                # index) but ON CONFLICT DO NOTHING below makes re-inserting
                # them a no-op. Only the CPU cost is repeated, not correctness.
                return await asyncio.to_thread(
                    _scan_symbol_signals, frames[sym], sym, date(2000, 1, 1), frame_latest_by_symbol[sym]
                )

        rescanned = await asyncio.gather(*(_scan_one(s) for s in to_rescan))
        upsert_rows = []
        for sigs in rescanned:
            for sig in sigs:
                upsert_rows.append((
                    sig.symbol, sig.signal_week_end, sig.box_start_idx, sig.box_weeks,
                    sig.box_top, sig.box_bottom, sig.box_depth_pct,
                    sig.breakout_close, sig.breakout_high, sig.breakout_low,
                    sig.entry_trigger, sig.initial_stop, sig.risk_pct,
                ))
        if upsert_rows:
            await pool.executemany(
                """
                INSERT INTO backtest_weekly_signals_cache
                  (symbol, signal_week_end, box_start_idx, box_weeks, box_top, box_bottom,
                   box_depth_pct, breakout_close, breakout_high, breakout_low,
                   entry_trigger, initial_stop, risk_pct)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (symbol, signal_week_end) DO NOTHING
                """,
                upsert_rows,
            )
        await pool.executemany(
            """
            INSERT INTO backtest_weekly_scan_progress (symbol, scanned_through)
            VALUES ($1, $2)
            ON CONFLICT (symbol) DO UPDATE SET scanned_through = EXCLUDED.scanned_through
            """,
            [(s, frame_latest_by_symbol[s]) for s in to_rescan],
        )
        logger.info("weekly_scan_cache: rescanned %d/%d symbols (%d already cached through latest data)",
                    len(to_rescan), len(frames), len(frames) - len(to_rescan))

    cache_rows = await pool.fetch(
        """
        SELECT symbol, signal_week_end, box_start_idx, box_weeks, box_top, box_bottom,
               box_depth_pct, breakout_close, breakout_high, breakout_low,
               entry_trigger, initial_stop, risk_pct
        FROM backtest_weekly_signals_cache
        WHERE symbol = ANY($1) AND signal_week_end BETWEEN $2 AND $3
        ORDER BY symbol, signal_week_end
        """,
        list(frames.keys()), start_date, end_date,
    )
    return [
        wb.BoxBreakoutSignal(
            symbol=r["symbol"], signal_week_end=r["signal_week_end"],
            box_start_idx=r["box_start_idx"], box_weeks=r["box_weeks"],
            box_top=float(r["box_top"]), box_bottom=float(r["box_bottom"]),
            box_depth_pct=float(r["box_depth_pct"]), breakout_close=float(r["breakout_close"]),
            breakout_high=float(r["breakout_high"]), breakout_low=float(r["breakout_low"]),
            entry_trigger=float(r["entry_trigger"]), initial_stop=float(r["initial_stop"]),
            risk_pct=float(r["risk_pct"]),
        )
        for r in cache_rows
    ]


async def _fundamentals_pass_batch(pool, pairs: list[tuple[str, date]]) -> set[tuple[str, date]]:
    """Batched version of wb.fundamentals_pass() — same LATERAL-join pattern
    as _sql_gate_pass() below. One query for the whole shortlist instead of
    one sequential awaited round-trip per (symbol, as_of) pair (profiling,
    run #653: 5843 sequential calls cost 24.9s, ~4.3ms/call, almost all
    round-trip latency rather than actual query work). Same pass rule as the
    original: fewer than 2 qualifying reports before `as_of` -> pass by
    default (doesn't block a young/sparse-history stock); otherwise latest
    revenue AND net_profit must both be >= the prior report's."""
    if not pairs:
        return set()
    rows = await pool.fetch(
        """
        SELECT k.symbol, k.d, f.revenue, f.net_profit, f.period_to
        FROM (SELECT unnest($1::text[]) AS symbol, unnest($2::date[]) AS d) k
        JOIN LATERAL (
            SELECT revenue, net_profit, period_to
            FROM earnings_fundamentals ef
            WHERE ef.symbol = k.symbol AND ef.broadcast_date < k.d
              AND ef.revenue IS NOT NULL AND ef.net_profit IS NOT NULL
            ORDER BY ef.period_to DESC
            LIMIT 2
        ) f ON true
        """,
        [p[0] for p in pairs], [p[1] for p in pairs],
    )
    by_key: dict[tuple[str, date], list] = {}
    for r in rows:
        by_key.setdefault((r["symbol"], r["d"]), []).append(r)

    passed = set()
    for sym, d in pairs:
        reports = by_key.get((sym, d), [])
        if len(reports) < 2:
            passed.add((sym, d))
            continue
        latest, prior = reports[0], reports[1]
        if float(latest["revenue"]) >= float(prior["revenue"]) and float(latest["net_profit"]) >= float(prior["net_profit"]):
            passed.add((sym, d))
    return passed


async def _load_daily_bars(pool, symbols: set[str], day: date) -> dict[str, dict]:
    rows = await pool.fetch(
        "SELECT symbol, open, high, low, close FROM ohlcv_data WHERE symbol = ANY($1) AND time::date = $2",
        list(symbols), day,
    )
    return {r["symbol"]: {"open": float(r["open"]), "high": float(r["high"]),
                           "low": float(r["low"]), "close": float(r["close"])} for r in rows}


async def raw_breakout_week_ends(pool, start_date: date, end_date: date) -> dict[str, list[date]]:
    """Weekly consolidation-box breakout weeks per symbol — signal generation
    ONLY (no fundamentals filter; a caller using this as a daily-engine entry
    gate already has its own liquidity/quality gates on top, see
    engine.py's require_weekly_box_breakout). Reuses this module's own Phase
    A exactly (see run_weekly_backtest) so the definition of "breakout" can
    never drift from the WEEKLY_BREAKOUT strategy it's borrowed from.
    Returns {symbol: sorted [week_end, ...]} — symbols with zero signals are
    simply absent, not an empty list."""
    symbols = await _eligible_symbols(pool)
    frames = await _load_all_weekly_frames(pool, symbols, end_date)
    sem = asyncio.Semaphore(4)

    async def _scan_one(sym: str):
        async with sem:
            return await asyncio.to_thread(_scan_symbol_signals, frames[sym], sym, start_date, end_date)

    scanned = await asyncio.gather(*(_scan_one(s) for s in frames))
    out: dict[str, list[date]] = {}
    for sigs in scanned:
        for sig in sigs:
            out.setdefault(sig.symbol, []).append(sig.signal_week_end)
    for sym in out:
        out[sym].sort()
    return out


async def apply_daily_quality_gates(pool, signals_by_week: dict[date, list], run: dict) -> dict[date, list]:
    """Phase B2 — see run_weekly_backtest's call site docstring. Two families:
      1. SQL numeric gates, straight off stock_indicators (cheap, one batched
         query for the whole shortlist).
      2. Base-stage classification + VCP contraction ratio, which need a
         daily OHLCV frame per symbol — reuses screen_gpt.classify_base_stage
         and funnel.contraction_ratios directly (the SAME functions the daily
         BREAKOUT strategy calls) so the definition can never drift, grouped
         by distinct week_end since each needs "as of that Friday" data.
    Every gate is independently opt-in; an unset threshold is simply not
    checked. Returns a NEW dict — weeks that end up with zero signals are
    dropped, same convention as signals_by_week already has."""
    sql_thresholds = {
        k: (float(run[f"gate_{k}"]) if run.get(f"gate_{k}") is not None else None)
        for k in ("min_turnover_cr", "max_base_range_pct", "min_vol_mult",
                  "min_prior_upmove_pct", "max_giveback_pct", "max_vol_dryup_ratio",
                  "max_dist_from_high_pct", "min_ifp_score")
    }
    base_stage_max = (int(run["stage2_base_stage_max_allowed"])
                       if run.get("stage2_base_stage_max_allowed") is not None else None)
    max_contraction = (float(run["max_contraction_ratio"])
                        if run.get("max_contraction_ratio") is not None else None)

    if not any(v is not None for v in sql_thresholds.values()) and base_stage_max is None and max_contraction is None:
        return signals_by_week  # nothing configured -- no-op, byte-identical to before this existed

    if any(v is not None for v in sql_thresholds.values()):
        pairs = [(sig.symbol, we) for we, sigs in signals_by_week.items() for sig in sigs]
        passed = await _sql_gate_pass(pool, pairs, sql_thresholds)
        signals_by_week = {
            we: kept for we, sigs in signals_by_week.items()
            if (kept := [s for s in sigs if (s.symbol, we) in passed])
        }

    if base_stage_max is not None or max_contraction is not None:
        signals_by_week = await _base_stage_vcp_pass(pool, signals_by_week, base_stage_max, max_contraction)

    return signals_by_week


COMPOSITE_FACTORS = (
    # (stock_indicators column, invert, weight)
    ("turnover_1m_avg_cr", True, 1.0),   # low turnover preferred
    ("pct_chg_3m", False, 1.0),          # high 3-month momentum preferred
    ("dist_sma_200_pct", False, 1.0),    # further above the 200SMA preferred
)


async def _composite_rank_scores(pool, signals_by_week: dict[date, list]
                                  ) -> dict[tuple[str, date], float]:
    """Cross-sectional composite score per (symbol, signal_week_end).

    Each factor is z-scored WITHIN its own signal week, across only the
    candidates competing that same week. This matters for correctness, not
    just tidiness: z-scoring against the full-history distribution would let a
    2011 candidate be scored using 2026 information, and would also make the
    score drift with the market's overall level rather than measuring relative
    attractiveness, which is the only thing a ranking needs to do.

    A factor is skipped for a week if fewer than 3 candidates have a value for
    it (std is meaningless) or if its within-week std is 0. A candidate missing
    a factor contributes 0 for that factor (treated as average), so partial
    data degrades the score gracefully instead of discarding the candidate.
    """
    pairs = [(sig.symbol, we) for we, sigs in signals_by_week.items() for sig in sigs]
    if not pairs:
        return {}
    cols = ", ".join(f"si.{c}" for c, _, _ in COMPOSITE_FACTORS)
    rows = await pool.fetch(
        f"""
        SELECT si.symbol, si.indicator_date, {cols}
        FROM stock_indicators si
        JOIN (SELECT unnest($1::text[]) AS symbol, unnest($2::date[]) AS d) k
          ON si.symbol = k.symbol AND si.indicator_date = k.d
        """,
        [p[0] for p in pairs], [p[1] for p in pairs],
    )
    by_week: dict[date, list] = {}
    for r in rows:
        by_week.setdefault(r["indicator_date"], []).append(r)

    scores: dict[tuple[str, date], float] = {}
    for week_end, wrows in by_week.items():
        for col, invert, weight in COMPOSITE_FACTORS:
            vals = [(r["symbol"], float(r[col])) for r in wrows if r[col] is not None]
            if len(vals) < 3:
                continue
            xs = [v for _, v in vals]
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / len(xs)
            if var <= 0:
                continue
            std = var ** 0.5
            for sym, v in vals:
                z = (v - mean) / std
                if invert:
                    z = -z
                key = (sym, week_end)
                scores[key] = scores.get(key, 0.0) + weight * z
    return scores


async def _sql_gate_pass(pool, pairs: list[tuple[str, date]], t: dict) -> set[tuple[str, date]]:
    """(symbol, week_end) pairs surviving every SET threshold in `t`. Same
    operators as funnel.py's GATE_SQL (turnover >=, base_range <, vol_mult
    >, prior_upmove >=, giveback <=, vol_dryup <=, dist_from_high >= -N,
    ifp_score >=). A pair with no stock_indicators row for that exact date
    passes everything by default — missing data never silently blocks,
    same convention used everywhere else in this engine."""
    if not pairs:
        return set()
    rows = await pool.fetch(
        """
        SELECT si.symbol, si.indicator_date, si.turnover_1m_avg_cr, si.base_range_20d_pct,
               si.vol_ratio_1d, si.prior_upmove_pct, si.giveback_pct, si.vol_dryup_ratio,
               si.dist_20d_high_pct, si.ifp_score
        FROM stock_indicators si
        JOIN (SELECT unnest($1::text[]) AS symbol, unnest($2::date[]) AS d) k
          ON si.symbol = k.symbol AND si.indicator_date = k.d
        """,
        [p[0] for p in pairs], [p[1] for p in pairs],
    )
    by_key = {(r["symbol"], r["indicator_date"]): r for r in rows}
    passed = set()
    for sym, d in pairs:
        row = by_key.get((sym, d))
        if row is None:
            passed.add((sym, d))
            continue
        ok = True
        if t["min_turnover_cr"] is not None and (row["turnover_1m_avg_cr"] or 0) < t["min_turnover_cr"]:
            ok = False
        if t["max_base_range_pct"] is not None and (row["base_range_20d_pct"] if row["base_range_20d_pct"] is not None else 999) >= t["max_base_range_pct"]:
            ok = False
        if t["min_vol_mult"] is not None and (row["vol_ratio_1d"] or 0) <= t["min_vol_mult"]:
            ok = False
        if t["min_prior_upmove_pct"] is not None and (row["prior_upmove_pct"] or 0) < t["min_prior_upmove_pct"]:
            ok = False
        if t["max_giveback_pct"] is not None and (row["giveback_pct"] if row["giveback_pct"] is not None else 999) > t["max_giveback_pct"]:
            ok = False
        if t["max_vol_dryup_ratio"] is not None and (row["vol_dryup_ratio"] if row["vol_dryup_ratio"] is not None else 999) > t["max_vol_dryup_ratio"]:
            ok = False
        if t["max_dist_from_high_pct"] is not None and (row["dist_20d_high_pct"] if row["dist_20d_high_pct"] is not None else -999) < -t["max_dist_from_high_pct"]:
            ok = False
        if t["min_ifp_score"] is not None and (row["ifp_score"] or 0) < t["min_ifp_score"]:
            ok = False
        if ok:
            passed.add((sym, d))
    return passed


async def _base_stage_vcp_pass(pool, signals_by_week: dict[date, list],
                                base_stage_max: int | None, max_contraction: float | None) -> dict[date, list]:
    from . import funnel  # imported first -- inserts /root/trade-execution-webhook onto sys.path
    import screen_gpt
    screen_gpt.DEBUG = False  # classify_base_stage() otherwise print()s per-symbol
    # debug lines (base-count/bounce-check detail) straight into the run's log
    # file -- fine for a handful of calls, needless noise/IO across a whole
    # shortlist. engine.py's daily loop already does this same thing for its
    # own screen_gpt calls (_prepare_screen_gpt_for_backtest); weekly_engine
    # didn't need to until this gate started calling classify_base_stage().

    # Frame/ratio loads, one pair of queries per distinct week_end. Was a
    # sequential `for week_end in ...: await ...` loop (2026-08-17 finding,
    # run #660: this alone stalled for 20+ minutes over a 15-year window with
    # ~780 distinct week_ends x up to 2 sequential round-trips each — almost
    # entirely network/round-trip latency, not real query cost, exactly the
    # same shape of problem the Phase B fundamentals batching fixed). Now
    # issued concurrently via asyncio.gather with a semaphore matching the DB
    # pool's actual max_size (5, see app/db.py create_pool) — going wider
    # than the pool just queues at the connection-checkout level instead of
    # actually overlapping I/O wait, so this is the real ceiling.
    # classify_base_stage() itself (the CPU-bound part, below) was already
    # concurrent via its own thread-pool semaphore.
    frames_by_week: dict[date, dict] = {}
    ratios_by_week: dict[date, dict] = {}
    io_sem = asyncio.Semaphore(5)

    async def _load_week(week_end, symbols):
        async with io_sem:
            frame_result = (await funnel.load_ohlcv_frames_batch(pool, symbols, week_end)
                             if base_stage_max is not None else None)
            ratio_result = (await funnel.contraction_ratios(pool, symbols, week_end)
                             if max_contraction is not None else None)
            return week_end, frame_result, ratio_result

    week_symbol_pairs = [(we, [s.symbol for s in sigs]) for we, sigs in signals_by_week.items()]
    loaded = await asyncio.gather(*(_load_week(we, syms) for we, syms in week_symbol_pairs))
    for week_end, frame_result, ratio_result in loaded:
        if frame_result is not None:
            frames_by_week[week_end] = frame_result
        if ratio_result is not None:
            ratios_by_week[week_end] = ratio_result

    def _classify(df, symbol: str) -> int | None:
        if df is None or len(df) < 200:
            return None  # not enough history to classify -- conservative skip, mirrors funnel.py
        stage, _ = screen_gpt.classify_base_stage(df, symbol=symbol + ".NS")
        return stage

    sem = asyncio.Semaphore(4)

    async def _classify_one(week_end, sig):
        stage = None
        if base_stage_max is not None:
            async with sem:
                df = frames_by_week.get(week_end, {}).get(sig.symbol)
                stage = await asyncio.to_thread(_classify, df, sig.symbol)
        return week_end, sig, stage

    pairs = [(we, sig) for we, sigs in signals_by_week.items() for sig in sigs]
    results = await asyncio.gather(*(_classify_one(we, sig) for we, sig in pairs))

    out: dict[date, list] = {}
    for week_end, sig, stage in results:
        if base_stage_max is not None and (stage is None or stage > base_stage_max):
            continue
        if max_contraction is not None:
            ratio = ratios_by_week.get(week_end, {}).get(sig.symbol)
            if ratio is not None and ratio > max_contraction:
                continue  # missing ratio -> don't block, same convention as above
        out.setdefault(week_end, []).append(sig)
    return out


def _scan_symbol_signals(df: pd.DataFrame, symbol: str, start_date: date, end_date: date) -> list:
    """Only the weeks that pass wb._scan_prep()'s vectorized fast_pass gate
    are ever handed to wb.scan_breakout() — see the perf note in
    weekly_breakout.py's module docstring. `candidates` is np.nonzero() on a
    numpy bool array, so it's already in ascending index order; the
    end_date `break` below is therefore still a valid short-circuit."""
    if len(df) <= MIN_HISTORY_WEEKS:
        return []
    fast_pass, rollups, turnover_4wk = wb._scan_prep(df)
    candidates = fast_pass[MIN_HISTORY_WEEKS:].nonzero()[0] + MIN_HISTORY_WEEKS

    out = []
    for idx in candidates:
        week_end = df.index[idx]
        if week_end < start_date:
            continue
        if week_end > end_date:
            break
        sig = wb.scan_breakout(df, idx, symbol, rollups, turnover_4wk)
        if sig is not None:
            out.append(sig)
    return out


async def _eligible_symbols(pool) -> list[str]:
    rows = await pool.fetch(
        "SELECT symbol FROM symbols_meta WHERE is_active = true AND COALESCE(is_sme, false) = false "
        "AND (series IS NULL OR series = 'EQ')"
    )
    return [r["symbol"] for r in rows]


def _build_one_frame(rs: list) -> pd.DataFrame | None:
    """Per-symbol DataFrame construction + indicator computation — split out
    so it can run in a thread pool (see _load_all_weekly_frames below).
    len(rs) < MIN_HISTORY_WEEKS -> None (not enough history, same convention
    as the original inline loop this replaced)."""
    if len(rs) < MIN_HISTORY_WEEKS:
        return None
    df = pd.DataFrame([dict(r) for r in rs])
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume", "week_end"]].astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
    )
    df = df.set_index("week_end")
    return wb.compute_weekly_indicators(df)


async def _load_all_weekly_frames(pool, symbols: list[str], upto: date) -> dict[str, pd.DataFrame]:
    _t0 = time.time()
    rows = await pool.fetch(
        "SELECT symbol, week_end, open, high, low, close, volume FROM ohlcv_weekly "
        "WHERE symbol = ANY($1) AND week_end <= $2 ORDER BY symbol, week_end ASC",
        symbols, upto,
    )
    _t_fetch = time.time()
    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    # Parallelized (2026-08-17 perf work — see run #653 profiling: this loop
    # alone cost 69.6s, entirely sequential despite each symbol's
    # DataFrame-build + compute_weekly_indicators() being fully independent
    # work). Semaphore sized to the VPS's actual core count (2, confirmed via
    # nproc) rather than the 4 used elsewhere in this file for I/O-bound
    # work — this is CPU-bound pandas/numpy work, so going wider than core
    # count just adds thread-scheduling overhead without more real
    # parallelism (and pandas doesn't release the GIL for all of this, so
    # even 2 threads won't give a full 2x — still strictly better than 1).
    sem = asyncio.Semaphore(2)

    async def _build(sym: str, rs: list):
        async with sem:
            return sym, await asyncio.to_thread(_build_one_frame, rs)

    results = await asyncio.gather(*(_build(sym, rs) for sym, rs in by_symbol.items()))
    frames = {sym: df for sym, df in results if df is not None}
    _t_build = time.time()
    logger.info("PROFILE _load_all_weekly_frames: fetch=%.1fs (rows=%d) build_frames=%.1fs (symbols=%d)",
                _t_fetch - _t0, len(rows), _t_build - _t_fetch, len(frames))
    return frames


def _bar_at(df: pd.DataFrame, week_end: date) -> dict | None:
    try:
        pos = df.index.get_loc(week_end)
    except KeyError:
        return None
    if not isinstance(pos, int):
        return None
    row = df.iloc[pos]
    prev = df.iloc[pos - 1] if pos > 0 else None
    return {
        "open": float(row["Open"]), "high": float(row["High"]),
        "low": float(row["Low"]), "close": float(row["Close"]),
        "macd_line": float(row["macd_line"]) if pd.notna(row["macd_line"]) else None,
        "macd_signal": float(row["macd_signal"]) if pd.notna(row["macd_signal"]) else None,
        "macd_line_prev": float(prev["macd_line"]) if prev is not None and pd.notna(prev["macd_line"]) else None,
        "macd_signal_prev": float(prev["macd_signal"]) if prev is not None and pd.notna(prev["macd_signal"]) else None,
    }


async def _persist(pool, run_id: int, t: WeeklyTrade) -> None:
    if t.db_id is None:
        row = await pool.fetchrow(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, quant_rank, signal_date, entry_trigger_price,
               structural_sl, target_price, risk_per_share, quantity, entry_type, status)
            VALUES ($1,$2,1,$3,$4,$5,0.0,$6,$7,$8,$9)
            RETURNING id
            """,
            run_id, t.symbol, t.signal_week_end, t.entry_trigger_price,
            t.structural_sl, t.risk_per_share, t.quantity, "WEEKLY_BOX_BREAKOUT", t.status,
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
