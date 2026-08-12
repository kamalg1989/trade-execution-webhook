"""Exhaustive parameter sweep for the positional momentum book.

READ THIS BEFORE TRUSTING ANY "SWEET SPOT" IT REPORTS.

A grid of 48 configs x 11 windows is 528 backtests. At that size a best-looking
config is GUARANTEED to exist whether or not any real structure is present —
that is precisely how the six earlier "edges" in this project were manufactured
and then died. So this harness is built to answer a harder question than "which
config scored highest":

  1. SPLIT-SAMPLE. Years are split into FIT (2016-2020) and TEST (2021-2026).
     A parameter that only works because it was picked on the same data it was
     scored on will rank well on FIT and badly on TEST. The report shows both,
     and the rank correlation between them. If that correlation is near zero,
     the grid contains no transferable information and NO config should be
     adopted, however good its total looks.

  2. PLATEAUS, NOT PEAKS. A real parameter effect is smooth: neighbours of a
     good setting are also good. Noise produces isolated spikes. The report
     therefore prints results grouped by each axis so a plateau is visible,
     rather than just sorting by total P&L.

  3. REAL DRAWDOWN. The earlier positional module reported maxDD as 0.0, which
     is a missing metric, not a good result. This marks the book to market
     EVERY day and computes true peak-to-trough drawdown on the equity curve.
     For a ~100%-deployed strategy with measured +95%/-34% calendar years, that
     number matters more than the P&L.

Runs in-process with multiprocessing rather than through the one-run-at-a-time
API, since 528 sequential API runs would take ~6 hours.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import sys
from datetime import date

sys.path.insert(0, "/root/trade-execution-webhook")

STT_PCT, STAMP_PCT, EXCH_PCT, DP_CHARGE = 0.100, 0.015, 0.0030, 14.75

FIT_YEARS = {"2016", "2017", "2018", "2019", "2020"}
TEST_YEARS = {"2021", "2022", "2023", "2024", "2025", "2026ytd"}


def _leg_cost(value: float, is_sell: bool) -> float:
    c = value * STT_PCT / 100 + value * EXCH_PCT / 100
    return c + (DP_CHARGE if is_sell else value * STAMP_PCT / 100)


async def _run_one(pool, start: date, end: date, capital: float, slip: float,
                   momentum: str, rebalance: int, top_n: int, buffer_n: int,
                   min_turnover: float, sl_mode: str = "none", sl_pct: float = 0.0,
                   sl_confirm: int = 1, sl_buffer: float = 0.0,
                   sl_arm_pct: float = 0.0, reentry_block: int = 0,
                   entry_max_ext: float | None = None):
    """sl_mode, checked EVERY DAY (the base strategy only ever checked at
    rebalance, which is why it drew down ~45%):
        none    -- exit only at rebalance (rank drop / lost SMA200)
        fixed   -- hard stop sl_pct below the entry price
        trail   -- stop sl_pct below the highest close since entry
        sma200  -- daily close below the stock's own SMA200  (alias: struct)
        sma50   -- daily close below SMA50   (a mid-tier structural stop)
        ema21   -- daily close below EMA21   (the tightest structural stop;
                   there is no sma_21 column, and EMA21 is the line the breakout
                   book already trails on, so it is the like-for-like choice)
        ema50   -- daily close below EMA50
    The three MA stops are the same MECHANISM at three speeds, which is the point
    of testing them together: it turns "which stop" into a monotonic axis
    (fast -> slow) where a plateau is meaningful and a lone spike is not.
    After a stop fires the slot stays in CASH until the next rebalance, which is
    what a real trader does; refilling instantly would quietly re-buy the same
    falling names and flatter the result.

    ---- de-whipsaw modifiers (round 3) ----
    Round 2 found the MA stops monotonically WORSE the faster they are, and the
    diagnosis was churn: EMA21 doubled the trade count (407 -> 834) because
    momentum leaders routinely dip below their 21-day line mid-trend, get
    stopped, and are then re-bought at the next rebalance. Critically, the extra
    trades are NOT extra names — they are the SAME names round-tripping. So the
    fix is not a stricter entry filter; it is refusing to act on a dip that
    isn't a real breakdown. Each modifier attacks that from a different angle:

      sl_confirm    require N CONSECUTIVE closes in violation. A one-day poke
                    through the line stops meaning anything.
      sl_buffer     violation needs close < MA*(1 - buffer%), i.e. a noise band
                    around the line rather than a hair trigger.
      sl_arm_pct    the stop does not exist until the trade is up this much.
                    Turns the MA into a profit-protector rather than something
                    that kills positions before they've done anything.
      reentry_block a stopped symbol cannot be re-bought for N rebalance cycles.
                    Attacks the round-trip directly: it cannot re-buy what it
                    just sold, so a whipsaw costs one exit rather than an
                    exit-plus-re-entry pair.
      entry_max_ext only buy names within this % of their EMA21 — the one
                    genuine ENTRY-quality axis here, on the theory that a name
                    bought far extended is the one most likely to mean-revert
                    into its stop immediately.

    All five default to inert, so a run with defaults reproduces round 2 exactly
    and the two rounds stay directly comparable."""
    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start, end)]
    if not days:
        return None

    # The extension filter is applied to the BUY list only, never to the "still
    # ranked" set: a name must be able to stay held once bought even if it later
    # runs far from its EMA21, otherwise the filter silently becomes an exit
    # rule as well and its effect could not be attributed to entries.
    rank_sql = f"""
        SELECT symbol, sma_200, dist_ema_21_pct FROM stock_indicators
        WHERE indicator_date=$1 AND turnover_1m_avg_cr>=$2 AND close>sma_200
          AND {momentum} IS NOT NULL
        ORDER BY {momentum} DESC LIMIT $3
    """

    holdings: dict[str, dict] = {}
    cash = capital
    realized = 0.0
    n_closed = n_win = 0
    equity_curve: list[float] = []
    # symbol -> the rebalance number it becomes buyable again at
    blocked: dict[str, int] = {}
    rebal_no = 0

    # Per-symbol close series for the whole window, fetched ONCE on first hold
    # and reused. The obvious implementation — one query per day for the current
    # holdings — costs ~250 round trips per window per config, which dominated
    # everything else and put the 528-backtest sweep on a ~4 hour path. A symbol
    # is now queried once no matter how long it is held.
    series: dict[str, dict] = {}

    async def closes_for(sym: str) -> dict:
        if sym not in series:
            # Every MA the structural stops can reference comes along for the
            # ride, so switching stop type costs no extra round trips per symbol.
            def _f(v):
                return float(v) if v is not None else None
            series[sym] = {
                r["d"]: (float(r["c"]),
                         {"sma200": _f(r["sma_200"]), "sma50": _f(r["sma_50"]),
                          "ema21": _f(r["ema_21"]), "ema50": _f(r["ema_50"])})
                for r in await pool.fetch(
                    "SELECT o.time::date AS d, o.close AS c, "
                    "       si.sma_200, si.sma_50, si.ema_21, si.ema_50 "
                    "FROM ohlcv_data o LEFT JOIN stock_indicators si "
                    "  ON si.symbol=o.symbol AND si.indicator_date=o.time::date "
                    "WHERE o.symbol=$1 AND o.time::date BETWEEN $2 AND $3",
                    sym, start, end)}
        return series[sym]

    MA_STOPS = {"struct": "sma200", "sma200": "sma200", "sma50": "sma50",
                "ema21": "ema21", "ema50": "ema50"}

    for i, day in enumerate(days):
        # --- daily mark to market (this is what makes real drawdown possible)
        for s, h in holdings.items():
            row = series.get(s, {}).get(day)
            if row is not None:
                h["last"] = row[0]
                h["peak"] = max(h.get("peak", h["last"]), h["last"])
                h["ma"] = row[1]

        # --- DAILY stop-loss check (the base strategy had none at all)
        if sl_mode != "none" and holdings:
            stopped = []
            for s, h in holdings.items():
                px = h["last"]
                # armed: below the profit threshold the stop simply does not
                # exist. Uses PEAK, not last price — once a trade has been up
                # 10% the stop stays armed even if it gives that back, which is
                # the whole point of arming it.
                armed = (sl_arm_pct <= 0
                         or h["peak"] >= h["entry_px"] * (1 + sl_arm_pct / 100))
                hit = False
                if not armed:
                    pass
                elif sl_mode == "fixed":
                    hit = px <= h["entry_px"] * (1 - sl_pct / 100)
                elif sl_mode == "trail":
                    hit = px <= h["peak"] * (1 - sl_pct / 100)
                elif sl_mode in MA_STOPS:
                    ma = (h.get("ma") or {}).get(MA_STOPS[sl_mode])
                    hit = bool(ma) and px < ma * (1 - sl_buffer / 100)
                # Consecutive-day counter, reset the moment price recovers, so
                # sl_confirm means "N days in a row" and not "N days total".
                h["viol"] = h.get("viol", 0) + 1 if hit else 0
                if h["viol"] >= sl_confirm:
                    stopped.append(s)
            for s in stopped:
                h = holdings.pop(s)
                if reentry_block:
                    blocked[s] = rebal_no + reentry_block
                net = h["last"] * (1 - slip / 100)
                proceeds = net * h["qty"] - _leg_cost(net * h["qty"], True)
                cash += proceeds
                pnl = proceeds - h["cost_basis"]
                realized += pnl
                n_closed += 1
                n_win += pnl > 0

        equity_curve.append(cash + sum(h["last"] * h["qty"] for h in holdings.values()))

        if i % rebalance != 0 or i + 1 >= len(days):
            continue
        nxt = days[i + 1]
        rebal_no += 1
        ranked = await pool.fetch(rank_sql, day, min_turnover, buffer_n)
        keep = {r["symbol"] for r in ranked}
        # Buy list = ranked, minus anything still cooling off from a stop-out,
        # minus anything too far extended from its EMA21 if that filter is on.
        buyable = [r for r in ranked if blocked.get(r["symbol"], -1) < rebal_no]
        if entry_max_ext is not None:
            buyable = [r for r in buyable
                       if r["dist_ema_21_pct"] is not None
                       and float(r["dist_ema_21_pct"]) <= entry_max_ext]
        want = [r["symbol"] for r in buyable][:top_n]

        drop = [s for s in holdings if s not in keep]
        if drop:
            fills = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                "SELECT symbol, open FROM ohlcv_data WHERE symbol=ANY($1) AND time::date=$2",
                drop, nxt)}
            for s in drop:
                if s not in fills:
                    continue
                h = holdings.pop(s)
                net = fills[s] * (1 - slip / 100)
                proceeds = net * h["qty"] - _leg_cost(net * h["qty"], True)
                cash += proceeds
                pnl = proceeds - h["cost_basis"]
                realized += pnl
                n_closed += 1
                n_win += pnl > 0

        slots = top_n - len(holdings)
        if slots > 0:
            adds = [s for s in want if s not in holdings][:slots]
            if adds:
                fills = {r["symbol"]: float(r["open"]) for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data WHERE symbol=ANY($1) AND time::date=$2",
                    adds, nxt)}
                alloc = capital / top_n
                for s in adds:
                    o = fills.get(s)
                    if not o or o <= 0:
                        continue
                    entry = o * (1 + slip / 100)
                    qty = int(alloc / entry)
                    if qty <= 0:
                        continue
                    outlay = entry * qty + _leg_cost(entry * qty, False)
                    if outlay > cash:
                        continue          # never spend money the book doesn't have
                    cash -= outlay
                    await closes_for(s)   # warm this symbol's series for MTM
                    holdings[s] = {"qty": qty, "cost_basis": outlay, "last": entry,
                                   "entry_px": entry, "peak": entry, "ma": None}

    final = equity_curve[-1] if equity_curve else capital
    peak = equity_curve[0] if equity_curve else capital
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return {"total": round(final - capital, 2), "realized": round(realized, 2),
            "trades": n_closed, "winRate": round(100 * n_win / max(n_closed, 1), 1),
            "maxDD": round(max_dd, 2),
            "maxDDpct": round(max_dd / capital * 100, 1)}


def _worker(job: dict):
    """job is a dict rather than a positional tuple: with 12 knobs a tuple is
    one silent mis-ordering away from a whole sweep measuring the wrong thing,
    and that failure is invisible in the output."""
    from app.db import create_pool

    cfg = {"momentum": "pct_chg_6m", "rebalance": 63, "top_n": 20, "buffer_n": 40,
           "min_turnover": 5.0, "capital": 400000.0, "slip": 0.10,
           "sl_mode": "none", "sl_pct": 0.0, "sl_confirm": 1, "sl_buffer": 0.0,
           "sl_arm_pct": 0.0, "reentry_block": 0, "entry_max_ext": None,
           "label": ""}
    cfg.update(job)

    async def go():
        pool = await create_pool()
        out = []
        try:
            for y in range(2016, 2027):
                s = date(y, 1, 1)
                e = date(2026, 8, 8) if y == 2026 else date(y, 12, 31)
                r = await _run_one(
                    pool, s, e, cfg["capital"], cfg["slip"], cfg["momentum"],
                    cfg["rebalance"], cfg["top_n"], cfg["buffer_n"],
                    cfg["min_turnover"], cfg["sl_mode"], cfg["sl_pct"],
                    cfg["sl_confirm"], cfg["sl_buffer"], cfg["sl_arm_pct"],
                    cfg["reentry_block"], cfg["entry_max_ext"])
                if r:
                    r.update({k: v for k, v in cfg.items()})
                    r["window"] = str(y) if y < 2026 else "2026ytd"
                    out.append(r)
        finally:
            await pool.close()
        return out

    return asyncio.run(go())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--capital", type=float, default=400000)
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--min-turnover", type=float, default=5.0)
    ap.add_argument("--out", default="/root/possweep.json")
    a = ap.parse_args()

    # buffer is tied to top_n (2x) rather than being a free axis: an independent
    # buffer would add a fourth dimension of overfitting surface for a knob whose
    # only job is anti-churn hysteresis.
    # Stop-loss study, held on the plateau-supported settings from the first
    # sweep (6m/12m momentum, 63-day rebalance, top-20) so the SL effect is not
    # confounded by re-optimising everything else at the same time.
    # Round 2 adds the MA-stop speed ladder (ema21 < sma50 < ema50 < sma200).
    # Round 1's only structural stop was SMA200, which barely differed from no
    # stop at all (40% vs 42% maxDD) — the obvious follow-up is whether a FASTER
    # line does the job that SMA200 is too slow to do. `none` and `fixed 15`
    # (round 1's winner) stay in as fixed reference points so the two rounds are
    # directly comparable rather than merely adjacent.
    # Round 3 — can the EMA21 stop keep its drawdown advantage (25% vs 42%)
    # WITHOUT its churn (834 trades vs 407)? Round 2 established the raw MA
    # ladder; this asks whether the fast line's problem is the line itself or
    # merely its hair trigger. Two reference rows are kept so the comparison is
    # in-batch rather than against remembered numbers from a previous run.
    jobs = [
        {"label": "ref-none", "sl_mode": "none"},
        {"label": "ref-fixed15", "sl_mode": "fixed", "sl_pct": 15.0},
        {"label": "ref-ema21", "sl_mode": "ema21"},

        # (a) confirmation days — a one-day poke through the line stops counting
        {"label": "ema21-confirm2", "sl_mode": "ema21", "sl_confirm": 2},
        {"label": "ema21-confirm3", "sl_mode": "ema21", "sl_confirm": 3},
        {"label": "ema21-confirm5", "sl_mode": "ema21", "sl_confirm": 5},

        # (b) noise band below the line
        {"label": "ema21-buf2", "sl_mode": "ema21", "sl_buffer": 2.0},
        {"label": "ema21-buf4", "sl_mode": "ema21", "sl_buffer": 4.0},
        {"label": "ema21-buf6", "sl_mode": "ema21", "sl_buffer": 6.0},

        # (c) arm only once the trade has actually worked — turns the MA into a
        #     profit-protector instead of an early-life killer
        {"label": "ema21-arm10", "sl_mode": "ema21", "sl_arm_pct": 10.0},
        {"label": "ema21-arm20", "sl_mode": "ema21", "sl_arm_pct": 20.0},

        # (d) attack the round-trip directly: can't re-buy what you just sold
        {"label": "ema21-noreentry1", "sl_mode": "ema21", "reentry_block": 1},
        {"label": "ema21-noreentry2", "sl_mode": "ema21", "reentry_block": 2},

        # (e) combinations of whichever levers are individually plausible
        {"label": "ema21-c3+buf4", "sl_mode": "ema21", "sl_confirm": 3, "sl_buffer": 4.0},
        {"label": "ema21-c3+arm20", "sl_mode": "ema21", "sl_confirm": 3, "sl_arm_pct": 20.0},
        {"label": "ema21-c3+buf4+nore1", "sl_mode": "ema21", "sl_confirm": 3,
         "sl_buffer": 4.0, "reentry_block": 1},
        {"label": "ema21-arm20+buf4", "sl_mode": "ema21", "sl_arm_pct": 20.0,
         "sl_buffer": 4.0},

        # (f) same de-whipsaw treatment on the mid-speed line, to check any
        #     finding is about the MECHANISM and not specific to EMA21
        {"label": "sma50-c3+buf4", "sl_mode": "sma50", "sl_confirm": 3, "sl_buffer": 4.0},
        {"label": "sma50-arm20", "sl_mode": "sma50", "sl_arm_pct": 20.0},

        # (g) the entry-quality axis, on the round-2 winner — the only variant
        #     here that reduces trades by buying LESS rather than selling less
        {"label": "fixed15+ext15", "sl_mode": "fixed", "sl_pct": 15.0, "entry_max_ext": 15.0},
        {"label": "ema21+ext15", "sl_mode": "ema21", "entry_max_ext": 15.0},
    ]
    for j in jobs:
        j.setdefault("min_turnover", a.min_turnover)
        j.setdefault("capital", a.capital)
        j.setdefault("slip", a.slippage)
    print(f"positional sweep: {len(jobs)} configs x 11 windows = {len(jobs)*11} backtests, "
          f"{a.workers} workers", flush=True)

    rows = []
    with mp.Pool(a.workers) as p:
        for k, res in enumerate(p.imap_unordered(_worker, jobs), 1):
            rows.extend(res)
            print(f"  {k}/{len(jobs)} configs done", flush=True)
    with open(a.out, "w") as fh:
        json.dump(rows, fh)
    print(f"WROTE {len(rows)} rows -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
