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
import traceback
import logging

# ==========================
# LOGGING SETUP
# ==========================
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

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
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        logger.info(f"✅ Connected to database: {DB_FILE}")
        return conn
    except Exception as e:
        logger.error(f"❌ Database connection error: {e}")
        logger.error(f"Database file exists: {os.path.exists(DB_FILE)}")
        raise


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    try:
        logger.debug("📍 /api/health called")
        return jsonify({
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": DB_FILE,
            "db_exists": os.path.exists(DB_FILE)
        })
    except Exception as e:
        logger.error(f"❌ /api/health error: {e}")
        return jsonify({"error": str(e)}), 500


# ==========================
# SUMMARY ENDPOINTS
# ==========================
@app.route("/api/summary", methods=["GET"])
def get_summary():
    """Get overall P&L summary."""
    logger.debug("📍 /api/summary called")
    try:
        conn = get_db_connection()

        # Check if tables exist
        tables = conn.execute("""
            SELECT name FROM sqlite_master WHERE type='table'
        """).fetchall()
        logger.debug(f"📋 Tables in database: {[t[0] for t in tables]}")

        # Pending orders summary
        try:
            pending = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
                FROM pending_orders
            """).fetchone()
            logger.debug(f"✅ Pending orders query succeeded: {dict(pending)}")
        except Exception as e:
            logger.error(f"❌ Pending orders query failed: {e}")
            pending = {"total": 0, "pending": 0, "failed": 0}

        # Executed orders summary
        try:
            executed = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_count,
                    SUM(CASE WHEN status='FILLED' THEN 1 ELSE 0 END) as filled_count,
                    SUM(CASE WHEN status LIKE 'CLOSED_%' THEN 1 ELSE 0 END) as closed_count,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(AVG(pnl_percent), 0) as avg_pnl_pct,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losers
                FROM executed_orders
            """).fetchone()
            logger.debug(f"✅ Executed orders query succeeded: {dict(executed)}")
        except Exception as e:
            logger.error(f"❌ Executed orders query failed: {e}")
            executed = {
                "total": 0, "open_count": 0, "filled_count": 0, "closed_count": 0,
                "total_pnl": 0, "avg_pnl_pct": 0, "winners": 0, "losers": 0
            }

        conn.close()

        winners = executed["winners"] or 0
        losers = executed["losers"] or 0
        total_trades = winners + losers

        win_rate = round((winners / total_trades * 100), 2) if total_trades > 0 else 0

        response = {
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
                "win_count": winners,
                "loss_count": losers,
                "win_rate_pct": win_rate,
            }
        }
        logger.debug(f"✅ /api/summary returning: {response}")
        return jsonify(response)
    except Exception as e:
        logger.error(f"❌ /api/summary error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# OPEN POSITIONS ENDPOINT
# ==========================
@app.route("/api/positions/open", methods=["GET"])
def get_open_positions():
    """Get all open positions with unrealized P&L."""
    logger.debug("📍 /api/positions/open called")
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
        logger.debug(f"✅ Open positions: {len(positions)} found")
        return jsonify(positions)
    except Exception as e:
        logger.error(f"❌ /api/positions/open error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# CLOSED POSITIONS ENDPOINT
# ==========================
@app.route("/api/positions/closed", methods=["GET"])
def get_closed_positions():
    """Get closed positions with realized P&L."""
    logger.debug("📍 /api/positions/closed called")
    try:
        limit = request.args.get("limit", 50, type=int)
        logger.debug(f"Limit: {limit}")

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
        logger.debug(f"✅ Closed positions: {len(positions)} found")
        return jsonify(positions)
    except Exception as e:
        logger.error(f"❌ /api/positions/closed error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# DAILY P&L ENDPOINT
# ==========================
@app.route("/api/analytics/daily", methods=["GET"])
def get_daily_pnl():
    """Get daily P&L aggregation."""
    logger.debug("📍 /api/analytics/daily called")
    try:
        days = request.args.get("days", 30, type=int)
        logger.debug(f"Days: {days}")

        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                DATE(executed_at) as trade_date,
                COUNT(*) as trade_count,
                COALESCE(SUM(pnl), 0) as daily_pnl,
                COALESCE(AVG(pnl_percent), 0) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM executed_orders
            WHERE executed_at IS NOT NULL
            GROUP BY DATE(executed_at)
            ORDER BY trade_date DESC
            LIMIT ?
        """, (days,)).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        logger.debug(f"✅ Daily P&L: {len(data)} days found")
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ /api/analytics/daily error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# SYMBOL PERFORMANCE ENDPOINT
# ==========================
@app.route("/api/analytics/symbols", methods=["GET"])
def get_symbol_performance():
    """Get P&L by symbol."""
    logger.debug("📍 /api/analytics/symbols called")
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                symbol,
                COUNT(*) as trades,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl_percent), 0) as avg_pnl_pct,
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
        logger.debug(f"✅ Symbol performance: {len(data)} symbols found")
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ /api/analytics/symbols error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# BASE STAGE PERFORMANCE ENDPOINT
# ==========================
@app.route("/api/analytics/base-stage", methods=["GET"])
def get_base_stage_performance():
    """Get P&L by base stage."""
    logger.debug("📍 /api/analytics/base-stage called")
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                base_stage,
                COUNT(*) as trades,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl_percent), 0) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM executed_orders
            WHERE status LIKE 'CLOSED_%'
              AND base_stage > 0
            GROUP BY base_stage
            ORDER BY base_stage
        """).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        logger.debug(f"✅ Base stage performance: {len(data)} stages found")
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ /api/analytics/base-stage error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# RISK METRICS ENDPOINT
# ==========================
@app.route("/api/analytics/risk", methods=["GET"])
def get_risk_metrics():
    """Get overall risk/reward metrics."""
    logger.debug("📍 /api/analytics/risk called")
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                COALESCE(SUM(qty_executed * (entry_price_executed - sl_price)), 0) as total_risk,
                COALESCE(AVG(qty_executed * (entry_price_executed - sl_price)), 0) as avg_risk,
                SUM(CASE WHEN pnl > 0 THEN ABS(pnl) ELSE 0 END) as sum_wins,
                SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as sum_losses,
                COALESCE(AVG(CASE WHEN pnl > 0 THEN pnl END), 0) as avg_win,
                COALESCE(AVG(CASE WHEN pnl < 0 THEN pnl END), 0) as avg_loss
            FROM executed_orders
            WHERE status LIKE 'CLOSED_%'
        """).fetchone()

        conn.close()

        total_risk = rows["total_risk"] or 0
        sum_wins = rows["sum_wins"] or 0
        sum_losses = rows["sum_losses"] or 0

        profit_factor = (sum_wins / sum_losses) if sum_losses != 0 else (1 if sum_wins > 0 else 0)

        response = {
            "total_risk_inr": round(total_risk, 2),
            "avg_risk_inr": round(rows["avg_risk"] or 0, 2),
            "total_wins_inr": round(sum_wins, 2),
            "total_losses_inr": round(sum_losses, 2),
            "avg_win_inr": round(rows["avg_win"] or 0, 2),
            "avg_loss_inr": round(rows["avg_loss"] or 0, 2),
            "profit_factor": round(profit_factor, 2),
        }
        logger.debug(f"✅ Risk metrics: {response}")
        return jsonify(response)
    except Exception as e:
        logger.error(f"❌ /api/analytics/risk error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# STUCK ORDERS ENDPOINT
# ==========================
@app.route("/api/orders/stuck", methods=["GET"])
def get_stuck_orders():
    """Get orders stuck in pending_orders (for manual review)."""
    logger.debug("📍 /api/orders/stuck called")
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
        logger.debug(f"✅ Stuck orders: {len(orders)} found")
        return jsonify(orders)
    except Exception as e:
        logger.error(f"❌ /api/orders/stuck error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# STATS FOR CARDS ENDPOINT
# ==========================
@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Get quick stats for dashboard cards."""
    logger.debug("📍 /api/stats called")
    try:
        conn = get_db_connection()

        # Current P&L
        try:
            current_pnl = conn.execute("""
                SELECT COALESCE(SUM(pnl), 0) as total FROM executed_orders WHERE status='FILLED'
            """).fetchone()
            logger.debug(f"✅ Current P&L: {current_pnl['total']}")
        except Exception as e:
            logger.error(f"❌ Current P&L query failed: {e}")
            current_pnl = {"total": 0}

        # Today's trades
        try:
            today_trades = conn.execute("""
                SELECT COUNT(*) as count FROM executed_orders 
                WHERE DATE(executed_at) = DATE('now')
            """).fetchone()
            logger.debug(f"✅ Today's trades: {today_trades['count']}")
        except Exception as e:
            logger.error(f"❌ Today's trades query failed: {e}")
            today_trades = {"count": 0}

        # This month P&L
        try:
            month_pnl = conn.execute("""
                SELECT COALESCE(SUM(pnl), 0) as total FROM executed_orders 
                WHERE strftime('%Y-%m', executed_at) = strftime('%Y-%m', 'now')
            """).fetchone()
            logger.debug(f"✅ Month P&L: {month_pnl['total']}")
        except Exception as e:
            logger.error(f"❌ Month P&L query failed: {e}")
            month_pnl = {"total": 0}

        # Best trade
        try:
            best_trade = conn.execute("""
                SELECT COALESCE(MAX(pnl), 0) as best FROM executed_orders WHERE status LIKE 'CLOSED_%'
            """).fetchone()
            logger.debug(f"✅ Best trade: {best_trade['best']}")
        except Exception as e:
            logger.error(f"❌ Best trade query failed: {e}")
            best_trade = {"best": 0}

        conn.close()

        response = {
            "unrealized_pnl": round(current_pnl["total"] or 0, 2),
            "today_trades": today_trades["count"] or 0,
            "month_pnl": round(month_pnl["total"] or 0, 2),
            "best_trade": round(best_trade["best"] or 0, 2),
        }
        logger.debug(f"✅ /api/stats returning: {response}")
        return jsonify(response)
    except Exception as e:
        logger.error(f"❌ /api/stats error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# ERROR HANDLER
# ==========================
@app.errorhandler(404)
def not_found(error):
    logger.error(f"❌ 404 Not Found: {error}")
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def server_error(error):
    logger.error(f"❌ 500 Server Error: {error}")
    return jsonify({"error": "Internal server error"}), 500


# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    print("=" * 70)
    print("📊 P/L DASHBOARD API STARTING")
    print("=" * 70)
    print(f"Using database: {DB_FILE}")
    print(f"Database exists: {os.path.exists(DB_FILE)}")
    print(f"Environment: {FLASK_ENV}")
    print("=" * 70)

    app.run(
        host="0.0.0.0",
        port=5001,
        debug=(FLASK_ENV == "development"),
    )