"""Positional momentum engine that PERSISTS to backtest_runs/backtest_trades.

positional.py is the standalone research script (prints RESULT lines). This is
the same strategy wired into the run pipeline so it appears in the normal UI —
run list, trade log, equity curve, realized/unrealized P&L columns — without
duplicating any of those surfaces. See sql/020 for the field mapping.

Strategy recap (cross-sectional momentum / rotation):
    universe   liquid, close > SMA200
    rank       pos_momentum column, highest first
    rebalance  every pos_rebalance_days sessions (NOT daily — daily re-ranking
               is what creates the turnover this strategy exists to avoid)
    hold       top pos_top_n; sell only when a name falls outside pos_buffer_n
               or loses its SMA200. The buffer is hysteresis: without it a name
               oscillating around rank N churns at every rebalance.
    size       equal weight, capital / top_n per position
    stop       pos_sl_mode, checked EVERY SESSION (see sql/021). 'none'
               reproduces the original rebalance-only behaviour.

A stopped-out slot deliberately stays in CASH until the next rebalance rather
than being refilled immediately: instant refill would re-buy from the same
ranking that just stopped a name out, quietly averaging into whatever is
falling, and would flatter the backtest relative to what a trader would do.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

SLIPPAGE_PCT_DEFAULT = 0.10

# pos_sl_mode -> (indicator column, exit_reason). The four MA stops are one
# mechanism at four speeds; keeping them in a table rather than a branch chain
# means adding another line is a one-row change.
MA_STOPS = {
    "sma200": ("sma_200", "SL_SMA200"),
    "sma50": ("sma_50", "SL_SMA50"),
    "ema50": ("ema_50", "SL_EMA50"),
    "ema21": ("ema_21", "SL_EMA21"),
}
# 'atr_trail' (2026-08-18) = chandelier-style volatility trailing stop:
# exit when close <= peak_close * (1 - mult * atr_pct/100). Distinct from
# 'trail', which is a FIXED percentage and so demands the same give-back from
# a 2%-ATR largecap and a 6%-ATR midcap. Fills at T+1 open (see the async
# guard block) rather than the triggering close, matching the other guards.
VALID_SL_MODES = {"none", "fixed", "trail", "atr_trail", *MA_STOPS}


def _leg_cost(value: float, is_sell: bool, cfg: dict) -> float:
    """Same Dhan equity-delivery model as simulator._leg_costs."""
    c = value * cfg["stt_pct"] / 100 + value * cfg["exchange_charges_pct"] / 100
    c += cfg["dp_charge"] if is_sell else value * cfg["stamp_duty_pct"] / 100
    return c + cfg.get("brokerage_per_order", 0.0)


async def run_positional(run: dict, pool) -> None:
    run_id = run["id"]
    start_date, end_date = run["start_date"], run["end_date"]
    capital = float(run["capital"])
    momentum = run.get("pos_momentum") or "pct_chg_6m"
    if momentum not in ("pct_chg_3m", "pct_chg_6m", "pct_chg_1y", "composite_rs"):
        raise ValueError(f"unsupported pos_momentum {momentum!r}")
    # 2026-08-18 — composite multi-factor RS ranking (research: MULTIFACTOR_RS
    # _ENGINE.md). Opt-in ONLY: pos_momentum='composite_rs'. Every existing
    # value keeps the original single-column ORDER BY, so all prior POSITIONAL
    # runs reproduce byte-identically and nothing in BAU changes.
    #   score = z(12-1 mom) + z(6m) + z(3m) + z(6m/atr_pct), cross-sectionally
    #   z-scored WITHIN the rebalance day's own candidate set (never against a
    #   full-history distribution, which would leak future information).
    composite = momentum == "composite_rs"
    # Optional guards, all None/absent = inert (pre-existing behaviour).
    atr_max = (float(run["pos_atr_max_pct"])
               if run.get("pos_atr_max_pct") is not None else None)
    regime_ma = (int(run["pos_regime_ma_days"])
                 if run.get("pos_regime_ma_days") is not None else None)
    cash_annual = float(run.get("pos_cash_annual_pct") or 0.0)
    regime_entry_band = float(run.get("pos_regime_entry_band_pct") or 0.0)
    # screen_gpt signal ports (2026-08-18). All None/0 = inert, so every
    # pre-existing POSITIONAL run reproduces byte-identically.
    min_ifp = (float(run["pos_min_ifp_score"])
               if run.get("pos_min_ifp_score") is not None else None)
    min_close = (float(run["pos_min_close"])
                 if run.get("pos_min_close") is not None else None)
    base_score_w = float(run.get("pos_base_range_score_w") or 0.0)
    # 2026-08-20 — PER-FACTOR COMPOSITE WEIGHTS. Diagnostics on 44,495 eligible
    # rows showed the "5-factor" composite carries only ~2.96 independent
    # signals: mom6 and mom_vadj correlate at 0.969 (mom_vadj = mom6/atr, and
    # atr is already gated <=5%, so the division barely reorders), while the
    # factors' information coefficients span 3x:
    #     mom12_1 IC +.0606 | mom_vadj +.0419 | mom6 +.0384
    #     mom3    IC +.0213 | neg_base +.0199 (52.2% hit rate -- a coin flip)
    # All default to 1.0, so every prior run reproduces byte-identically.
    w_mom12 = float(run["pos_w_mom12"]) if run.get("pos_w_mom12") is not None else 1.0
    w_mom6 = float(run["pos_w_mom6"]) if run.get("pos_w_mom6") is not None else 1.0
    w_mom3 = float(run["pos_w_mom3"]) if run.get("pos_w_mom3") is not None else 1.0
    w_vadj = float(run["pos_w_vadj"]) if run.get("pos_w_vadj") is not None else 1.0
    # which momentum leg gets volatility-adjusted. 'mom6' = existing behaviour
    # (and the source of the 0.969 duplication); 'mom12' vol-adjusts the
    # STRONGEST factor instead of a duplicate of the middling one.
    vadj_base = str(run.get("pos_vadj_base") or "mom6")
    if vadj_base not in ("mom6", "mom12", "mom3"):
        raise ValueError(f"pos_vadj_base must be mom6|mom12|mom3, got {vadj_base!r}")
    # TREND INFORMATION RATIO — slope of the log-price regression divided by the
    # STANDARD ERROR OF ITS RESIDUALS. Measures how consistently a name advanced
    # rather than how far, so it is independent of raw momentum (magnitude),
    # mom_vadj (divides by total volatility) and base_range (width of a 20d box).
    # Lives in its own table so BAU is untouched. 0 = inert.
    trend_ir_w = float(run.get("pos_trend_ir_w") or 0.0)
    trend_ir_col = str(run.get("pos_trend_ir_col") or "126")
    if trend_ir_col not in ("126", "63"):
        raise ValueError(f"pos_trend_ir_col must be 126 or 63, got {trend_ir_col!r}")
    # H4 — INFORMATION DISCRETENESS (Da, Gurun & Warachka 2014), 2026-08-19.
    #   ID = sign(PRET) * (%neg - %pos) over the formation window.
    #   ID < 0 = continuous information (many small up-days)  -> momentum persists
    #   ID > 0 = discrete information (few large jumps)       -> momentum reverses
    # Applied as a SCORE PENALTY: score -= w * z(ID). Harness found the benefit
    # scales with CONCENTRATION (dCalmar +0.21 at N=10, +0.11 at N=25, +0.01 at
    # N=30) because one jump-driven name reversing is a far larger share of a
    # small book. 126d beats 252d. 0 = inert, so every prior POSITIONAL run
    # reproduces byte-identically.
    id_score_w = float(run.get("pos_id_score_w") or 0.0)
    id_lookback = int(run.get("pos_id_lookback") or 126)
    if id_lookback not in (126, 252):
        raise ValueError(f"pos_id_lookback must be 126 or 252, got {id_lookback}")
    # H3 — BARROSO & SANTA-CLARA (2015) VOLATILITY TARGETING, 2026-08-19.
    #   sigma_hat = stdev(last N DAILY returns of the held book) * sqrt(252)
    #   exposure  = min(max_lev, vol_target / sigma_hat)
    # The estimator is the whole point: an earlier test of this used SIX MONTHLY
    # observations (~30% standard error on the vol estimate) and scaled exposure
    # by noise, costing 0.15 Calmar purely through estimator error. Uses DAILY
    # returns of the actual book. None = inert.
    vol_target = (float(run["pos_vol_target_pct"])
                  if run.get("pos_vol_target_pct") is not None else None)
    vol_lb_days = int(run.get("pos_vol_lb_days") or 126)
    vol_max_lev = float(run.get("pos_vol_max_lev") or 1.0)
    # 2026-08-19 — pos_atr_max_pct has always driven BOTH the entry filter and
    # the daily exit guard, so their contributions were never separable. The
    # daily guard produces 56% of all exits, which is far too large a mechanism
    # to leave unattributed. Setting this false keeps the entry filter and drops
    # the daily exit. Default true = existing behaviour, so every prior run
    # reproduces byte-identically.
    atr_daily_exit = run.get("pos_atr_daily_exit")
    atr_daily_exit = True if atr_daily_exit is None else bool(atr_daily_exit)
    # 2026-08-19 — ASYMMETRIC ATR CEILING. Post-exit forensics on run #823 show
    # the ceiling is right on the median name (-4.18% excess over the next 126
    # sessions) but wrong on winners: positions sold while UP went on to beat the
    # universe by +4.81%, and the 31 sold while up >50% by +14.01%. A stock
    # accelerating upward expands its ATR exactly like one breaking down, and the
    # rule cannot tell them apart. This exempts a position from the daily ATR
    # exit while it is up more than N% on entry. None = no exemption = unchanged.
    atr_exempt_gain = (float(run["pos_atr_exempt_gain_pct"])
                       if run.get("pos_atr_exempt_gain_pct") is not None else None)
    # 2026-08-19 — MIDDLE-GROUND EXIT ARCHITECTURES. The binary daily ATR exit is
    # a pure risk/return dial (+5-10 CAGR, +3-8 MaxDD, Calmar flat). These four
    # try to keep the drawdown shield while giving back less of the right tail.
    # All default to the existing behaviour.
    #   A persistence: require N consecutive breach sessions before exiting
    #   B relative:    exit on ATR expanding vs its OWN norm AND price below
    #                  trend, so upside expansion is not punished like downside
    #   C trim:        sell a fraction on breach instead of the whole position
    #   D structure:   winners ignore ATR entirely, exit on an N-day low instead
    atr_persist_days = int(run.get("pos_atr_persist_days") or 1)
    atr_rel_mult = (float(run["pos_atr_rel_mult"])
                    if run.get("pos_atr_rel_mult") is not None else None)
    atr_trim_pct = float(run.get("pos_atr_trim_pct") or 100.0)
    struct_low_days = (int(run["pos_struct_low_days"])
                       if run.get("pos_struct_low_days") is not None else None)
    # any of these needs the daily close series even with sl_mode='none'
    need_series = (vol_target is not None or atr_exempt_gain is not None
                   or atr_rel_mult is not None or struct_low_days is not None)
    # 2026-08-18 drawdown-reduction program (all opt-in / inert by default):
    #   A sector cap · B inverse-vol sizing · C factor-breadth scaling ·
    #   E2 relative-strength exit · E3 cash buffer.  (D = N/buffer, params only.)
    max_sector_pct = (float(run["pos_max_sector_pct"])
                      if run.get("pos_max_sector_pct") is not None else None)
    size_mode = str(run.get("pos_size_mode") or "equal")
    breadth_scaling = bool(run.get("pos_breadth_scaling"))
    rs_exit_pct = (float(run["pos_rs_exit_pct"])
                   if run.get("pos_rs_exit_pct") is not None else None)
    cash_buffer_pct = float(run.get("pos_cash_buffer_pct") or 0.0)
    sector_of: dict = {}
    if max_sector_pct:
        sector_of = {r["symbol"]: r["sector"] for r in await pool.fetch(
            "SELECT symbol, sector FROM symbols_meta WHERE sector IS NOT NULL")}
        logger.info("run %s: sector cap %.0f%% (%d symbols classified)",
                    run_id, max_sector_pct, len(sector_of))
    # Opt-in compounding, reusing the existing shared columns so it means the
    # same thing here as in every other engine.
    compounding = bool(run.get("compounding_enabled"))
    comp_floor = float(run.get("compounding_min_capital") or capital)
    comp_ceiling = (float(run["compounding_max_capital"])
                    if run.get("compounding_max_capital") is not None else None)
    rebalance_days = int(run.get("pos_rebalance_days") or 21)
    top_n = int(run.get("pos_top_n") or 10)
    buffer_n = max(int(run.get("pos_buffer_n") or 20), top_n)
    min_turnover = float(run.get("pos_min_turnover_cr") or 5.0)
    # 2026-08-24 liquidity audit: adv_position_cap_pct is accepted by the API and
    # honoured by position_sizing.py / engine.py / weekly_engine.py, but THIS engine
    # never read it. Large-capital POSITIONAL runs (#1115 at Rs25L, #1116 at Rs1Cr)
    # therefore sized single positions up to ~22% of a day's turnover and reported
    # fills that could not be executed. None = inert, so every prior run reproduces
    # byte-identically; set it (e.g. 2.0) for any run above ~Rs10L capital.
    adv_cap_pct = (float(run["adv_position_cap_pct"])
                   if run.get("adv_position_cap_pct") is not None else None)
    sl_mode = (run.get("pos_sl_mode") or "none").lower()
    if sl_mode not in VALID_SL_MODES:
        raise ValueError(f"unsupported pos_sl_mode {sl_mode!r}")
    sl_pct = float(run.get("pos_sl_pct") or 0.0)
    if sl_mode in ("fixed", "trail") and sl_pct <= 0:
        raise ValueError(f"pos_sl_mode={sl_mode} requires pos_sl_pct > 0")
    sl_atr_mult = float(run.get("pos_sl_atr_mult") or 3.0)
    # 2026-08-20 Martin-port: the stop does not EXIST until the trade has been
    # up sl_arm_pct from entry (checked against PEAK, so once armed it stays
    # armed even if the gain is given back). 0/None = inert, prior runs
    # reproduce byte-identically.
    sl_arm = float(run.get("pos_sl_arm_pct") or 0.0)
    # 2026-08-20 Martin-port: equity-curve throttle. When the book's MtM equity
    # sits below its own running peak by dd_pct, NEW-entry gross exposure is
    # multiplied by cut (exits never throttled). None = inert.
    eq_throttle_dd = (float(run["pos_eq_throttle_dd_pct"])
                      if run.get("pos_eq_throttle_dd_pct") is not None else None)
    eq_throttle_cut = (float(run["pos_eq_throttle_cut"])
                       if run.get("pos_eq_throttle_cut") is not None else 0.5)
    # 'step' = original binary cut below the threshold. 'linear' = graduated:
    # multiplier ramps from 1.0 at zero drawdown down to `cut` at dd_pct or
    # deeper, and ramps back up automatically as equity recovers — sizing
    # follows the drawdown continuously in both directions.
    eq_throttle_mode = str(run.get("pos_eq_throttle_mode") or "step")
    # 2026-08-21 breadth-smile sizing (DD-attribution study, runs #1047 internals):
    # forward returns are STRONGEST at breadth extremes (washout <30%: +8.8%/63d,
    # 89% win; healthy >=60%: +7.5%) and weakest in the chop band. This trims
    # gross exposure ONLY inside [band_lo, band_hi) breadth — never at washout,
    # which is historically the single best forward-return state. None = inert.
    b200_mid_cut = (float(run["pos_b200_mid_cut"])
                    if run.get("pos_b200_mid_cut") is not None else None)
    b200_band_lo = float(run.get("pos_b200_band_lo") or 45.0)
    b200_band_hi = float(run.get("pos_b200_band_hi") or 60.0)
    # 2026-08-21 — GEORGE & HWANG (2004) 52-WEEK-HIGH factor. dist_52w_high_pct
    # is NEGATIVE below the high, so the raw value itself ranks "nearness to
    # the high" (closer to 0 = stronger). Weighted optional composite factor;
    # 0/None = inert, every prior run reproduces byte-identically.
    w_52wh = float(run.get("pos_w_52wh") or 0.0)
    # 2026-08-21 — EARNINGS ENTRY GATE: skip a BUY whose symbol has a results
    # broadcast scheduled within N calendar days after the fill date (the
    # gapped-through-stop failure mode a monthly book cannot exit fast enough
    # to dodge). Uses earnings_filings.broadcast_date. None = inert.
    earn_gate_days = (int(run["pos_earn_gate_days"])
                      if run.get("pos_earn_gate_days") is not None else None)

    cfg = {
        "slippage_pct": float(run["slippage_pct"]),
        "stt_pct": float(run["stt_pct"]),
        "stamp_duty_pct": float(run["stamp_duty_pct"]),
        "exchange_charges_pct": float(run["exchange_charges_pct"]),
        "dp_charge": float(run["dp_charge"]),
        "brokerage_per_order": float(run["brokerage_per_order"]),
    }
    slip = cfg["slippage_pct"]
    # exit_slippage_pct: same story as adv_position_cap_pct — supported by the other
    # engines, ignored here. Falls back to the symmetric slippage when unset.
    exit_slip = (float(run["exit_slippage_pct"])
                 if run.get("exit_slippage_pct") is not None else slip)

    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start_date, end_date)]
    await pool.execute("UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2",
                       len(days), run_id)
    if not days:
        await pool.execute("UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() "
                           "WHERE id=$1", run_id)
        return

    if composite:
        # Pull the factor inputs for the day; ranking happens in Python because
        # the z-scores are cross-sectional over this same candidate set. LIMIT
        # is applied AFTER scoring, so it can't truncate the ranking basis.
        # ATR ceiling is now enforced DAILY on held names (see the async guards
        # in the day loop). Applying it at selection time as well keeps the
        # engine from buying a name it would liquidate the next session.
        atr_clause = ("AND atr_pct <= " + str(atr_max)) if atr_max is not None else ""
        # TASK A (2026-08-18) — screen_gpt hard gates, validated in harness:
        #   ifp_score >= 0.40  institutional-sponsorship regime. NOTE this gate
        #     lives in the REBALANCE query, so it is re-evaluated every
        #     rebalance and a holding whose IFP decays below it falls out of
        #     `ranked` and is sold as RANK_DROP. That continuous re-check is
        #     where the edge is: harness measured Calmar 0.94 re-checked
        #     monthly vs 0.80 entry-only (identical to no gate at all).
        #   close >= 20  removes the crash-prone penny tail WITHOUT raising the
        #     turnover floor (raising liquidity was measured strictly harmful:
        #     Calmar 0.66/0.57/0.51/0.42 at TO 8/15/25/50 — the illiquidity
        #     premium is real and must be preserved).
        # base_range_20d_pct is SELECTed for the score (Task B) but deliberately
        # NOT gated — as a binary filter it collapsed Calmar to 0.47.
        ifp_clause = ("AND ifp_score >= " + str(min_ifp)) if min_ifp is not None else ""
        close_clause = ("AND close >= " + str(min_close)) if min_close is not None else ""
        # H4: information discreteness lives in its own table so the BAU daily
        # compute job (which writes stock_indicators) is untouched. LEFT JOIN so
        # a missing ID can never drop a candidate from the universe; missing
        # values are neutralised at the cross-sectional mean during scoring.
        id_select = (f", d.id_{id_lookback} AS id_val" if id_score_w else "")
        id_join = ("LEFT JOIN stock_information_discreteness d "
                   "  ON d.symbol = s.symbol AND d.indicator_date = s.indicator_date"
                   if id_score_w else "")
        tir_select = (f", t.trend_ir_{trend_ir_col} AS tir_val" if trend_ir_w else "")
        tir_join = ("LEFT JOIN stock_trend_ir t "
                    "  ON t.symbol = s.symbol AND t.indicator_date = s.indicator_date"
                    if trend_ir_w else "")
        rank_sql = f"""
            SELECT s.symbol, s.close, s.sma_200, s.atr_pct, s.base_range_20d_pct,
                   s.dist_52w_high_pct,
                   s.pct_chg_1m, s.pct_chg_3m, s.pct_chg_6m, s.pct_chg_1y{id_select}{tir_select}
            FROM stock_indicators s
            {id_join}
            {tir_join}
            WHERE s.indicator_date = $1 AND s.turnover_1m_avg_cr >= $2
              AND s.close > s.sma_200 AND s.pct_chg_1y IS NOT NULL
              AND s.pct_chg_6m IS NOT NULL AND s.pct_chg_3m IS NOT NULL
              AND s.atr_pct IS NOT NULL AND s.atr_pct > 0
              {atr_clause.replace('atr_pct', 's.atr_pct')}
              {ifp_clause.replace('ifp_score', 's.ifp_score')}
              {close_clause.replace('close', 's.close')}
        """
    else:
        rank_sql = f"""
            SELECT symbol, close, sma_200, {momentum} AS mom
            FROM stock_indicators
            WHERE indicator_date = $1 AND turnover_1m_avg_cr >= $2
              AND close > sma_200 AND {momentum} IS NOT NULL
            ORDER BY {momentum} DESC LIMIT $3
        """

    def _score_composite(rows: list, limit: int) -> list:
        """z(mom12_1) + z(mom6) + z(mom3) + z(mom6/atr) over `rows`, best first.
        Returns dicts shaped like the non-composite path (symbol/close/mom)."""
        import statistics as _st
        recs = []
        for r in rows:
            try:
                m1 = float(r["pct_chg_1m"] or 0) / 100
                m12 = (1 + float(r["pct_chg_1y"]) / 100) / (1 + m1) - 1
                m6 = float(r["pct_chg_6m"]) / 100
                m3 = float(r["pct_chg_3m"]) / 100
                atr = float(r["atr_pct"])
                # TASK B — 5th factor: base tightness, INVERTED so a tighter
                # (smaller) 20-day range produces a POSITIVE z-score. Weighted
                # by base_score_w (default 1.0, 0 disables). Missing value ->
                # neutral (the cross-sectional mean), so a name is never
                # rewarded or punished for absent data.
                br = r["base_range_20d_pct"]
                vb = {"mom6": m6, "mom12": m12, "mom3": m3}[vadj_base]
                f = [m12, m6, m3, vb / atr]
                if base_score_w:
                    f.append(-float(br) if br is not None else None)
                # H4 6th factor: NEGATED ID, so a CONTINUOUS-information name
                # (ID<0) scores positively. Negating the raw value before
                # z-scoring is identical to `score -= w*z(ID)` because z is
                # linear and the +/-3 clip is symmetric.
                if id_score_w:
                    idv = r["id_val"]
                    f.append(-float(idv) if idv is not None else None)
                if trend_ir_w:
                    tv = r["tir_val"]
                    f.append(float(tv) if tv is not None else None)
                if w_52wh:
                    dh = r["dist_52w_high_pct"]
                    f.append(float(dh) if dh is not None else None)
                recs.append({"symbol": r["symbol"], "close": float(r["close"]),
                             "f": tuple(f)})
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        if len(recs) < 5:
            return []
        # Explicit per-factor weights: the 4 momentum factors always carry 1.0,
        # then any enabled optional factors in append order. Keeping weights in
        # a list (rather than indexing by position) means adding a factor can
        # never silently reweight an existing one.
        weights = [w_mom12, w_mom6, w_mom3, w_vadj]
        if base_score_w:
            weights.append(base_score_w)
        if id_score_w:
            weights.append(id_score_w)
        if trend_ir_w:
            weights.append(trend_ir_w)
        if w_52wh:
            weights.append(w_52wh)
        n_factors = len(weights)
        # neutralise missing optional-factor values at the cross-sectional mean
        for i in range(4, n_factors):
            vals = [x["f"][i] for x in recs if x["f"][i] is not None]
            mu_i = _st.fmean(vals) if vals else 0.0
            for x in recs:
                if x["f"][i] is None:
                    x["f"] = x["f"][:i] + (mu_i,) + x["f"][i + 1:]
        scored = []
        stats_by_factor = []
        for i in range(n_factors):
            vals = [x["f"][i] for x in recs]
            mu = _st.fmean(vals)
            sd = _st.pstdev(vals) or 1.0
            stats_by_factor.append((mu, sd))
        for x in recs:
            s = 0.0
            for i, (mu, sd) in enumerate(stats_by_factor):
                z = max(-3.0, min(3.0, (x["f"][i] - mu) / sd))
                s += weights[i] * z
            scored.append({"symbol": x["symbol"], "close": x["close"], "mom": s})
        scored.sort(key=lambda z: -z["mom"])
        return scored[:limit]

    # Regime shield: index proxy > its own N-day MA, evaluated per session.
    # Absent -> always risk-on, i.e. exactly the pre-existing behaviour.
    regime_on: dict = {}
    if regime_ma:
        prox = await pool.fetch(
            "SELECT d, level FROM index_proxy_daily WHERE proxy='SYNTH_EQW' ORDER BY d")
        lv = [(r["d"], float(r["level"])) for r in prox]
        run_sum, window, state, switches = 0.0, [], True, 0
        # HYSTERESIS (2026-08-18): exit the instant the proxy loses its MA, but
        # require entry_band above the MA to re-enter. Asymmetric on purpose —
        # the harness sweep showed widening the EXIT side is strictly harmful
        # (you eat more of the crash) while widening the ENTRY side kills the
        # whipsaw round-trips that made the daily shield lose money in run #773
        # (1,314 REGIME_OFF exits for +Rs.0.44L net). Band 0 = plain threshold,
        # reproducing prior runs exactly.
        for d_, v in lv:
            window.append(v); run_sum += v
            if len(window) > regime_ma:
                run_sum -= window.pop(0)
            if len(window) < regime_ma:
                regime_on[d_] = state
                continue
            ma = run_sum / regime_ma
            if state and v < ma:
                state = False; switches += 1
            elif (not state) and v > ma * (1 + regime_entry_band / 100.0):
                state = True; switches += 1
            regime_on[d_] = state
        logger.info("run %s: regime state machine — entry_band=%.1f%%, %d switches",
                    run_id, regime_entry_band, switches)
        logger.info("run %s: regime shield ma=%d over %d proxy days",
                    run_id, regime_ma, len(regime_on))

    holdings: dict[str, dict] = {}

    # Per-symbol close+MA series, fetched ONCE when a name is first bought and
    # reused for every subsequent daily stop check. The naive alternative — one
    # query per day for the current holdings — is ~250 round trips per window
    # and dominated the runtime of the research sweep before it was cached.
    series: dict[str, dict] = {}
    ma_col = MA_STOPS[sl_mode][0] if sl_mode in MA_STOPS else None

    # Daily ATR series per held name, for the asynchronous ATR ceiling (2026-08-18).
    # Fetched once per symbol on purchase, same caching rationale as `series`.
    atr_series: dict[str, dict] = {}

    async def warm_atr(sym: str) -> None:
        # needed by the ATR ceiling AND the ATR trailing stop
        if sym in atr_series or (atr_max is None and sl_mode != "atr_trail"):
            return
        atr_series[sym] = {
            r["d"]: float(r["a"]) for r in await pool.fetch(
                "SELECT indicator_date AS d, atr_pct AS a FROM stock_indicators "
                "WHERE symbol=$1 AND indicator_date BETWEEN $2 AND $3 AND atr_pct IS NOT NULL",
                sym, start_date, end_date)}

    # Rolling daily returns of the held book, for H3 vol targeting. Appended
    # once per session; only ever READ at a rebalance from strictly past
    # sessions, so there is no look-ahead.
    book_daily_rets: list[float] = []
    eq_peak = [float(run["capital"])]   # equity-throttle running peak

    async def warm_series(sym: str) -> None:
        # several opt-in guards need the close series even with no stop configured
        if sym in series or (sl_mode == "none" and not need_series):
            return
        # Option B needs a trend reference. With sl_mode='none' ma_col is None,
        # which previously made the "below trend" test always False and silenced
        # the whole rule -- runs #891/#892 returned filter-only numbers exactly.
        # ema_21 stands in for SMA-20 (stock_indicators has no SMA-20 column).
        trend_col = ma_col or ("ema_21" if atr_rel_mult is not None else None)
        sel = f", si.{trend_col} AS ma" if trend_col else ", NULL::numeric AS ma"
        series[sym] = {
            r["d"]: (float(r["c"]), float(r["ma"]) if r["ma"] is not None else None)
            for r in await pool.fetch(
                f"SELECT o.time::date AS d, o.close AS c{sel} "
                "FROM ohlcv_data o LEFT JOIN stock_indicators si "
                "  ON si.symbol=o.symbol AND si.indicator_date=o.time::date "
                "WHERE o.symbol=$1 AND o.time::date BETWEEN $2 AND $3",
                sym, start_date, end_date)}

    # Running realized P&L, for opt-in compounding (see alloc below). Kept as a
    # one-element list so the nested close_out() can mutate it without nonlocal
    # gymnastics. Unused when compounding_enabled is false.
    realized_total = [0.0]
    # Accrued interest on idle cash, and the previous session's regime state
    # (for edge detection). Lists so nested scopes can mutate them.
    cash_credit = [0.0]
    prev_regime_on = [True]

    # tracks consecutive ATR-breach sessions per symbol (Option A persistence)
    atr_breach_run: dict[str, int] = {}

    async def _clone_trade_row(h: dict, qty: int) -> int:
        """Duplicate an open trade row with a smaller quantity, for partial
        exits. Returns the new row id so the sold slice can be closed normally."""
        return await pool.fetchval(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, signal_date, entry_trigger_price, structural_sl,
               entry_fill_date, entry_fill_price, quantity, status, entry_type,
               risk_per_share)
            SELECT run_id, symbol, signal_date, entry_trigger_price, structural_sl,
                   entry_fill_date, entry_fill_price, $2, 'OPEN', entry_type,
                   risk_per_share
            FROM backtest_trades WHERE id = $1
            RETURNING id
            """, h["db_id"], qty)

    async def close_out(sym: str, h: dict, when, exit_px: float, reason: str) -> None:
        """Single exit path for both stops and rotations, so a stopped trade is
        costed, R-scored and recorded exactly like any other."""
        net = round(exit_px * (1 - exit_slip / 100), 2)
        pnl = (net - h["entry"]) * h["qty"] - _leg_cost(net * h["qty"], True, cfg)
        # gross_pnl is the FRICTIONLESS result — raw price to raw price, no
        # slippage and no charges — which is what the summary subtracts net from
        # to report cost drag. Omitting it (as an earlier version did) leaves
        # gross at 0 and makes the UI show a nonsensical negative cost equal to
        # the whole realized P&L.
        gross = (exit_px - float(h["gross_entry"])) * h["qty"]
        # r_multiple is NUMERIC(8,3) -> |value| must stay under 100000. With
        # pos_sl_mode='none' there is no real risk-per-share, so this ratio can
        # explode and overflow the column (observed: run #868 crashed here, and
        # stored values already range -2377..+7320). R-multiples are not
        # meaningful for a strategy without a defined stop; clamp so a junk
        # diagnostic can never abort an otherwise valid backtest.
        rmul = None
        if h["risk"] and h["qty"]:
            try:
                rmul = max(-99999.999, min(99999.999,
                                           pnl / (h["risk"] * h["qty"])))
                rmul = round(rmul, 3)
            except (ZeroDivisionError, OverflowError):
                rmul = None
        await pool.execute(
            """
            UPDATE backtest_trades SET status='CLOSED', exit_date=$2, exit_price=$3,
              exit_reason=$8, realized_pnl=$4, r_multiple=$5, holding_days=$6,
              gross_pnl=$7
            WHERE id=$1
            """,
            h["db_id"], when, net, round(pnl, 2), rmul, (when - h["date"]).days,
            round(gross, 2), reason)
        realized_total[0] += pnl

    for i, day in enumerate(days):
        if i % 25 == 0:
            await pool.execute("UPDATE backtest_runs SET progress_day=$1 WHERE id=$2",
                               i + 1, run_id)

        # ---- DAILY stop check. Runs on EVERY session, including rebalance days
        #      and before the rebalance itself, so a name that has already
        #      violated its stop is never carried into the rotation logic.
        if sl_mode != "none" and holdings:
            stopped: list[tuple[str, float, str]] = []
            for sym, h in holdings.items():
                row = series.get(sym, {}).get(day)
                if row is None:
                    continue
                px, ma = row
                h["peak"] = max(h.get("peak", px), px)
                if sl_arm > 0 and h["peak"] < h["entry"] * (1 + sl_arm / 100):
                    continue        # stop not armed yet (Martin-port, 2026-08-20)
                if sl_mode == "fixed" and px <= h["entry"] * (1 - sl_pct / 100):
                    stopped.append((sym, px, "SL_FIXED"))
                elif sl_mode == "trail" and px <= h["peak"] * (1 - sl_pct / 100):
                    stopped.append((sym, px, "SL_TRAIL"))
                elif ma_col and ma and px < ma:
                    stopped.append((sym, px, MA_STOPS[sl_mode][1]))
            for sym, px, reason in stopped:
                await close_out(sym, holdings.pop(sym), day, px, reason)

        # ================= DAILY ASYNCHRONOUS RISK GUARDS (2026-08-18) =========
        # Selection/ranking stays on the 21-session clock; RISK CONTROL runs
        # every session. Rationale (runs #772 vs harness): a regime break on
        # day 2 of a cycle previously sat unhedged for up to 19 sessions, which
        # is where the 39.5%-vs-20% drawdown gap came from.
        # Both guards decide on day T's close and FILL AT DAY T+1's OPEN —
        # never at T's close, which would be an unexecutable same-bar fill.
        nxt = days[i + 1] if i + 1 < len(days) else None
        force_rebalance = False

        if regime_ma and nxt is not None:
            today_on = regime_on.get(day, prev_regime_on[0])
            if prev_regime_on[0] and not today_on:
                # RISK-OFF transition -> liquidate the whole book at T+1 open.
                if holdings:
                    px = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                        "SELECT symbol, open FROM ohlcv_data "
                        "WHERE symbol = ANY($1) AND time::date=$2",
                        list(holdings), nxt)}
                    for sym in list(holdings):
                        if sym in px and px[sym] > 0:
                            await close_out(sym, holdings.pop(sym), nxt, px[sym],
                                            "REGIME_OFF")
                logger.info("run %s: REGIME OFF %s -> liquidated at %s open",
                            run_id, day, nxt)
            elif today_on and not prev_regime_on[0]:
                # RISK-ON transition -> re-enter immediately, do not wait for
                # the 21-session timer (spec 3.A).
                force_rebalance = True
                logger.info("run %s: REGIME ON %s -> forced re-entry", run_id, day)
            prev_regime_on[0] = today_on
            risk_off_today = not today_on
        else:
            risk_off_today = False

        # ---- daily ATR ceiling on held names (spec 3.B)
        if atr_max is not None and atr_daily_exit and holdings and nxt is not None:
            def _gain_pct(s: str) -> float | None:
                row = series.get(s, {}).get(day)
                ent = holdings[s].get("entry")
                if row and ent and ent > 0:
                    return (row[0] / ent - 1) * 100
                return None

            def _lookback(store: dict, s: str, n: int) -> list:
                """Last n available values for symbol s up to and including today."""
                out = []
                for k in range(i, max(-1, i - n), -1):
                    v = store.get(s, {}).get(days[k])
                    if v is not None:
                        out.append(v[0] if isinstance(v, tuple) else v)
                return out

            # ---- OPTION B: relative ATR expansion instead of a static ceiling.
            # Exits only when ATR is expanding against its OWN 20-session norm
            # AND price is below trend -- so a name expanding while it rallies is
            # left alone, which a static ceiling cannot distinguish.
            # NOTE ema_21 stands in for SMA-20 (stock_indicators has no SMA-20).
            if atr_rel_mult is not None:
                hot = []
                for s in holdings:
                    a_now = atr_series.get(s, {}).get(day)
                    hist = _lookback(atr_series, s, 20)
                    row = series.get(s, {}).get(day)
                    if not a_now or len(hist) < 10 or row is None:
                        continue
                    norm = sum(hist) / len(hist)
                    below_trend = row[1] is not None and row[0] < row[1]
                    if norm > 0 and (a_now / norm) > atr_rel_mult and below_trend:
                        hot.append(s)
            else:
                hot = [s for s in holdings
                       if (atr_series.get(s, {}).get(day) or 0) > atr_max]

            # ---- OPTION A: multi-session persistence. A single-day volatility
            # spike on a parabolic winner is noise; a structural breakdown
            # persists. Requires N consecutive breach sessions.
            if atr_persist_days > 1:
                survivors = []
                for s in hot:
                    n = atr_breach_run.get(s, 0) + 1
                    atr_breach_run[s] = n
                    if n >= atr_persist_days:
                        survivors.append(s)
                for s in list(atr_breach_run):
                    if s not in hot:
                        atr_breach_run.pop(s, None)
                hot = survivors

            # ---- OPTION D: structure shield. A position in profit ignores the
            # volatility test entirely and is invalidated by PRICE STRUCTURE
            # instead -- a close below the N-session low.
            if struct_low_days is not None:
                keep_hot, struct_hits = [], []
                for s in list(holdings):
                    g = _gain_pct(s)
                    row = series.get(s, {}).get(day)
                    if g is not None and g > 0:
                        lows = _lookback(series, s, struct_low_days + 1)
                        if row and len(lows) > struct_low_days and \
                                row[0] < min(lows[1:]):
                            struct_hits.append(s)
                        continue           # winners are exempt from the ATR test
                    if s in hot:
                        keep_hot.append(s)
                hot = keep_hot + [s for s in struct_hits if s not in keep_hot]

            if atr_exempt_gain is not None and hot:
                # spare the runners: a position already up this much is treated
                # as accelerating rather than breaking down
                keep_hot = []
                for s in hot:
                    row = series.get(s, {}).get(day)
                    ent = holdings[s].get("entry")
                    if row and ent and ent > 0:
                        if (row[0] / ent - 1) * 100 >= atr_exempt_gain:
                            continue
                    keep_hot.append(s)
                hot = keep_hot
            if hot:
                px = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data "
                    "WHERE symbol = ANY($1) AND time::date=$2", hot, nxt)}
                for sym in hot:
                    if sym not in px or px[sym] <= 0:
                        continue
                    # ---- OPTION C: partial trim. Sell only a fraction and keep
                    # the remainder exposed to the trend, re-evaluated at the
                    # next rebalance. The sold half is written as its OWN closed
                    # trade row so the MtM/equity reconstruction sees it; leaving
                    # it only in memory would silently understate realised P&L.
                    if atr_trim_pct < 100.0 and holdings[sym]["qty"] > 1:
                        h = holdings[sym]
                        sell_qty = int(h["qty"] * atr_trim_pct / 100.0)
                        if sell_qty >= 1:
                            part = dict(h)
                            part["qty"] = sell_qty
                            part["db_id"] = await _clone_trade_row(h, sell_qty)
                            await close_out(sym, part, nxt, px[sym], "ATR_TRIM")
                            h["qty"] -= sell_qty
                            await pool.execute(
                                "UPDATE backtest_trades SET quantity=$2 WHERE id=$1",
                                h["db_id"], h["qty"])
                            atr_breach_run.pop(sym, None)
                            continue
                    await close_out(sym, holdings.pop(sym), nxt, px[sym], "ATR_CEILING")

        # ---- ATR TRAILING STOP (2026-08-18). Chandelier-style: exit when the
        # close gives back mult x ATR% from the highest close since entry.
        # h["peak"] is maintained by the daily stop-check block above. Fills at
        # T+1 open, NOT the triggering close — a close-triggered rule cannot
        # actually be executed at the close that revealed it.
        if sl_mode == "atr_trail" and holdings and nxt is not None:
            hit = []
            for sym, h in holdings.items():
                row = series.get(sym, {}).get(day)
                a = atr_series.get(sym, {}).get(day)
                if row is None or not a:
                    continue
                px_close = row[0]
                if sl_arm > 0 and h.get("peak", px_close) < h["entry"] * (1 + sl_arm / 100):
                    continue        # stop not armed yet (Martin-port, 2026-08-20)
                if px_close <= h.get("peak", px_close) * (1 - sl_atr_mult * a / 100.0):
                    hit.append(sym)
            if hit:
                px = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data "
                    "WHERE symbol = ANY($1) AND time::date=$2", hit, nxt)}
                for sym in hit:
                    if sym in px and px[sym] > 0:
                        await close_out(sym, holdings.pop(sym), nxt, px[sym], "SL_ATR_TRAIL")

        # ---- cash yield on uninvested capital (spec 3.A). Accrued daily on
        # (running equity - cost basis of open positions); with the book
        # liquidated in risk-off this is the whole account, which is the point.
        if cash_annual > 0:
            deployed = sum(h["entry"] * h["qty"] for h in holdings.values())
            idle = max(0.0, capital + realized_total[0] + cash_credit[0] - deployed)
            cash_credit[0] += idle * (cash_annual / 100.0) / 365.0

        # ---- H3: accumulate today's equal-weighted return of the held book.
        # Done for EVERY session (not just rebalances) so sigma_hat is estimated
        # from ~126 observations rather than the ~6 the flawed earlier version
        # had. A name with no quote today is skipped rather than treated as 0%,
        # which would bias the variance downward.
        if vol_target is not None and holdings and i > 0:
            prev = days[i - 1]
            rs = []
            for sym in holdings:
                s_ = series.get(sym)
                if not s_:
                    continue
                a_, b_ = s_.get(prev), s_.get(day)
                if a_ and b_ and a_[0] > 0:
                    rs.append(b_[0] / a_[0] - 1.0)
            if rs:
                book_daily_rets.append(sum(rs) / len(rs))

        # ---- selection cadence: scheduled every rebalance_days, OR forced by
        # a risk-on transition. Frozen entirely while risk-off.
        if risk_off_today or nxt is None:
            continue
        if not force_rebalance and i % rebalance_days != 0:
            continue

        # composite path takes no LIMIT param — ranking must see the whole
        # candidate set before the top-buffer_n slice is taken in Python.
        raw = (await pool.fetch(rank_sql, day, min_turnover) if composite
               else await pool.fetch(rank_sql, day, min_turnover, buffer_n))
        # ATR as-of this rebalance, for inverse-vol sizing (HYP B). The
        # composite SELECT already carries atr_pct; the single-column path
        # does not, so fetch it only when actually needed.
        atr_at_rebal: dict = {}
        if size_mode == "inverse_vol":
            if composite:
                atr_at_rebal = {r["symbol"]: float(r["atr_pct"]) for r in raw
                                if r["atr_pct"] is not None}
            else:
                atr_at_rebal = {r["symbol"]: float(r["a"]) for r in await pool.fetch(
                    "SELECT symbol, atr_pct AS a FROM stock_indicators "
                    "WHERE indicator_date=$1 AND atr_pct IS NOT NULL", day)}
        sma_by_sym = {r["symbol"]: r["sma_200"] for r in raw}
        if composite:
            ranked = _score_composite(raw, buffer_n)
        else:
            ranked = raw
        # Regime shield: when risk-off, hold NOTHING new and let the drop
        # logic below liquidate the book (keep set is empty), parking in cash.
        # Regime is now handled by the DAILY guard above (which liquidates on
        # the transition and `continue`s past this block while risk-off), so
        # reaching here already implies risk-on. The old rebalance-day-only
        # check that used to live here is gone — it was the 19-session lag.
        keep = {r["symbol"] for r in ranked}

        # ---- HYP C: internal FACTOR BREADTH scaling (2026-08-18). Macro
        # index trend missed the 2025 momentum-factor crash entirely; this
        # measures the factor's OWN health — % of the liquid universe with a
        # positive 3-month return — and scales gross exposure 100/50/0.
        exposure = 1.0
        if breadth_scaling:
            br = await pool.fetchval(
                "SELECT AVG(CASE WHEN pct_chg_3m > 0 THEN 1.0 ELSE 0.0 END) "
                "FROM stock_indicators WHERE indicator_date=$1 "
                "AND turnover_1m_avg_cr >= $2 AND pct_chg_3m IS NOT NULL",
                day, min_turnover)
            br = float(br or 1.0) * 100
            exposure = 1.0 if br >= 50 else (0.5 if br >= 35 else 0.0)

        # ---- Martin-port (2026-08-20): EQUITY-CURVE THROTTLE. MtM equity vs
        # its own running peak; below peak by dd_pct -> scale NEW-entry gross
        # exposure by cut. Mirrors weekly_equity_throttle_mode='dd_peak'.
        if eq_throttle_dd is not None:
            mtm = capital + realized_total[0] + cash_credit[0]
            for _s, _h in holdings.items():
                _row = series.get(_s, {}).get(day)
                if _row is not None:
                    mtm += (_row[0] - _h["entry"]) * _h["qty"]
            eq_peak[0] = max(eq_peak[0], mtm)
            dd_now = (eq_peak[0] - mtm) / eq_peak[0] * 100.0 if eq_peak[0] > 0 else 0.0
            if eq_throttle_mode == "linear":
                frac = min(dd_now / eq_throttle_dd, 1.0)
                exposure *= 1.0 - (1.0 - eq_throttle_cut) * frac
            elif dd_now >= eq_throttle_dd:
                exposure *= eq_throttle_cut

        # ---- breadth-smile sizing: trim only the mid-band chop zone
        if b200_mid_cut is not None:
            br200 = await pool.fetchval(
                "SELECT AVG(CASE WHEN close > sma_200 THEN 100.0 ELSE 0 END) "
                "FROM stock_indicators WHERE indicator_date=$1 "
                "AND turnover_1m_avg_cr >= $2 AND sma_200 IS NOT NULL",
                day, min_turnover)
            if br200 is not None and b200_band_lo <= float(br200) < b200_band_hi:
                exposure *= b200_mid_cut

        # ---- HYP E3: permanent / volatility-triggered cash buffer
        if cash_buffer_pct:
            exposure *= (1 - cash_buffer_pct / 100.0)

        # ---- H3: BARROSO VOLATILITY TARGETING. Scale gross exposure by the
        # ratio of the target vol to the book's OWN recently realised vol.
        # Requires >=60 daily observations before it engages, so the first few
        # months run unscaled rather than on a meaningless estimate.
        if vol_target is not None and len(book_daily_rets) >= 60:
            import statistics as _vst
            w_ = book_daily_rets[-vol_lb_days:]
            sd_ = _vst.stdev(w_) if len(w_) > 1 else 0.0
            sigma_ann = sd_ * (252 ** 0.5) * 100.0
            if sigma_ann > 0:
                exposure *= min(vol_max_lev, vol_target / sigma_ann)

        # ---- HYP A: SECTOR CAP. Walk the ranking in order and skip a name
        # whose sector is already at its cap, taking the next-best from a
        # different sector instead (spec 2.A). NULL-sector names are exempt
        # (each treated as its own bucket) rather than lumped into one giant
        # pseudo-sector, which would falsely constrain unrelated stocks —
        # sector data covers only ~42% of the liquid universe, so this test
        # is a LOWER BOUND on the mechanism's true effect.
        if max_sector_pct:
            per_pos_pct = 100.0 / top_n
            max_per_sector = max(1, int(max_sector_pct // per_pos_pct))
            sec_count: dict = {}
            for s in holdings:            # existing book counts toward the cap
                sec = sector_of.get(s)
                if sec: sec_count[sec] = sec_count.get(sec, 0) + 1
            want = []
            for r in ranked:
                s = r["symbol"]
                if len(want) >= top_n: break
                sec = sector_of.get(s)
                if sec:
                    if sec_count.get(sec, 0) >= max_per_sector: continue
                    sec_count[sec] = sec_count.get(sec, 0) + 1
                want.append(s)
        else:
            want = [r["symbol"] for r in ranked][:top_n]

        # ---- HYP E2: RELATIVE-STRENGTH EXIT. Drop a holding whose composite
        # score sits below the median of the CURRENTLY HELD book, rather than
        # waiting for a full fall out of the buffer. Only meaningful with the
        # composite ranker (single-column ranking has no comparable score).
        if rs_exit_pct and composite and holdings:
            score_of = {r["symbol"]: r["mom"] for r in ranked}
            held_scores = sorted(score_of[s] for s in holdings if s in score_of)
            if len(held_scores) >= 4:
                cut = held_scores[int(len(held_scores) * rs_exit_pct / 100.0)]
                keep -= {s for s in holdings
                         if s in score_of and score_of[s] < cut}

        # ---- SELL anything that fell outside the buffer (or lost its SMA200,
        #      which removes it from the ranked set by construction)
        drop = [s for s in holdings if s not in keep]
        if drop:
            px = {r["symbol"]: r for r in await pool.fetch(
                "SELECT symbol, open FROM ohlcv_data WHERE symbol = ANY($1) AND time::date=$2",
                drop, nxt)}
            for sym in drop:
                f = px.get(sym)
                if f is None:
                    continue
                await close_out(sym, holdings.pop(sym), nxt, float(f["open"]),
                                "RANK_DROP")

        # ---- BUY into free slots from the top of the ranking
        slots = top_n - len(holdings)
        if slots > 0:
            buyable_syms = [s for s in want if s not in holdings]
            if earn_gate_days and buyable_syms and nxt is not None:
                from datetime import timedelta as _etd
                blocked_rows = await pool.fetch(
                    "SELECT DISTINCT symbol FROM earnings_filings "
                    "WHERE symbol = ANY($1) AND broadcast_date >= $2 "
                    "AND broadcast_date <= $3",
                    buyable_syms, nxt, nxt + _etd(days=earn_gate_days))
                blocked = {r["symbol"] for r in blocked_rows}
                buyable_syms = [s for s in buyable_syms if s not in blocked]
            adds = buyable_syms[:slots]
            if adds:
                fills = {r["symbol"]: r for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data WHERE symbol = ANY($1) AND time::date=$2",
                    adds, nxt)}
                # Average daily traded value at the SIGNAL date, used to cap each
                # position at adv_cap_pct% of it (turnover_1m_avg_cr is in Rs crore).
                adv_cr: dict = {}
                if adv_cap_pct is not None:
                    adv_cr = {r["symbol"]: float(r["t"]) for r in await pool.fetch(
                        "SELECT symbol, turnover_1m_avg_cr AS t FROM stock_indicators "
                        "WHERE symbol = ANY($1) AND indicator_date = $2 "
                        "AND turnover_1m_avg_cr IS NOT NULL",
                        adds, day)}
                # Equal weight across the FULL book. compounding_enabled (opt-in,
                # 2026-08-18) sizes off running equity = capital + realized P&L
                # instead of the original FIXED capital. Without it a 15-year
                # run never reinvests profit, which mechanically converts a
                # ~22%/yr strategy into ~11% CAGR (linear vs geometric growth)
                # — the exact gap seen between this engine and the research
                # harness in runs #768/#769. Default false = unchanged.
                base_capital = capital
                if compounding:
                    base_capital = max(comp_floor,
                                       capital + realized_total[0] + cash_credit[0])
                    if comp_ceiling is not None:
                        base_capital = min(base_capital, comp_ceiling)
                base_capital *= exposure          # HYP C / E3 gross-exposure scaling
                # ---- HYP B: INVERSE-VOLATILITY (risk-parity) SIZING.
                # weight_i = (1/vol_i) / sum(1/vol_j) over the names being
                # bought, vol = atr_pct. Equal-weight (1/N) gives a 6%-ATR
                # midcap the same risk budget as a 2%-ATR largecap, which
                # concentrates portfolio variance in the wildest names.
                # Normalised across top_n slots so a partial fill doesn't
                # silently lever the book.
                inv_w = {}
                if size_mode == "inverse_vol":
                    atrs = {s: (atr_at_rebal.get(s) or 0) for s in adds}
                    inv = {s: (1.0 / a) for s, a in atrs.items() if a and a > 0}
                    tot_inv = sum(inv.values())
                    if tot_inv > 0:
                        # scale so the FULL book (top_n slots) sums to 1.0
                        share = len(adds) / float(top_n)
                        inv_w = {s: (v / tot_inv) * share for s, v in inv.items()}
                alloc_equal = base_capital / top_n
                for sym in adds:
                    f = fills.get(sym)
                    if f is None or float(f["open"]) <= 0:
                        continue
                    alloc = (base_capital * inv_w[sym]) if sym in inv_w else alloc_equal
                    if adv_cap_pct is not None:
                        adv_value = adv_cr.get(sym)
                        if adv_value is None or adv_value <= 0:
                            continue      # no liquidity read -> cannot size safely
                        alloc = min(alloc, adv_value * 1e7 * adv_cap_pct / 100.0)
                    gross = float(f["open"])
                    entry = round(gross * (1 + slip / 100), 2)
                    qty = int(alloc / entry)
                    if qty <= 0:
                        continue
                    sma = float(sma_by_sym.get(sym) or 0)
                    risk = max(entry - sma, 0.01)   # SMA200 is the invalidation level
                    row = await pool.fetchrow(
                        """
                        INSERT INTO backtest_trades
                          (run_id, symbol, quant_rank, signal_date, entry_trigger_price,
                           structural_sl, risk_per_share, quantity, entry_type, status,
                           entry_fill_date, entry_fill_price, realized_pnl, gross_pnl)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'MOMENTUM_RANK','OPEN',$9,$10,$11,0)
                        RETURNING id
                        """,
                        run_id, sym, want.index(sym) + 1, nxt, round(gross, 2),
                        round(sma, 2), round(risk, 2), qty, nxt, entry,
                        round(-_leg_cost(entry * qty, False, cfg), 2))
                    # gross_entry is the RAW open, kept separately from `entry`
                    # (which includes buy slippage) so the frictionless gross
                    # P&L can be computed at exit.
                    holdings[sym] = {"entry": entry, "gross_entry": gross, "qty": qty,
                                     "date": nxt, "risk": risk, "db_id": row["id"],
                                     "peak": entry}
                    await warm_series(sym)
                    await warm_atr(sym)

    # Positions still open at the window end stay OPEN — the summary endpoint
    # marks them to the last close as unrealized, exactly like the breakout book.
    # MtM path stats (CAGR/maxDD/w12m/Martin/underwater) onto the run row —
    # this engine never wrote them, so POSITIONAL rows showed em-dashes in the
    # run table (same gap fixed for weekly/index/daily on 2026-08-17/18).
    from .path_stats import compute_and_store_mtm_stats
    await compute_and_store_mtm_stats(pool, run_id)
    await pool.execute("UPDATE backtest_runs SET status='COMPLETED', progress_day=$2, "
                       "completed_at=NOW() WHERE id=$1", run_id, len(days))
