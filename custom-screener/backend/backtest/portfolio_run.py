"""Persistence wrapper: runs portfolio_engine and writes it into the normal
run/trade tables so a continuous-portfolio backtest is reviewable in the UI.

portfolio_engine is deliberately pure - it takes a pool and returns a dict, with
no knowledge of backtest_runs. That is what lets test_portfolio_engine assert its
accounting identities without a run row existing, and what lets the sweeps call
it 30 times in a loop without writing 30 rows of junk. This module is the only
place that knows about persistence.

Trades are captured through the engine's existing _audit_trade hook rather than
by threading INSERTs through the engine, so the hot loop stays free of database
writes and the engine keeps working identically whether or not anyone is
listening.

Field mapping onto the breakout-shaped trade row (same approach as sql/020):
  entry_trigger_price = raw next-day open used for the fill (pre-slippage)
  structural_sl       = the fixed-stop level, entry * (1 - sl_pct/100), so
                        risk_per_share and therefore r_multiple stay meaningful
  entry_type          = 'PORTFOLIO'
  exit_reason         = 'STOP' | 'ROTATE'
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def run_portfolio_persisted(run: dict, pool) -> None:
    from .portfolio_engine import run_portfolio

    run_id = run["id"]
    sl_pct = float(run.get("pos_sl_pct") or 0)
    top_n = int(run.get("pos_top_n") or 20)

    trades: list[dict] = []
    m = await run_portfolio(
        pool,
        _audit_trade=trades.append,
        start=run["start_date"], end=run["end_date"],
        capital=float(run["capital"]),
        slippage=float(run["slippage_pct"]),
        momentum=run.get("pos_momentum") or "pct_chg_6m",
        rebalance_days=int(run.get("pos_rebalance_days") or 63),
        top_n=top_n,
        buffer_n=int(run.get("pos_buffer_n") or top_n * 2),
        min_turnover=float(run.get("pos_min_turnover_cr") or 5.0),
        sl_pct=sl_pct,
        vol_mode=run.get("pf_vol_mode") or "none",
        vol_levels=_levels(run.get("pf_vol_floor")),
        max_per_stock_pct=float(run.get("pf_max_per_stock_pct") or 100),
        max_per_sector_pct=float(run.get("pf_max_per_sector_pct") or 100),
        max_stocks_per_sector=int(run.get("pf_max_stocks_per_sector") or 99),
        require_sector=bool(run.get("pf_require_sector")),
        dd_throttle_at=float(run.get("pf_dd_throttle_at") or 0),
    )
    if not m:
        await pool.execute("UPDATE backtest_runs SET status='COMPLETED', "
                           "completed_at=NOW() WHERE id=$1", run_id)
        return

    def sl_of(entry: float) -> float:
        """structural_sl is NOT NULL on backtest_trades, so a stopless run
        records 0 — read as 'no invalidation level'. What must NOT happen is
        inventing a small non-zero risk to keep r_multiple computable: an
        earlier version used 0.01, which made r_multiple = pnl/(0.01*qty),
        numbers in the millions that overflowed NUMERIC(8,3) and failed the run.
        risk_per_share and r_multiple are nullable, so they carry the NULL that
        structural_sl cannot, and the R columns stay empty rather than wrong."""
        return round(entry * (1 - sl_pct / 100), 2) if sl_pct > 0 else 0.0

    def risk_of(entry: float):
        return max(entry - sl_of(entry), 0.01) if sl_pct > 0 else None

    for t in trades:
        entry, qty = t["entry_px"], t["qty"]
        stop, risk = sl_of(entry), risk_of(entry)
        pnl = t["proceeds"] - t["cost"]
        # Frictionless gross: raw open to raw exit, no slippage and no charges.
        # This is what the summary subtracts net from to report cost drag;
        # leaving it at 0 makes the UI show a negative cost equal to the whole
        # realized P&L, which is the bug that shipped in the positional engine.
        gross = (t["exit_px"] / (1 - float(run["slippage_pct"]) / 100)
                 - t["raw_open"]) * qty
        await pool.execute(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, signal_date, entry_trigger_price, structural_sl,
               risk_per_share, quantity, entry_type, status, entry_fill_date,
               entry_fill_price, exit_date, exit_price, exit_reason,
               realized_pnl, gross_pnl, r_multiple, holding_days, quant_rank)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'PORTFOLIO','CLOSED',$8,$9,$10,$11,$12,
                    $13,$14,$15,$16,1)
            """,
            run_id, t["sym"], t["entry"], round(t["raw_open"], 2),
            stop, round(risk, 2) if risk is not None else None, qty,
            t["entry"], round(entry, 2),
            t["exit"], round(t["exit_px"], 2), t["reason"],
            round(pnl, 2), round(gross, 2),
            round(pnl / (risk * qty), 3) if risk and qty else None,
            (t["exit"] - t["entry"]).days)

    for o in m.pop("_open", []):
        await pool.execute(
            """
            INSERT INTO backtest_trades
              (run_id, symbol, signal_date, entry_trigger_price, structural_sl,
               risk_per_share, quantity, entry_type, status, entry_fill_date,
               entry_fill_price, realized_pnl, gross_pnl, quant_rank)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'PORTFOLIO','OPEN',$8,$9,0,0,1)
            """,
            run_id, o["sym"], o["date"], round(o["raw_open"], 2),
            sl_of(o["entry"]),
            round(risk_of(o["entry"]), 2) if risk_of(o["entry"]) is not None else None,
            o["qty"], o["date"], round(o["entry"], 2))

    curve = m.pop("_curve", [])
    await pool.execute(
        """
        UPDATE backtest_runs SET status='COMPLETED', completed_at=NOW(),
          pf_cagr_pct=$2, pf_max_dd_pct=$3, pf_ulcer=$4, pf_worst_12m_pct=$5,
          pf_martin=$6, pf_turnover_per_yr=$7, pf_avg_exposure=$8,
          pf_final_equity=$9, pf_calendar=$10, pf_equity_curve=$11,
          progress_day=1, progress_total_days=1
        WHERE id=$1
        """,
        run["id"], m["cagrPct"], m["maxDDPct"], m["ulcer"], m["worst12mPct"],
        m["martin"], m["turnoverPerYr"], m["avgExposure"], m["final"],
        json.dumps(m["calendar"]), json.dumps(curve))
    logger.info("portfolio run %s: CAGR %.2f%% maxDD %.1f%% martin %s",
                run_id, m["cagrPct"], m["maxDDPct"], m["martin"])


def _levels(floor) -> tuple:
    """Vol ladder from a single 'floor' knob, so the UI exposes one number
    instead of four. The intermediate steps are interpolated between 100% and
    the floor - the earlier hand-picked ladders were four free parameters and
    only the mildest of them ever helped."""
    if floor is None:
        return (1.0, 0.75, 0.50, 0.25)
    f = float(floor) / 100 if float(floor) > 1 else float(floor)
    return (1.0, 1 - (1 - f) / 3, 1 - 2 * (1 - f) / 3, f)
