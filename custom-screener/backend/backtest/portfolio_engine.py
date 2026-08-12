"""Continuous portfolio simulation, 2016 -> 2026, with portfolio-level risk control.

WHY THIS EXISTS — and why every earlier number in this project is not comparable
to these ones.

Everything before this ran ELEVEN INDEPENDENT ONE-YEAR BACKTESTS and summed the
P&L. That is not a portfolio. It resets capital to Rs.4L every January, discards
open positions at each year end, and therefore cannot compound, cannot express a
drawdown that spans a year boundary, and cannot answer the only questions that
matter for a real book: what is the CAGR, and how bad does the equity curve get
between two arbitrary dates? A strategy that loses 40% in Dec 2017 and makes it
back in Jan 2018 shows up in the old framework as "2017: bad, 2018: good" and in
this one as a single 40% drawdown, which is what it actually was.

So this runs ONE simulation start to finish:
  * capital carried forward and compounding
  * positions carried across year ends
  * daily mark to market
  * costs, caps and cash all applied at portfolio level
  * metrics that describe the PATH (maxDD, ulcer, worst rolling 12m), not just
    the destination (total P&L)

THE CONTROLS, and the reasoning behind how each is implemented:

1. EXPOSURE BUDGET (volatility scaling). Earlier "sit out bad regimes" rules were
   binary and late: they turned fully off after the drawdown had begun and fully
   on after the recovery had begun, converting unrealised drawdown into realised
   loss (campaign v5 made 2018 and 2019 WORSE). This is the graded version. It
   scales exposure at the existing 63-session rebalance, never daily, so it adds
   almost no turnover - and turnover is the thing that has killed every other
   idea in this project.

   Implemented as SLOT COUNT, not position size: n_slots = round(top_n * exposure),
   each slot always 1/top_n of equity. So 50% exposure means holding 10 names of
   5% each and 50% cash - NOT 20 names of 2.5%. The alternative (shrinking every
   position) both raises turnover and, perversely, concentrates the book more in
   bad times, since position size would rise as exposure falls.

   Thresholds are EXPANDING-WINDOW PERCENTILES of the strategy's own realised
   volatility, i.e. "is today's vol high relative to everything seen SO FAR".
   Fixed thresholds (15%/25%/35%) are also offered, but percentile-of-past is
   the honest default: a fixed threshold is a free parameter fitted to the
   sample, and computing percentiles over the whole sample would be look-ahead.

2. FIXED STOP, treated as a range. 10/15/20% only, walk-forward validated. No
   further sweeping - the supported finding is "a moderate fixed stop helps",
   not "15% is the optimum" (its FIT rank was 1st, its TEST rank 12th of 26).

3. PORTFOLIO-LEVEL LIMITS, because per-stock stops do nothing about correlated
   losses - twenty stocks each stopping at -15% in the same week is a -15% book.
     * max % of equity per stock
     * max % of equity per sector, and max stocks per sector
     * drawdown throttle: halve new exposure past a drawdown threshold, restore
       in steps (not in one jump) once recovered
     * explicit cash residual - unallocated capital earns nothing and is not
       quietly assumed to be invested

   SECTOR DATA IS INCOMPLETE. symbols_meta.sector is populated only for current
   NSE index constituents: ~38% of the liquid universe and ~55% of the names this
   strategy actually trades. Two policies are therefore offered and BOTH must be
   reported, because each is biased in a different direction:
     require_sector=False  unknown-sector names are unconstrained -> the cap is
                           real but binds on only ~half the book
     require_sector=True   unknown-sector names are excluded entirely -> the cap
                           is fully enforced, but the universe is now "today's
                           index members", which is survivorship-contaminated
   Neither is clean. Reporting one alone would be misleading.

These controls are NOT expected to raise returns. They are expected to lower
CAGR somewhat and lower drawdown more. The test is whether the second effect
exceeds the first.
"""
from __future__ import annotations

import math
import random
import statistics as st
from datetime import date

# Dhan equity delivery, identical to simulator._leg_costs and positional_sweep.
STT_PCT, STAMP_PCT, EXCH_PCT, DP_CHARGE = 0.100, 0.015, 0.0030, 14.75
TRADING_DAYS = 252


