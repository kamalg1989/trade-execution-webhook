# ==============================================
# 📊 P/L DASHBOARD — FLASK BACKEND API
# Serves P/L data from trades.db to React frontend
# ==============================================

from flask import Flask, jsonify, request
from flask_cors import CORS
import sqlite3
import json
from datetime import datetime, timedelta, timezone
import os

# ==========================
# CONFIG
# ==========================
# CORRECTED: Points to the actual database location
DB_FILE = "/root/trade-execution-webhook/trades.db"
FLASK_ENV = os.getenv("FLASK_ENV", "production")

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend


# ==========================
# DATABASE QUERIES
# ==========================
def get_db_connection():
    """Get database connection."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


# ==========================
# SUMMARY ENDPOINTS
# ==========================
@app.route("/api/summary", methods=["GET"])
def get_summary():
    """Get overall P&L summary."""
    try:
        conn = get_db_connection()

        # Pending orders summary
        pending = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
            FROM pending_orders
        """).fetchone()

        # Executed orders summary
        executed = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN status='FILLED' THEN 1 ELSE 0 END) as filled_count,
                SUM(CASE WHEN status LIKE 'CLOSED_%' THEN 1 ELSE 0 END) as closed_count,
                SUM(pnl) as total_pnl,
                AVG(pnl_percent) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winners,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losers
            FROM executed_orders
        """).fetchone()

        conn.close()

        return jsonify({
            "pending": {
                "total": pending["total"] or 0,
                "pending": pending["pending"] or 0,
                "failed": pending["failed"] or 0,
            },
            "executed": {
                "total": executed["total"] or 0,
                "open": executed["open_count"] or 0,
                "filled": executed["filled_count"] or 0,
                "closed": executed["closed_count"] or 0,
                "total_pnl": round(executed["total_pnl"] or 0, 2),
                "avg_pnl_pct": round(executed["avg_pnl_pct"] or 0, 2),
                "win_count": executed["winners"] or 0,
                "loss_count": executed["losers"] or 0,
                "win_rate_pct": round((executed["winners"] or 0) / ((executed["winners"] or 0) + (executed["losers"] or 0)) * 100, 2) if (executed["winners"] or 0) + (executed["losers"] or 0) > 0 else 0,
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# OPEN POSITIONS ENDPOINT
# ==========================
@app.route("/api/positions/open", methods=["GET"])
def get_open_positions():
    """Get all open positions with unrealized P&L."""
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                symbol, dhan_order_id, qty_executed, entry_price_executed,
                current_price, sl_price, target_price, pnl, pnl_percent,
                placed_at, current_price_update_at, base_stage
            FROM executed_orders
            WHERE status = 'FILLED'
            ORDER BY placed_at DESC
        """).fetchall()

        conn.close()

        positions = [dict(row) for row in rows]
        return jsonify(positions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# CLOSED POSITIONS ENDPOINT
# ==========================
@app.route("/api/positions/closed", methods=["GET"])
def get_closed_positions():
    """Get closed positions with realized P&L."""
    try:
        limit = request.args.get("limit", 50, type=int)

        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                symbol, qty_executed, entry_price_executed, current_price,
                pnl, pnl_percent, status, current_price_update_at,
                base_stage, score
            FROM executed_orders
            WHERE status LIKE 'CLOSED_%'
            ORDER BY current_price_update_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        conn.close()

        positions = [dict(row) for row in rows]
        return jsonify(positions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# DAILY P/L ENDPOINT
# ==========================
@app.route("/api/analytics/daily", methods=["GET"])
def get_daily_pnl():
    """Get daily P&L aggregation."""
    try:
        days = request.args.get("days", 30, type=int)

        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                DATE(executed_at) as trade_date,
                COUNT(*) as trade_count,
                SUM(pnl) as daily_pnl,
                AVG(pnl_percent) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM executed_orders
            WHERE executed_at IS NOT NULL
              AND DATE(executed_at) >= DATE('now', '-' || ? || ' days')
            GROUP BY trade_date
            ORDER BY trade_date DESC
        """, (days,)).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# SYMBOL PERFORMANCE ENDPOINT
# ==========================
@app.route("/api/analytics/symbols", methods=["GET"])
def get_symbol_performance():
    """Get P&L by symbol."""
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                symbol,
                COUNT(*) as trades,
                SUM(pnl) as total_pnl,
                AVG(pnl_percent) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                MAX(pnl) as best_trade,
                MIN(pnl) as worst_trade
            FROM executed_orders
            WHERE status LIKE 'CLOSED_%'
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# BASE STAGE PERFORMANCE ENDPOINT
# ==========================
@app.route("/api/analytics/base-stage", methods=["GET"])
def get_base_stage_performance():
    """Get P&L by base stage."""
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                base_stage,
                COUNT(*) as trades,
                SUM(pnl) as total_pnl,
                AVG(pnl_percent) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM executed_orders
            WHERE status LIKE 'CLOSED_%'
              AND base_stage > 0
            GROUP BY base_stage
            ORDER BY base_stage
        """).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# RISK METRICS ENDPOINT
# ==========================
@app.route("/api/analytics/risk", methods=["GET"])
def get_risk_metrics():
    """Get overall risk/reward metrics."""
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                SUM(qty_executed * (entry_price_executed - sl_price)) as total_risk,
                AVG(qty_executed * (entry_price_executed - sl_price)) as avg_risk,
                SUM(CASE WHEN pnl > 0 THEN ABS(pnl) ELSE 0 END) as sum_wins,
                SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as sum_losses,
                AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss
            FROM executed_orders
            WHERE status LIKE 'CLOSED_%'
        """).fetchone()

        conn.close()

        total_risk = rows["total_risk"] or 0
        sum_wins = rows["sum_wins"] or 0
        sum_losses = rows["sum_losses"] or 0

        profit_factor = (sum_wins / sum_losses) if sum_losses != 0 else (1 if sum_wins > 0 else 0)

        return jsonify({
            "total_risk_inr": round(total_risk, 2),
            "avg_risk_inr": round(rows["avg_risk"] or 0, 2),
            "total_wins_inr": round(sum_wins, 2),
            "total_losses_inr": round(sum_losses, 2),
            "avg_win_inr": round(rows["avg_win"] or 0, 2),
            "avg_loss_inr": round(rows["avg_loss"] or 0, 2),
            "profit_factor": round(profit_factor, 2),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# STUCK ORDERS ENDPOINT
# ==========================
@app.route("/api/orders/stuck", methods=["GET"])
def get_stuck_orders():
    """Get orders stuck in pending_orders (for manual review)."""
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                setup_id, symbol, qty, entry_price, sl_price, target_price,
                status, attempt_count, last_error, retry_at, placed_at
            FROM pending_orders
            WHERE status IN ('PENDING', 'FAILED')
            ORDER BY placed_at DESC
        """).fetchall()

        conn.close()

        orders = [dict(row) for row in rows]
        return jsonify(orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# STATS FOR CARDS ENDPOINT
# ==========================
@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Get quick stats for dashboard cards."""
    try:
        conn = get_db_connection()

        # Current P&L
        current_pnl = conn.execute("""
            SELECT SUM(pnl) as total FROM executed_orders WHERE status='FILLED'
        """).fetchone()

        # Today's trades
        today_trades = conn.execute("""
            SELECT COUNT(*) as count FROM executed_orders 
            WHERE DATE(executed_at) = DATE('now')
        """).fetchone()

        # This month P&L
        month_pnl = conn.execute("""
            SELECT SUM(pnl) as total FROM executed_orders 
            WHERE strftime('%Y-%m', executed_at) = strftime('%Y-%m', 'now')
        """).fetchone()

        # Best trade
        best_trade = conn.execute("""
            SELECT MAX(pnl) as best FROM executed_orders WHERE status LIKE 'CLOSED_%'
        """).fetchone()

        conn.close()

        return jsonify({
            "unrealized_pnl": round(current_pnl["total"] or 0, 2),
            "today_trades": today_trades["count"] or 0,
            "month_pnl": round(month_pnl["total"] or 0, 2),
            "best_trade": round(best_trade["best"] or 0, 2),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================
# ERROR HANDLER
# ==========================
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({"error": "Internal server error"}), 500


# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    print(f"📊 P/L Dashboard API Starting")
    print(f"Using database: {DB_FILE}")
    print(f"Environment: {FLASK_ENV}")

    app.run(
        host="0.0.0.0",
        port=5001,
        debug=(FLASK_ENV == "development"),
    )