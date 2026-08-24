"""
Portfolio Router — real Dhan holdings enriched with trade metadata,
plus real closed-trade history + insights from the `trades` DB table.
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
import logging
import os

import psycopg2
import psycopg2.extras

import dhan_client

router = APIRouter()
logger = logging.getLogger(__name__)

DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/trading_platform"
)


def _conn():
    return psycopg2.connect(DB_DSN, connect_timeout=5)


CLOSED_TRADE_COLS = """
    id, security_id, symbol, quantity, actual_buy_price, buy_trigger_price,
    sell_price, realized_pnl, r_multiple_realized, holding_period_days,
    closed_via, exit_reason, reason, entry_type, regime, base_stage,
    ai_reviewed, ai_rank, ai_confidence, ai_recommendation,
    buy_filled_at, placed_at, sell_date
"""

OPEN_TRADE_COLS = """
    security_id, reason, entry_type, regime, base_stage,
    ai_reviewed, ai_rank, ai_confidence, ai_recommendation,
    structural_sl, target_price, buy_filled_at, placed_at, status
"""


def _fetch_closed_trades():
    """Real closed trades from the trades table (status='CLOSED')."""
    try:
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT {CLOSED_TRADE_COLS}
                FROM trades
                WHERE status = 'CLOSED'
                ORDER BY COALESCE(sell_date, placed_at) DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"_fetch_closed_trades failed: {e}")
        return []


def _fetch_open_trade_meta():
    """Latest trade-metadata row per security_id for still-open trades,
    keyed by security_id (string) for enriching Dhan holdings."""
    try:
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT DISTINCT ON (security_id) {OPEN_TRADE_COLS}
                FROM trades
                WHERE status IN ('OPEN', 'EXIT_PENDING', 'HALF_BOOKED')
                ORDER BY security_id, placed_at DESC
            """)
            return {str(r["security_id"]): dict(r) for r in cur.fetchall()}
    except Exception as e:
        logger.warning(f"_fetch_open_trade_meta failed: {e}")
        return {}


def _closed_trade_to_json(t):
    qty = int(t.get("quantity") or 0)
    entry_price = float(t.get("actual_buy_price") or t.get("buy_trigger_price") or 0)
    exit_price = float(t.get("sell_price") or 0)
    pnl = float(t.get("realized_pnl")) if t.get("realized_pnl") is not None else round((exit_price - entry_price) * qty, 2)
    invested = entry_price * qty
    return_pct = round((pnl / invested) * 100, 2) if invested else 0.0
    entry_date = t.get("buy_filled_at") or t.get("placed_at")
    return {
        "id": t.get("id"),
        "symbol": t.get("symbol", ""),
        "securityId": str(t.get("security_id") or ""),
        "quantity": qty,
        "entryPrice": round(entry_price, 2),
        "exitPrice": round(exit_price, 2),
        "pnl": round(pnl, 2),
        "returnPercent": return_pct,
        "rMultiple": float(t["r_multiple_realized"]) if t.get("r_multiple_realized") is not None else None,
        "holdingDays": t.get("holding_period_days"),
        "closedVia": t.get("closed_via"),
        "exitReason": t.get("exit_reason"),
        "reason": t.get("reason"),
        "entryType": t.get("entry_type"),
        "regime": t.get("regime"),
        "baseStage": t.get("base_stage"),
        "aiReviewed": bool(t.get("ai_reviewed")),
        "aiRank": t.get("ai_rank"),
        "aiConfidence": float(t["ai_confidence"]) if t.get("ai_confidence") is not None else None,
        "aiRecommendation": t.get("ai_recommendation"),
        "entryDate": entry_date.isoformat() if entry_date else None,
        "exitDate": t["sell_date"].isoformat() if t.get("sell_date") else None,
    }


def _compute_insights(closed_json):
    total = len(closed_json)
    if total == 0:
        return {
            "totalClosed": 0,
            "winRate": 0.0,
            "totalRealizedPnL": 0.0,
            "avgRMultiple": None,
            "avgHoldingDays": None,
            "quantOnly": {"count": 0, "winRate": 0.0, "avgPnL": 0.0},
            "aiReviewed": {"count": 0, "winRate": 0.0, "avgPnL": 0.0},
        }

    wins = [t for t in closed_json if t["pnl"] > 0]
    win_rate = round(len(wins) / total * 100, 1)
    total_pnl = round(sum(t["pnl"] for t in closed_json), 2)
    r_vals = [t["rMultiple"] for t in closed_json if t["rMultiple"] is not None]
    avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else None
    hold_vals = [t["holdingDays"] for t in closed_json if t["holdingDays"] is not None]
    avg_hold = round(sum(hold_vals) / len(hold_vals), 1) if hold_vals else None

    def _bucket(trades):
        n = len(trades)
        if n == 0:
            return {"count": 0, "winRate": 0.0, "avgPnL": 0.0}
        w = len([t for t in trades if t["pnl"] > 0])
        return {
            "count": n,
            "winRate": round(w / n * 100, 1),
            "avgPnL": round(sum(t["pnl"] for t in trades) / n, 2),
        }

    quant_only = [t for t in closed_json if not t["aiReviewed"]]
    ai_reviewed = [t for t in closed_json if t["aiReviewed"]]

    return {
        "totalClosed": total,
        "winRate": win_rate,
        "totalRealizedPnL": total_pnl,
        "avgRMultiple": avg_r,
        "avgHoldingDays": avg_hold,
        "quantOnly": _bucket(quant_only),
        "aiReviewed": _bucket(ai_reviewed),
    }