def _leg_cost(value: float, is_sell: bool) -> float:
    c = value * STT_PCT / 100 + value * EXCH_PCT / 100
    return c + (DP_CHARGE if is_sell else value * STAMP_PCT / 100)


DEFAULTS = {
    "start": date(2016, 1, 1),
    "end": date(2026, 8, 8),
    "capital": 400000.0,
    "slippage": 0.10,
    # --- selection (held at the plateau-supported settings; NOT re-optimised
    #     here, because re-tuning selection and risk control simultaneously
    #     would make it impossible to attribute any change to either)
    "momentum": "pct_chg_6m",
    "rebalance_days": 63,
    "top_n": 20,
    "buffer_n": 40,
    "min_turnover": 5.0,
    # --- stop
    "sl_pct": 15.0,              # 0 disables
    # --- exposure budget
    "vol_mode": "none",          # none | pct (expanding percentile) | abs
    "vol_lookback": 63,
    "vol_levels": (1.0, 0.75, 0.50, 0.25),
    "vol_abs_bands": (15.0, 25.0, 35.0),   # annualised %, for vol_mode='abs'
    # --- portfolio limits
    # DEFAULTS MUST BE INERT. An earlier version shipped the sector caps ON by
    # default, which meant the "no sector caps" baseline silently had them and
    # the "+sectorcaps" variant was byte-identical to it — a no-op that looked
    # like the finding "sector caps don't matter". They matter: a mid-2018
    # top-20 had NINE names in one sector. Any control whose default is active
    # cannot be measured against a baseline, so all of these are off unless a
    # config asks for them. max_per_stock is set to 100 for the same reason;
    # note it is near-inert anyway at top_n=20, where a slot is 1/20 = 5%.
    "max_per_stock_pct": 100.0,
    "max_per_sector_pct": 100.0,
    "max_stocks_per_sector": 99,
    "require_sector": False,
    "dd_throttle_at": 0.0,       # 0 disables; e.g. 0.10 = throttle past -10%
    "dd_restore_at": 0.05,
    "dd_throttle_factor": 0.5,
    # --- survivorship stress (see survivorship.py). Injects the losses the
    #     dataset CANNOT contain. 0 disables, reproducing the raw backtest.
    "delist_hazard_pa": 0.0,     # annual probability a held name blows up
    "delist_recovery": 0.0,      # fraction of value recovered when it does
    "delist_seed": 0,
}


async def _load_sectors(pool) -> dict[str, str]:
    return {r["symbol"]: r["sector"] for r in await pool.fetch(
        "SELECT symbol, sector FROM symbols_meta WHERE sector IS NOT NULL")}


def _exposure_from_vol(vol: float, history: list[float], cfg) -> float:
    """Map realised volatility to a fraction of full exposure.

    'pct' compares today's vol to the distribution of vol seen SO FAR, which
    needs no magic numbers and cannot see the future. Below 8 observations the
    distribution is meaningless, so exposure stays at 100% rather than being
    decided by noise."""
    lv = cfg["vol_levels"]
    if cfg["vol_mode"] == "abs":
        lo, mid, hi = cfg["vol_abs_bands"]
        return lv[0] if vol < lo else lv[1] if vol < mid else lv[2] if vol < hi else lv[3]
    if len(history) < 8:
        return lv[0]
    q = sorted(history)
    p25, p50, p75 = (q[int(len(q) * f)] for f in (0.25, 0.50, 0.75))
    return lv[0] if vol <= p25 else lv[1] if vol <= p50 else lv[2] if vol <= p75 else lv[3]


