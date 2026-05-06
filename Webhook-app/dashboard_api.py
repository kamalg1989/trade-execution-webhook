# ==============================================
# 🚀 OPEN TRADES DASHBOARD API
# Flask backend serving dashboard data with edit endpoints
# ==============================================

from flask import Flask, jsonify, request
from flask_cors import CORS
import sys
import os

# Add dashboard module to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Pnl_dashboard import EnhancedPnLDashboard, get_dhan_token, sync_entry_price_with_dhan

app = Flask(__name__)
CORS(app)

dashboard = EnhancedPnLDashboard()


# ==========================
# ROUTES
# ==========================

@app.route('/api/dashboard/open-trades', methods=['GET'])
def get_open_trades():
    """Get all open trades with live prices and metrics."""
    try:
        trades = dashboard.get_open_trades_dashboard()
        summary = dashboard.get_dashboard_summary()

        return jsonify({
            "success": True,
            "timestamp": summary['timestamp'],
            "summary": summary,
            "trades": trades,
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/dashboard/update-trade', methods=['POST'])
def update_trade_field():
    """Update an editable field in a trade."""
    try:
        data = request.get_json()

        setup_id = data.get('setup_id')
        field_name = data.get('field')
        value = data.get('value')

        if not all([setup_id, field_name, value is not None]):
            return jsonify({
                "success": False,
                "error": "Missing required fields: setup_id, field, value"
            }), 400

        # Map field names to DB columns
        field_mapping = {
            'entry_price_executed': 'entry_price_executed',
            'entry_price': 'entry_price_executed',
            'sl_price': 'sl_price',
            'sl': 'sl_price',
            'target_price': 'target_price',
            'target': 'target_price',
        }

        db_field = field_mapping.get(field_name)

        if not db_field:
            return jsonify({
                "success": False,
                "error": f"Field '{field_name}' is not editable"
            }), 400

        success, message = dashboard.update_trade_field(setup_id, db_field, value)

        if success:
            return jsonify({
                "success": True,
                "message": message
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/dashboard/update-safety-sl', methods=['POST'])
def update_safety_sl():
    """Update safety SL level (8% below entry price)."""
    try:
        data = request.get_json()

        setup_id = data.get('setup_id')
        safety_sl_pct = data.get('safety_sl_pct', 0.92)

        if not setup_id:
            return jsonify({
                "success": False,
                "error": "Missing setup_id"
            }), 400

        success, message = dashboard.update_safety_sl(setup_id, safety_sl_pct)

        if success:
            return jsonify({
                "success": True,
                "message": message
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/dashboard/sync-entry-prices', methods=['POST'])
def sync_entry_prices():
    """
    Sync entry prices from database with actual Dhan orders.
    Useful to ensure prices match between our DB and Dhan.
    """
    try:
        token = get_dhan_token()

        if not token:
            return jsonify({
                "success": False,
                "error": "Failed to generate Dhan token"
            }), 401

        sync_entry_price_with_dhan(token)

        return jsonify({
            "success": True,
            "message": "Entry prices synced with Dhan"
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/dashboard/summary', methods=['GET'])
def get_summary():
    """Get dashboard summary stats."""
    try:
        summary = dashboard.get_dashboard_summary()
        return jsonify({
            "success": True,
            "summary": summary
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "open-trades-dashboard-api"
    }), 200


# ==========================
# ERROR HANDLERS
# ==========================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found"
    }), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


# ==========================
# ENTRY POINT
# ==========================

if __name__ == '__main__':
    print("=" * 80)
    print("🚀 OPEN TRADES DASHBOARD API")
    print("=" * 80)
    print("✅ Available endpoints:")
    print("   GET  /api/dashboard/open-trades       → Get all open trades")
    print("   POST /api/dashboard/update-trade      → Update trade field")
    print("   POST /api/dashboard/update-safety-sl  → Update safety SL")
    print("   POST /api/dashboard/sync-entry-prices → Sync with Dhan")
    print("   GET  /api/dashboard/summary           → Get summary stats")
    print("   GET  /api/health                      → Health check")
    print("=" * 80)
    print("\n📡 Starting server on http://0.0.0.0:5002\n")

    app.run(host='0.0.0.0', port=5002, debug=False)