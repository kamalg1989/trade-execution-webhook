# ==============================================
# 📊 P/L DASHBOARD — FLASK BACKEND API
# Serves P&L data from actual trades.db schema
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
DB_FILE = "/root/trade-execution-webhook/trades.db"
FLASK_ENV = os.getenv("FLASK_ENV", "production")

app = Flask(__name__)
CORS(app)


# ==========================
# DATABASE CONNECTION
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
    """Get overall summary from trades and trade_setups."""
    logger.debug("📍 /api/summary called")
    try:
        conn = get_db_connection()

        # Pending setups (not yet executed)
        pending = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) as failed
            FROM trade_setups
        """).fetchone()
        logger.debug(f"✅ Pending setups: {dict(pending)}")

        # Executed trades (from trades table)
        executed = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) as closed_count,
                COALESCE(SUM(CASE WHEN pnl IS NOT NULL THEN 1 ELSE 0 END), 0) as pnl_trades,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 0) as avg_pnl
            FROM trades
        """).fetchone()
        logger.debug(f"✅ Executed trades: {dict(executed)}")

        conn.close()

        response = {
            "pending": {
                "total": pending["total"] or 0,
                "pending": pending["pending"] or 0,
                "failed": pending["failed"] or 0,
            },
            "executed": {
                "total": executed["total"] or 0,
                "open": executed["open_count"] or 0,
                "closed": executed["closed_count"] or 0,
                "total_pnl": round(executed["total_pnl"] or 0, 2),
                "avg_pnl_pct": round(executed["avg_pnl"] or 0, 2),
                "win_count": 0,
                "loss_count": 0,
                "win_rate_pct": 0.0,
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
    """Get all open trades."""
    logger.debug("📍 /api/positions/open called")
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                id, symbol, security_id, qty, entry_price, entry_time,
                status, setup_id, pnl
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY entry_time DESC
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
    """Get closed trades."""
    logger.debug("📍 /api/positions/closed called")
    try:
        limit = request.args.get("limit", 50, type=int)
        logger.debug(f"Limit: {limit}")

        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                id, symbol, security_id, qty, entry_price, entry_time,
                status, setup_id, pnl
            FROM trades
            WHERE status = 'CLOSED'
            ORDER BY entry_time DESC
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
# DAILY P/L ENDPOINT
# ==========================
@app.route("/api/analytics/daily", methods=["GET"])
def get_daily_pnl():
    """Get daily P&L aggregation from trades."""
    logger.debug("📍 /api/analytics/daily called")
    try:
        days = request.args.get("days", 30, type=int)
        logger.debug(f"Days: {days}")

        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                DATE(entry_time) as trade_date,
                COUNT(*) as trade_count,
                COALESCE(SUM(pnl), 0) as daily_pnl,
                COALESCE(AVG(pnl), 0) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades
            WHERE entry_time IS NOT NULL
            GROUP BY DATE(entry_time)
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
                COALESCE(AVG(pnl), 0) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                MAX(pnl) as best_trade,
                MIN(pnl) as worst_trade
            FROM trades
            WHERE status = 'CLOSED'
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
# STRATEGY PERFORMANCE ENDPOINT
# ==========================
@app.route("/api/analytics/strategy", methods=["GET"])
def get_strategy_performance():
    """Get P&L by strategy."""
    logger.debug("📍 /api/analytics/strategy called")
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                strategy,
                COUNT(*) as trades,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl), 0) as avg_pnl_pct,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trade_setups
            WHERE status = 'EXECUTED'
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        logger.debug(f"✅ Strategy performance: {len(data)} strategies found")
        return jsonify(data)
    except Exception as e:
        logger.error(f"❌ /api/analytics/strategy error: {e}")
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

        # From trade_setups
        rows = conn.execute("""
            SELECT 
                COALESCE(SUM(risk), 0) as total_risk,
                COALESCE(AVG(risk), 0) as avg_risk,
                COALESCE(SUM(reward), 0) as total_reward,
                COALESCE(AVG(rr_ratio), 0) as avg_rr_ratio
            FROM trade_setups
            WHERE status = 'EXECUTED'
        """).fetchone()

        conn.close()

        response = {
            "total_risk_inr": round(rows["total_risk"] or 0, 2),
            "avg_risk_inr": round(rows["avg_risk"] or 0, 2),
            "total_reward_inr": round(rows["total_reward"] or 0, 2),
            "avg_rr_ratio": round(rows["avg_rr_ratio"] or 0, 2),
        }
        logger.debug(f"✅ Risk metrics: {response}")
        return jsonify(response)
    except Exception as e:
        logger.error(f"❌ /api/analytics/risk error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ==========================
# PENDING SETUPS ENDPOINT
# ==========================
@app.route("/api/setups/pending", methods=["GET"])
def get_pending_setups():
    """Get pending trade setups."""
    logger.debug("📍 /api/setups/pending called")
    try:
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT 
                setup_id, symbol, qty, entry, sl, target, strategy,
                score, status, created_at
            FROM trade_setups
            WHERE status IN ('PENDING', 'REJECTED')
            ORDER BY setup_id DESC
        """).fetchall()

        conn.close()

        setups = [dict(row) for row in rows]
        logger.debug(f"✅ Pending setups: {len(setups)} found")
        return jsonify(setups)
    except Exception as e:
        logger.error(f"❌ /api/setups/pending error: {e}")
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

        # Current unrealized P&L (open trades)
        try:
            current_pnl = conn.execute("""
                SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE status='OPEN'
            """).fetchone()
            logger.debug(f"✅ Current P&L: {current_pnl['total']}")
        except Exception as e:
            logger.error(f"❌ Current P&L query failed: {e}")
            current_pnl = {"total": 0}

        # Today's trades
        try:
            today_trades = conn.execute("""
                SELECT COUNT(*) as count FROM trades 
                WHERE DATE(entry_time) = DATE('now')
            """).fetchone()
            logger.debug(f"✅ Today's trades: {today_trades['count']}")
        except Exception as e:
            logger.error(f"❌ Today's trades query failed: {e}")
            today_trades = {"count": 0}

        # This month P&L
        try:
            month_pnl = conn.execute("""
                SELECT COALESCE(SUM(pnl), 0) as total FROM trades 
                WHERE strftime('%Y-%m', entry_time) = strftime('%Y-%m', 'now')
            """).fetchone()
            logger.debug(f"✅ Month P&L: {month_pnl['total']}")
        except Exception as e:
            logger.error(f"❌ Month P&L query failed: {e}")
            month_pnl = {"total": 0}

        # Best trade
        try:
            best_trade = conn.execute("""
                SELECT COALESCE(MAX(pnl), 0) as best FROM trades WHERE status = 'CLOSED'
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
# ERROR HANDLERS
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
    print("\nDatabase schema:")
    print("  - trades (open/closed positions)")
    print("  - orders (stop loss orders)")
    print("  - trade_setups (pending/executed setups)")
    print("=" * 70)

    app.run(
        host="0.0.0.0",
        port=5001,
        debug=(FLASK_ENV == "development"),
    )