def _metrics(curve: list[tuple], capital: float, buy_val: float, sell_val: float,
             n_trades: int, n_win: int) -> dict:
    """Path-describing metrics. Total P&L is deliberately not the headline."""
    eq = [v for _, v in curve]
    final = eq[-1]
    n = len(eq)
    years = n / TRADING_DAYS
    cagr = (final / capital) ** (1 / years) - 1 if final > 0 else -1.0

    peak, max_dd, dd2 = eq[0], 0.0, []
    for v in eq:
        peak = max(peak, v)
        dd = (peak - v) / peak
        max_dd = max(max_dd, dd)
        dd2.append(dd * dd)
    ulcer = math.sqrt(sum(dd2) / len(dd2)) * 100

    # Worst return over any 252-session window — the number that tells you what
    # a bad YEAR feels like regardless of where the calendar boundaries fall.
    worst_12m = 0.0
    if n > TRADING_DAYS:
        worst_12m = min(eq[i + TRADING_DAYS] / eq[i] - 1
                        for i in range(n - TRADING_DAYS))

    # Calendar-year returns, from the equity curve rather than summed P&L.
    by_year, cal = {}, {}
    for d, v in curve:
        by_year.setdefault(d.year, []).append(v)
    prev_end = capital
    for y in sorted(by_year):
        cal[y] = by_year[y][-1] / prev_end - 1
        prev_end = by_year[y][-1]

    avg_eq = sum(eq) / len(eq)
    turnover = ((buy_val + sell_val) / 2) / avg_eq / years

    return {
        "final": round(final),
        "totalReturnPct": round((final / capital - 1) * 100, 1),
        "cagrPct": round(cagr * 100, 2),
        "maxDDPct": round(max_dd * 100, 1),
        "ulcer": round(ulcer, 2),
        "worst12mPct": round(worst_12m * 100, 1),
        "turnoverPerYr": round(turnover, 2),
        "trades": n_trades,
        "winRatePct": round(100 * n_win / max(n_trades, 1), 1),
        "yearsPositive": sum(1 for v in cal.values() if v > 0),
        "years": len(cal),
        "calendar": {y: round(v * 100, 1) for y, v in cal.items()},
        # Return per unit of pain. Ulcer is the denominator rather than maxDD
        # because it accounts for how LONG drawdowns last, not just how deep the
        # single worst one got.
        "martin": round(cagr * 100 / ulcer, 2) if ulcer else None,
    }


