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
VALID_SL_MODES = {"none", "fixed", "trail", *MA_STOPS}


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
    if momentum not in ("pct_chg_3m", "pct_chg_6m", "pct_chg_1y"):
        raise ValueError(f"unsupported pos_momentum {momentum!r}")
    rebalance_days = int(run.get("pos_rebalance_days") or 21)
    top_n = int(run.get("pos_top_n") or 10)
    buffer_n = max(int(run.get("pos_buffer_n") or 20), top_n)
    min_turnover = float(run.get("pos_min_turnover_cr") or 5.0)
    sl_mode = (run.get("pos_sl_mode") or "none").lower()
    if sl_mode not in VALID_SL_MODES:
        raise ValueError(f"unsupported pos_sl_mode {sl_mode!r}")
    sl_pct = float(run.get("pos_sl_pct") or 0.0)
    if sl_mode in ("fixed", "trail") and sl_pct <= 0:
        raise ValueError(f"pos_sl_mode={sl_mode} requires pos_sl_pct > 0")

    cfg = {
        "slippage_pct": float(run["slippage_pct"]),
        "stt_pct": float(run["stt_pct"]),
        "stamp_duty_pct": float(run["stamp_duty_pct"]),
        "exchange_charges_pct": float(run["exchange_charges_pct"]),
        "dp_charge": float(run["dp_charge"]),
        "brokerage_per_order": float(run["brokerage_per_order"]),
    }
    slip = cfg["slippage_pct"]

    days = [r["d"] for r in await pool.fetch(
        "SELECT DISTINCT time::date AS d FROM ohlcv_data "
        "WHERE time::date BETWEEN $1 AND $2 ORDER BY d", start_date, end_date)]
    await pool.execute("UPDATE backtest_runs SET progress_total_days=$1 WHERE id=$2",
                       len(days), run_id)
    if not days:
        await pool.execute("UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW() "
                           "WHERE id=$1", run_id)
        return

    rank_sql = f"""
        SELECT symbol, close, sma_200, {momentum} AS mom
        FROM stock_indicators
        WHERE indicator_date = $1 AND turnover_1m_avg_cr >= $2
          AND close > sma_200 AND {momentum} IS NOT NULL
        ORDER BY {momentum} DESC LIMIT $3
    """

    holdings: dict[str, dict] = {}

    # Per-symbol close+MA series, fetched ONCE when a name is first bought and
    # reused for every subsequent daily stop check. The naive alternative — one
    # query per day for the current holdings — is ~250 round trips per window
    # and dominated the runtime of the research sweep before it was cached.
    series: dict[str, dict] = {}
    ma_col = MA_STOPS[sl_mode][0] if sl_mode in MA_STOPS else None

    async def warm_series(sym: str) -> None:
        if sym in series or sl_mode == "none":
            return
        sel = f", si.{ma_col} AS ma" if ma_col else ", NULL::numeric AS ma"
        series[sym] = {
            r["d"]: (float(r["c"]), float(r["ma"]) if r["ma"] is not None else None)
            for r in await pool.fetch(
                f"SELECT o.time::date AS d, o.close AS c{sel} "
                "FROM ohlcv_data o LEFT JOIN stock_indicators si "
                "  ON si.symbol=o.symbol AND si.indicator_date=o.time::date "
                "WHERE o.symbol=$1 AND o.time::date BETWEEN $2 AND $3",
                sym, start_date, end_date)}

    async def close_out(sym: str, h: dict, when, exit_px: float, reason: str) -> None:
        """Single exit path for both stops and rotations, so a stopped trade is
        costed, R-scored and recorded exactly like any other."""
        net = round(exit_px * (1 - slip / 100), 2)
        pnl = (net - h["entry"]) * h["qty"] - _leg_cost(net * h["qty"], True, cfg)
        # gross_pnl is the FRICTIONLESS result — raw price to raw price, no
        # slippage and no charges — which is what the summary subtracts net from
        # to report cost drag. Omitting it (as an earlier version did) leaves
        # gross at 0 and makes the UI show a nonsensical negative cost equal to
        # the whole realized P&L.
        gross = (exit_px - float(h["gross_entry"])) * h["qty"]
        rmul = round(pnl / (h["risk"] * h["qty"]), 3) if h["risk"] and h["qty"] else None
        await pool.execute(
            """
            UPDATE backtest_trades SET status='CLOSED', exit_date=$2, exit_price=$3,
              exit_reason=$8, realized_pnl=$4, r_multiple=$5, holding_days=$6,
              gross_pnl=$7
            WHERE id=$1
            """,
            h["db_id"], when, net, round(pnl, 2), rmul, (when - h["date"]).days,
            round(gross, 2), reason)

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
                if sl_mode == "fixed" and px <= h["entry"] * (1 - sl_pct / 100):
                    stopped.append((sym, px, "SL_FIXED"))
                elif sl_mode == "trail" and px <= h["peak"] * (1 - sl_pct / 100):
                    stopped.append((sym, px, "SL_TRAIL"))
                elif ma_col and ma and px < ma:
                    stopped.append((sym, px, MA_STOPS[sl_mode][1]))
            for sym, px, reason in stopped:
                await close_out(sym, holdings.pop(sym), day, px, reason)

        if i % rebalance_days != 0 or i + 1 >= len(days):
            continue
        nxt = days[i + 1]

        ranked = await pool.fetch(rank_sql, day, min_turnover, buffer_n)
        keep = {r["symbol"] for r in ranked}
        want = [r["symbol"] for r in ranked][:top_n]
        sma_by_sym = {r["symbol"]: r["sma_200"] for r in ranked}

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
            adds = [s for s in want if s not in holdings][:slots]
            if adds:
                fills = {r["symbol"]: r for r in await pool.fetch(
                    "SELECT symbol, open FROM ohlcv_data WHERE symbol = ANY($1) AND time::date=$2",
                    adds, nxt)}
                alloc = capital / top_n     # equal weight across the FULL book
                for sym in adds:
                    f = fills.get(sym)
                    if f is None or float(f["open"]) <= 0:
                        continue
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

    # Positions still open at the window end stay OPEN — the summary endpoint
    # marks them to the last close as unrealized, exactly like the breakout book.
    await pool.execute("UPDATE backtest_runs SET status='COMPLETED', progress_day=$2, "
                       "completed_at=NOW() WHERE id=$1", run_id, len(days))