def _quality_grade(t):
    """A+/A/B/C from AI confidence when the trade was AI-reviewed; quant-only
    trades (no AI confidence) get their own 'Quant' bucket rather than a
    fabricated grade."""
    if not t.get("aiReviewed") or t.get("aiConfidence") is None:
        return "Quant"
    c = t["aiConfidence"]
    if c >= 0.85:
        return "A+"
    if c >= 0.7:
        return "A"
    if c >= 0.5:
        return "B"
    return "C"


def _compute_journal_stats(closed_json):
    """Journal/equity-curve analytics computed purely from closed trades,
    ordered chronologically by exit date. Trades with no exit date (shouldn't
    happen for CLOSED status, but be defensive) are excluded from the
    time-based series."""
    dated = [t for t in closed_json if t.get("exitDate")]
    dated.sort(key=lambda t: t["exitDate"])

    if not dated:
        return {
            "equityCurve": [], "totalPnl": 0.0, "peakEquity": 0.0,
            "maxDrawdown": 0.0, "maxDrawdownPct": 0.0,
            "tradingDays": 0, "avgTradesPerDay": 0.0,
            "dailyWinRate": {"pct": 0.0, "wins": 0, "losses": 0},
            "consistency": {"pct": None, "label": "Not enough data"},
            "bestDay": None, "worstDay": None,
            "bestWinStreak": 0, "worstLossStreak": 0,
            "byQualityGrade": [], "byRegime": [],
        }

    # Group by calendar day (exit date) for the equity curve + daily stats.
    by_day = {}
    for t in dated:
        day = t["exitDate"][:10]
        by_day.setdefault(day, []).append(t)

    days = sorted(by_day.keys())
    equity_curve = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_pct = 0.0
    for day in days:
        day_pnl = round(sum(t["pnl"] for t in by_day[day]), 2)
        cum = round(cum + day_pnl, 2)
        peak = max(peak, cum)
        dd = round(peak - cum, 2)
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = round((dd / peak) * 100, 2) if peak > 0 else 0.0
        equity_curve.append({"date": day, "dailyPnl": day_pnl, "cumulativePnl": cum})

    total_pnl = round(sum(t["pnl"] for t in dated), 2)
    win_days = [d for d in equity_curve if d["dailyPnl"] > 0]
    loss_days = [d for d in equity_curve if d["dailyPnl"] < 0]
    daily_win_rate = round(len(win_days) / len(days) * 100, 1) if days else 0.0

    best_day = max(equity_curve, key=lambda d: d["dailyPnl"]) if equity_curve else None
    worst_day = min(equity_curve, key=lambda d: d["dailyPnl"]) if equity_curve else None

    # Consistency: how much of total profit came from a single best day —
    # the same idea prop firms use for "consistency rule" checks. 100 = profit
    # spread evenly across days, low score = one outsized day carried the book.
    if total_pnl > 0 and best_day and best_day["dailyPnl"] > 0:
        consistency_pct = round(max(0.0, 100 - (best_day["dailyPnl"] / total_pnl * 100)), 1)
    else:
        consistency_pct = None
    if consistency_pct is None:
        consistency_label = "Not enough data"
    elif consistency_pct >= 80:
        consistency_label = "Excellent"
    elif consistency_pct >= 60:
        consistency_label = "Good"
    elif consistency_pct >= 40:
        consistency_label = "Fair"
    else:
        consistency_label = "Poor"

    # Win/loss streaks — per trade, chronological, not per day.
    best_win_streak = worst_loss_streak = cur_win = cur_loss = 0
    for t in dated:
        if t["pnl"] > 0:
            cur_win += 1
            cur_loss = 0
        elif t["pnl"] < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        best_win_streak = max(best_win_streak, cur_win)
        worst_loss_streak = max(worst_loss_streak, cur_loss)

    def _bucket_by(keyfn, label_none="Untagged"):
        buckets = {}
        for t in dated:
            key = keyfn(t) or label_none
            buckets.setdefault(key, []).append(t)
        out = []
        for key, trades in buckets.items():
            n = len(trades)
            wins = len([t for t in trades if t["pnl"] > 0])
            out.append({
                "label": key, "count": n,
                "pnl": round(sum(t["pnl"] for t in trades), 2),
                "winRate": round(wins / n * 100, 1) if n else 0.0,
            })
        return out

    by_quality = _bucket_by(_quality_grade)
    by_regime = _bucket_by(lambda t: t.get("regime"))

    return {
        "equityCurve": equity_curve,
        "totalPnl": total_pnl,
        "peakEquity": peak,
        "maxDrawdown": max_dd,
        "maxDrawdownPct": max_dd_pct,
        "tradingDays": len(days),
        "avgTradesPerDay": round(len(dated) / len(days), 1) if days else 0.0,
        "dailyWinRate": {"pct": daily_win_rate, "wins": len(win_days), "losses": len(loss_days)},
        "consistency": {"pct": consistency_pct, "label": consistency_label},
        "bestDay": best_day, "worstDay": worst_day,
        "bestWinStreak": best_win_streak, "worstLossStreak": worst_loss_streak,
        "byQualityGrade": sorted(by_quality, key=lambda b: b["label"]),
        "byRegime": sorted(by_regime, key=lambda b: -b["pnl"]),
    }