async def run_portfolio(pool, _audit=None, _audit_trade=None, _audit_rebal=None,
                        **overrides) -> dict:
    """Audit hooks, all None in production and costing nothing. They exist so
    test_portfolio_engine.py can assert the accounting identities DIRECTLY,
    rather than re-implementing the engine and proving only that two copies of
    the same logic agree.

      _audit(dict)        once per session: cash, marked holdings, equity
      _audit_trade(dict)  once per closed trade: prices, qty, costs, exit reason
      _audit_rebal(dict)  once per rebalance: equity, the slot size derived from
                          it, top_n, n_slots and exposure

    _audit_rebal exists specifically because the obvious test of compounding —
    double the starting capital and check the final equity roughly doubles —
    proves nothing. An engine that wrongly sized every slot from the INITIAL
    capital would scale just as linearly and pass. Only comparing the slot size
    against the equity AT THAT REBALANCE can distinguish the two."""
    cfg = {**DEFAULTS, **overrides}
    cap0 = cfg["capital"]
    slip = cfg["slippage"]
    top_n = cfg["top_n"]
    sectors = await _load_sectors(pool)

    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", cfg["start"], cfg["end"])]
    if not days:
        return {}

    rank_sql = f"""
        SELECT symbol FROM stock_indicators
        WHERE indicator_date=$1 AND turnover_1m_avg_cr>=$2 AND close>sma_200
          AND {cfg['momentum']} IS NOT NULL
        ORDER BY {cfg['momentum']} DESC LIMIT $3
    """

    series: dict[str, dict] = {}

    async def warm(sym: str) -> None:
        if sym not in series:
            series[sym] = {r["d"]: float(r["c"]) for r in await pool.fetch(
                "SELECT time::date AS d, close AS c FROM ohlcv_data "
                "WHERE symbol=$1 AND time::date BETWEEN $2 AND $3",
                sym, cfg["start"], cfg["end"])}

    # Survivorship stress. Its own RNG instance, seeded, so a run is exactly
    # reproducible and does not perturb any other random state in the process.
    rng = random.Random(cfg["delist_seed"])
    daily_hazard = cfg["delist_hazard_pa"] / TRADING_DAYS
    n_delisted = 0

    holdings: dict[str, dict] = {}
    cash = cap0
    curve: list[tuple] = []
    daily_ret: list[float] = []
    vol_hist: list[float] = []
    buy_val = sell_val = 0.0
    n_trades = n_win = 0
    throttle_step = 0          # 0 = no throttle, 1 = halved, 2 = partial restore
    peak_eq = cap0
    exposure_log: list[tuple] = []

    def equity(day) -> float:
        return cash + sum(h["last"] * h["qty"] for h in holdings.values())

    def sell(sym: str, day, px: float, reason: str) -> None:
        nonlocal cash, sell_val, n_trades, n_win
        h = holdings.pop(sym)
        net = px * (1 - slip / 100)
        gross = net * h["qty"]
        proceeds = gross - _leg_cost(gross, True)
        cash += proceeds
        sell_val += gross
        n_trades += 1
        n_win += (proceeds - h["cost"]) > 0
        if _audit_trade:
            _audit_trade({"sym": sym, "entry": h["date"], "exit": day,
                          "entry_px": h["entry"], "raw_open": h["raw_open"],
                          "exit_px": net, "qty": h["qty"], "cost": h["cost"],
                          "proceeds": proceeds, "reason": reason})

    for i, day in enumerate(days):
        # ---- daily mark to market
        for sym, h in holdings.items():
            px = series.get(sym, {}).get(day)
            if px is not None:
                h["last"] = px
                h["peak"] = max(h.get("peak", px), px)

        # ---- daily stop check (a stopped slot stays in cash until rebalance)
        if cfg["sl_pct"] > 0 and holdings:
            for sym in [s for s, h in holdings.items()
                        if h["last"] <= h["entry"] * (1 - cfg["sl_pct"] / 100)]:
                # Exit at the CLOSE that revealed the breach, not at the stop
                # level. Only daily bars are available, so a fill at exactly the
                # stop price would assume the resting order was filled intraday
                # at its limit — which is precisely what does NOT happen on the
                # gap-downs that cause the worst losses. Exiting at the close is
                # the conservative choice and it is what produced the numbers in
                # BACKTEST_REPORT.md section 9.
                sell(sym, day, holdings[sym]["last"], "STOP")

        # ---- survivorship stress: force the blow-up losses the dataset cannot
        #      contain. Applied BEFORE the equity mark so the hit lands on the
        #      curve the same day, and after the stop check so a name that would
        #      have stopped out normally is not double-counted as a blow-up.
        if daily_hazard > 0 and holdings:
            for sym in [s for s in holdings if rng.random() < daily_hazard]:
                sell(sym, day, holdings[sym]["last"] * cfg["delist_recovery"],
                     "DELISTED")
                n_delisted += 1

        eq = equity(day)
        curve.append((day, eq))
        if _audit:
            _audit({"day": day, "cash": cash, "equity": eq,
                    "held": sum(h["last"] * h["qty"] for h in holdings.values()),
                    "n": len(holdings)})
        if len(curve) > 1:
            prev = curve[-2][1]
            daily_ret.append(eq / prev - 1 if prev else 0.0)
        peak_eq = max(peak_eq, eq)

        if i % cfg["rebalance_days"] != 0 or i + 1 >= len(days):
            continue
        nxt = days[i + 1]

        # ---- exposure budget -------------------------------------------------
        exposure = 1.0
        vol = 0.0
        if cfg["vol_mode"] != "none" and len(daily_ret) >= cfg["vol_lookback"]:
            window = daily_ret[-cfg["vol_lookback"]:]
            vol = st.pstdev(window) * math.sqrt(TRADING_DAYS) * 100
            exposure = _exposure_from_vol(vol, vol_hist, cfg)
            vol_hist.append(vol)

        # Drawdown throttle. Restores in two steps rather than one so the book
        # does not jump straight back to full size on the first good week.
        if cfg["dd_throttle_at"] > 0:
            dd = (peak_eq - eq) / peak_eq if peak_eq else 0.0
            if dd >= cfg["dd_throttle_at"]:
                throttle_step = 1
            elif throttle_step and dd <= cfg["dd_restore_at"]:
                throttle_step = 2 if throttle_step == 1 else 0
            if throttle_step == 1:
                exposure *= cfg["dd_throttle_factor"]
            elif throttle_step == 2:
                exposure *= (1 + cfg["dd_throttle_factor"]) / 2

        n_slots = max(1, round(top_n * exposure))
        exposure_log.append((day, round(exposure, 3), round(vol, 1), n_slots))

        # ---- ranking ---------------------------------------------------------
        ranked = [r["symbol"] for r in
                  await pool.fetch(rank_sql, day, cfg["min_turnover"], cfg["buffer_n"])]
        if cfg["require_sector"]:
            ranked = [s for s in ranked if s in sectors]
        keep = set(ranked)

        # ---- sells: fell out of the buffer, or over the slot budget ----------
        drop = [s for s in holdings if s not in keep]
        if len(holdings) - len(drop) > n_slots:
            # Trim the worst-ranked survivors down to the budget. Rank order is
            # the only defensible tie-break: it is the same signal the strategy
            # uses to buy.
            order = {s: n for n, s in enumerate(ranked)}
            survivors = sorted((s for s in holdings if s in keep),
                               key=lambda s: order.get(s, 10**6), reverse=True)
            drop += survivors[:len(holdings) - len(drop) - n_slots]
        if drop:
            fills = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                "SELECT symbol, open FROM ohlcv_data WHERE symbol=ANY($1) AND time::date=$2",
                drop, nxt)}
            for s in drop:
                if s in fills:
                    sell(s, nxt, fills[s], "ROTATE")

        # ---- buys ------------------------------------------------------------
        free = n_slots - len(holdings)
        if free > 0:
            eq_now = equity(nxt)
            cand = [s for s in ranked if s not in holdings][:free * 4]
            if cand:
                fills = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data WHERE symbol=ANY($1) AND time::date=$2",
                    cand, nxt)}
                # Slot size is 1/top_n of equity REGARDLESS of exposure, so
                # cutting exposure cuts the number of positions and raises cash,
                # instead of quietly concentrating the book.
                slot = eq_now / top_n
                slot = min(slot, eq_now * cfg["max_per_stock_pct"] / 100)
                if _audit_rebal:
                    _audit_rebal({"day": nxt, "equity": eq_now, "slot": slot,
                                  "top_n": top_n, "n_slots": n_slots,
                                  "exposure": exposure,
                                  "capped": cfg["max_per_stock_pct"] < 100})

                sec_val: dict[str, float] = {}
                sec_cnt: dict[str, int] = {}
                for s, h in holdings.items():
                    sec = sectors.get(s)
                    if sec:
                        sec_val[sec] = sec_val.get(sec, 0) + h["last"] * h["qty"]
                        sec_cnt[sec] = sec_cnt.get(sec, 0) + 1

                for sym in cand:
                    if free <= 0:
                        break
                    o = fills.get(sym)
                    if not o or o <= 0:
                        continue
                    sec = sectors.get(sym)
                    if sec:      # unknown sector => unconstrained (see docstring)
                        if sec_cnt.get(sec, 0) >= cfg["max_stocks_per_sector"]:
                            continue
                        if (sec_val.get(sec, 0) + slot
                                > eq_now * cfg["max_per_sector_pct"] / 100):
                            continue
                    entry = o * (1 + slip / 100)
                    qty = int(slot / entry)
                    if qty <= 0:
                        continue
                    gross = entry * qty
                    outlay = gross + _leg_cost(gross, False)
                    if outlay > cash:
                        continue
                    cash -= outlay
                    buy_val += gross
                    await warm(sym)
                    holdings[sym] = {"qty": qty, "cost": outlay, "entry": entry,
                                     "last": entry, "peak": entry,
                                     "raw_open": o, "date": nxt}
                    if sec:
                        sec_val[sec] = sec_val.get(sec, 0) + gross
                        sec_cnt[sec] = sec_cnt.get(sec, 0) + 1
                    free -= 1

    m = _metrics(curve, cap0, buy_val, sell_val, n_trades, n_win)
    m["avgExposure"] = (round(sum(e for _, e, _, _ in exposure_log) / len(exposure_log), 3)
                        if exposure_log else 1.0)
    m["openAtEnd"] = len(holdings)
    m["delisted"] = n_delisted
    # Still-open positions and the daily curve, for the persistence layer. The
    # curve is downsampled to weekly for storage: 2,600 daily points per run is
    # more than any chart needs and it bloats every list query that selects *.
    m["_open"] = [{"sym": s, **h} for s, h in holdings.items()]
    m["_curve"] = [(d.isoformat(), round(v)) for i, (d, v) in enumerate(curve)
                   if i % 5 == 0 or i == len(curve) - 1]
    return m
