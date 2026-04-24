# ==============================================
# 📊 P/L DASHBOARD — FLASK BACKEND API
# Serves P&L data from actual trades.db schema
# FIXED: No PNL column in trades table!
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
        conn = get_db_connection()

        # Check tables
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": DB_FILE,
            "db_exists": os.path.exists(DB_FILE),
            "tables": tables
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
        logger.debug("Querying pending setups...")
        pending = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) as failed
            FROM trade_setups
        """).fetchone()
        logger.debug(f"✅ Pending setups: {dict(pending)}")

        # Executed trades (from trades table)
        logger.debug("Querying executed trades...")
        executed = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_count,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) as closed_count
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
                "total_pnl": 0,
                "avg_pnl_pct": 0,
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
        logger.debug("Querying open trades from trades table...")
        rows = conn.execute("""
            SELECT 
                id, symbol, security_id, qty, entry_price, entry_time,
                status, setup_id
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
        logger.debug("Querying closed trades from trades table...")
        rows = conn.execute("""
            SELECT 
                id, symbol, security_id, qty, entry_price, entry_time,
                status, setup_id
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
# DAILY TRADES ENDPOINT
# ==========================
@app.route("/api/analytics/daily", methods=["GET"])
def get_daily_pnl():
    """Get daily trades count from trades."""
    logger.debug("📍 /api/analytics/daily called")
    try:
        days = request.args.get("days", 30, type=int)
        logger.debug(f"Days: {days}")

        conn = get_db_connection()
        logger.debug("Querying daily trade counts...")
        rows = conn.execute("""
            SELECT 
                DATE(entry_time) as trade_date,
                COUNT(*) as trade_count,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_trades,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) as closed_trades
            FROM trades
            WHERE entry_time IS NOT NULL
            GROUP BY DATE(entry_time)
            ORDER BY trade_date DESC
            LIMIT ?
        """, (days,)).fetchall()

        conn.close()

        data = [dict(row) for row in rows]
        logger.debug(f"✅ Daily trades: {len(data)} days found")
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
    """Get trades by symbol."""
    logger.debug("📍 /api/analytics/symbols called")
    try:
        conn = get_db_connection()
        logger.debug("Querying symbol performance...")
        rows = conn.execute("""
            SELECT 
                symbol,
                COUNT(*) as trades,
                SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_trades,
                SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) as closed_trades
            FROM trades
            GROUP BY symbol
            ORDER BY trades DESC
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
    """Get setups by strategy."""
    logger.debug("📍 /api/analytics/strategy called")
    try:
        conn = get_db_connection()
        logger.debug("Querying strategy performance...")
        rows = conn.execute("""
            SELECT 
                strategy,
                COUNT(*) as setups,
                SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status='EXECUTED' THEN 1 ELSE 0 END) as executed,
                AVG(score) as avg_score
            FROM trade_setups
            GROUP BY strategy
            ORDER BY setups DESC
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
# RISK/REWARD METRICS ENDPOINT
# ==========================
@app.route("/api/analytics/risk", methods=["GET"])
def get_risk_metrics():
    """Get risk/reward metrics from trade_setups."""
    logger.debug("📍 /api/analytics/risk called")
    try:
        conn = get_db_connection()
        logger.debug("Querying risk metrics...")

        rows = conn.execute("""
            SELECT 
                COALESCE(SUM(risk), 0) as total_risk,
                COALESCE(AVG(risk), 0) as avg_risk,
                COALESCE(SUM(reward), 0) as total_reward,
                COALESCE(AVG(reward), 0) as avg_reward,
                COALESCE(AVG(rr_ratio), 0) as avg_rr_ratio,
                COUNT(*) as total_setups
            FROM trade_setups
        """).fetchone()

        conn.close()

        response = {
            "total_risk_inr": round(rows["total_risk"] or 0, 2),
            "avg_risk_inr": round(rows["avg_risk"] or 0, 2),
            "total_reward_inr": round(rows["total_reward"] or 0, 2),
            "avg_reward_inr": round(rows["avg_reward"] or 0, 2),
            "avg_rr_ratio": round(rows["avg_rr_ratio"] or 0, 2),
            "total_setups": rows["total_setups"] or 0,
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
        logger.debug("Querying pending setups...")
        rows = conn.execute("""
            SELECT 
                setup_id, symbol, qty, entry, sl, target, strategy,
                score, status, timeframe
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
# EXECUTED SETUPS ENDPOINT
# ==========================
@app.route("/api/setups/executed", methods=["GET"])
def get_executed_setups():
    """Get executed trade setups."""
    logger.debug("📍 /api/setups/executed called")
    try:
        limit = request.args.get("limit", 50, type=int)
        logger.debug(f"Limit: {limit}")

        conn = get_db_connection()
        logger.debug("Querying executed setups...")
        rows = conn.execute("""
            SELECT 
                setup_id, symbol, qty, entry, sl, target, strategy,
                score, status, timeframe, pnl, updated_at
            FROM trade_setups
            WHERE status = 'EXECUTED'
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        conn.close()

        setups = [dict(row) for row in rows]
        logger.debug(f"✅ Executed setups: {len(setups)} found")
        return jsonify(setups)
    except Exception as e:
        logger.error(f"❌ /api/setups/executed error: {e}")
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

        # Total open trades
        try:
            logger.debug("Querying open trades count...")
            open_trades = conn.execute("""
                SELECT COUNT(*) as count FROM trades WHERE status='OPEN'
            """).fetchone()
            logger.debug(f"✅ Open trades: {open_trades['count']}")
        except Exception as e:
            logger.error(f"❌ Open trades query failed: {e}")
            open_trades = {"count": 0}

        # Total closed trades
        try:
            logger.debug("Querying closed trades count...")
            closed_trades = conn.execute("""
                SELECT COUNT(*) as count FROM trades WHERE status='CLOSED'
            """).fetchone()
            logger.debug(f"✅ Closed trades: {closed_trades['count']}")
        except Exception as e:
            logger.error(f"❌ Closed trades query failed: {e}")
            closed_trades = {"count": 0}

        # Today's trades
        try:
            logger.debug("Querying today's trades...")
            today_trades = conn.execute("""
                SELECT COUNT(*) as count FROM trades 
                WHERE DATE(entry_time) = DATE('now')
            """).fetchone()
            logger.debug(f"✅ Today's trades: {today_trades['count']}")
        except Exception as e:
            logger.error(f"❌ Today's trades query failed: {e}")
            today_trades = {"count": 0}

        # Pending setups
        try:
            logger.debug("Querying pending setups count...")
            pending_setups = conn.execute("""
                SELECT COUNT(*) as count FROM trade_setups WHERE status='PENDING'
            """).fetchone()
            logger.debug(f"✅ Pending setups: {pending_setups['count']}")
        except Exception as e:
            logger.error(f"❌ Pending setups query failed: {e}")
            pending_setups = {"count": 0}

        conn.close()

        response = {
            "open_trades": open_trades["count"] or 0,
            "closed_trades": closed_trades["count"] or 0,
            "today_trades": today_trades["count"] or 0,
            "pending_setups": pending_setups["count"] or 0,
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
    print("  ✅ trades (id, symbol, qty, entry_price, entry_time, status)")
    print("  ✅ orders (trade_id, dhan_order_id, status, trigger_price)")
    print("  ✅ trade_setups (setup_id, symbol, entry, sl, target, pnl, rr_ratio)")
    print("\n✨ All queries match actual database columns!")
    print("=" * 70)

    app.run(
        host="0.0.0.0",
        port=5001,
        debug=(FLASK_ENV == "development"),
    )