def _holdings_to_positions(holdings, open_meta=None):
    open_meta = open_meta or {}
    positions = []
    for h in holdings:
        qty = int(h.get("totalQty") or h.get("availableQty") or 0)
        if qty <= 0:
            continue
        avg = float(h.get("avgCostPrice") or 0)
        ltp = float(h.get("lastTradedPrice") or 0) or avg
        invested = avg * qty
        value = ltp * qty
        pnl = value - invested
        sec_id = str(h.get("securityId", ""))
        meta = open_meta.get(sec_id, {})
        pos = {
            "symbol": h.get("tradingSymbol", ""),
            "securityId": sec_id,
            "quantity": qty,
            "avgCost": round(avg, 2),
            "currentPrice": round(ltp, 2),
            "totalValue": round(value, 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnlPercent": round((pnl / invested) * 100, 2) if invested else 0.0,
            "returnPercent": round((pnl / invested) * 100, 2) if invested else 0.0,
            "reason": meta.get("reason"),
            "entryType": meta.get("entry_type"),
            "regime": meta.get("regime"),
            "baseStage": meta.get("base_stage"),
            "aiReviewed": bool(meta.get("ai_reviewed")) if meta else None,
            "aiRank": meta.get("ai_rank"),
            "structuralSl": float(meta["structural_sl"]) if meta.get("structural_sl") is not None else None,
            "target": float(meta["target_price"]) if meta.get("target_price") is not None else None,
        }
        entry_date = meta.get("buy_filled_at") or meta.get("placed_at")
        pos["entryDate"] = entry_date.isoformat() if entry_date else None
        positions.append(pos)
    return positions


@router.get("/portfolio")
async def get_portfolio(timeframe: str = "1m"):
    """P&L summary from real Dhan holdings + realized P&L from closed trades"""
    try:
        holdings = dhan_client.get_holdings()
    except Exception as e:
        logger.error(f"Dhan holdings failed: {e}")
        raise HTTPException(status_code=502, detail=f"Dhan API error: {str(e)[:120]}")

    positions = _holdings_to_positions(holdings)
    total_invested = sum(p["avgCost"] * p["quantity"] for p in positions)
    total_value = sum(p["totalValue"] for p in positions)

    closed_json = [_closed_trade_to_json(t) for t in _fetch_closed_trades()]
    realized_pnl = round(sum(t["pnl"] for t in closed_json), 2)

    return {
        "totalInvested": round(total_invested, 2),
        "totalValue": round(total_value, 2),
        "unrealizedPnL": round(total_value - total_invested, 2),
        "realizedPnL": realized_pnl,
        "positions": positions,
        "performanceHistory": [],  # populated once snapshots accumulate
        "asOf": datetime.now().isoformat(),
    }


@router.get("/portfolio/full")
async def get_portfolio_full():
    """Holdings (enriched with trade metadata) + real closed trades + insights"""
    try:
        holdings = dhan_client.get_holdings()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dhan API error: {str(e)[:120]}")

    open_meta = _fetch_open_trade_meta()
    positions = _holdings_to_positions(holdings, open_meta)

    closed_trades = _fetch_closed_trades()
    closed_json = [_closed_trade_to_json(t) for t in closed_trades]
    insights = _compute_insights(closed_json)
    journal = _compute_journal_stats(closed_json)

    total_invested = sum(p["avgCost"] * p["quantity"] for p in positions)
    total_value = sum(p["totalValue"] for p in positions)

    return {
        "holdings": positions,
        "closedTrades": closed_json,
        "insights": insights,
        "journal": journal,
        "openSummary": {
            "totalInvested": round(total_invested, 2),
            "totalValue": round(total_value, 2),
            "unrealizedPnL": round(total_value - total_invested, 2),
            "unrealizedPnLPct": round((total_value - total_invested) / total_invested * 100, 2) if total_invested else 0.0,
            "count": len(positions),
        },
    }
