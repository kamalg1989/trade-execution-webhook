# ==============================================
# 📊 ENHANCED P/L DASHBOARD — Query & Reporting Module
# With Editable Fields & Live Dhan Data
# All columns match the screenshot requirements
# ==============================================

import sqlite3
import json
from datetime import datetime, timezone, timedelta
import os
import requests
import pyotp
from threading import Thread
import pandas as pd

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")


def _db_available(path=DB_FILE):
    try:
        return bool(path) and os.path.exists(path)
    except Exception:
        return False

# ==========================
# DHAN API CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

session = requests.Session()
LIVE_PRICES_CACHE = {}


# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)


# ==========================
# TOKEN MANAGEMENT
# ==========================
def get_dhan_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    now = datetime.now(timezone.utc)

    if CURRENT_TOKEN and TOKEN_EXPIRY and now < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    try:
        log("🔐 Generating Dhan token...")
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

        response = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=15
        )

        if response.status_code != 200:
            log(f"❌ Token generation failed: {response.status_code}")
            return None

        data = response.json()
        token = data.get("accessToken")

        if not token:
            log(f"❌ No accessToken in response")
            return None

        CURRENT_TOKEN = token
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)

        log(f"✅ Token generated: {token[:30]}...")
        return token

    except Exception as e:
        log(f"❌ Token generation error: {e}")
        return None


# ==========================
# LOAD INSTRUMENTS (SECURITY ID MAPPING)
# ==========================
def load_instruments():
    try:
        log("📥 Loading instruments...")
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]
        df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip().str.upper()

        instrument_map = {}
        for _, row in df.iterrows():
            symbol = row['SEM_TRADING_SYMBOL']
            sec_id = str(row['SEM_SMST_SECURITY_ID'])
            instrument_map[symbol] = sec_id

        log(f"✅ Loaded {len(instrument_map)} instruments")
        return instrument_map

    except Exception as e:
        log(f"❌ Failed to load instruments: {e}")
        return {}


INSTRUMENTS = load_instruments()


# ==========================
# GET LIVE PRICES FROM DHAN
# ==========================
def get_live_price(symbol, security_id=None):
    """
    Fetch live price for a symbol from Dhan API.
    Uses cache to avoid repeated API calls.
    """
    try:
        # Check cache (5-minute expiry)
        if symbol in LIVE_PRICES_CACHE:
            cached_data = LIVE_PRICES_CACHE[symbol]
            if datetime.now(timezone.utc) - cached_data['timestamp'] < timedelta(minutes=5):
                return cached_data['price']

        token = get_dhan_token()
        if not token:
            return None

        # Get security ID if not provided
        if not security_id:
            security_id = INSTRUMENTS.get(symbol.replace('.NS', '').upper())

        if not security_id:
            log(f"⚠️ Security ID not found for {symbol}")
            return None

        # Fetch live price from Dhan API
        r = session.get(
            f"https://api.dhan.co/v2/quotes",
            headers={"access-token": token},
            params={
                "mode": "LTP",
                "exchangeTokens": f"NSE_EQ|{security_id}"
            },
            timeout=10
        )

        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                price = data[0].get('LTP') or data[0].get('price')

                # Cache the price
                LIVE_PRICES_CACHE[symbol] = {
                    'price': price,
                    'timestamp': datetime.now(timezone.utc)
                }

                return price

        return None

    except Exception as e:
        log(f"⚠️ Error fetching price for {symbol}: {e}")
        return None


# ==========================
# SYNC ENTRY PRICE WITH DHAN ORDERS
# ==========================
def sync_entry_price_with_dhan(token):
    """
    Cross-check entry prices from DB against actual Dhan orders.
    Update if mismatch found.
    """
    try:
        log("🔄 Syncing entry prices with Dhan orders...")

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            log(f"⚠️ Failed to fetch Dhan orders: {r.status_code}")
            return

        dhan_orders = r.json()
        if not isinstance(dhan_orders, list):
            return

        if not _db_available():
            log("⚠️ trades.db not found — skipping sync")
            return

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        for order in dhan_orders:
            if order.get('transactionType') != 'BUY':
                continue

            symbol = order.get('tradingSymbol', '').upper()
            dhan_entry = order.get('price')
            setup_id = order.get('correlationId')

            # Find matching trade in DB
            cursor.execute("""
                SELECT setup_id, entry_price_executed
                FROM executed_orders
                WHERE symbol = ? AND status = 'FILLED'
                LIMIT 1
            """, (symbol,))

            result = cursor.fetchone()
            if result:
                db_setup_id, db_entry = result
                if dhan_entry and db_entry and abs(dhan_entry - db_entry) > 0.01:
                    log(f"⚠️ Entry price mismatch for {symbol}: DB={db_entry}, Dhan={dhan_entry}")
                    log(f"   Updating DB to Dhan price...")

                    cursor.execute("""
                        UPDATE executed_orders
                        SET entry_price_executed = ?
                        WHERE symbol = ? AND status = 'FILLED'
                    """, (dhan_entry, symbol))

                    conn.commit()

        conn.close()
        log("✅ Sync complete")

    except Exception as e:
        log(f"❌ Sync error: {e}")


# ==========================
# ENHANCED DASHBOARD CLASS
# ==========================
class EnhancedPnLDashboard:
    """Enhanced P/L dashboard with live prices and editable fields."""

    def __init__(self, db_path=DB_FILE):
        # store path but keep functions robust when DB file is removed
        self.db = db_path if db_path else None

    # ==========================
    # OPEN POSITIONS WITH LIVE DATA
    # ==========================
    def get_open_trades_dashboard(self):
        """
        Get all open trades with live prices and calculated metrics.
        Returns list of trades with all dashboard columns.
        """
        try:
            if not self.db or not _db_available(self.db):
                log("⚠️ trades.db not found — returning empty open trades list")
                return []

            with sqlite3.connect(self.db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT 
                        setup_id,
                        symbol,
                        qty_executed as qty,
                        entry_price_executed as entry_price,
                        sl_price,
                        target_price,
                        base_stage,
                        placed_at,
                        dhan_order_id
                    FROM executed_orders
                    WHERE status = 'FILLED'
                    ORDER BY placed_at DESC
                """).fetchall()

            trades = []
            for row in rows:
                symbol = row['symbol']
                entry_price = row['entry_price']
                qty = row['qty']
                sl_price = row['sl_price']
                target_price = row['target_price']

                # Fetch live price from Dhan
                current_price = get_live_price(symbol)
                if current_price is None:
                    current_price = entry_price  # Fallback to entry price

                # Calculate all metrics
                allocation = entry_price * qty  # Capital allocated
                safety_sl_8pct = entry_price * 0.92  # 8% safety level
                rr_ratio = (target_price - entry_price) / (entry_price - sl_price) if (entry_price - sl_price) > 0 else 0
                chg_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                pnl = (current_price - entry_price) * qty
                pnl_pct = chg_pct

                trades.append({
                    "setup_id": row['setup_id'],
                    "symbol": symbol,
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(current_price, 2),  # Current price (live)
                    "sl": round(sl_price, 2),
                    "target_price": round(target_price, 2),
                    "qty": qty,
                    "allocation": round(allocation, 2),
                    "safety_sl_8pct": round(safety_sl_8pct, 2),
                    "rr_pct": round(rr_ratio, 2),
                    "chg_pct": round(chg_pct, 2),
                    "price": round(current_price, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "dhan_order_id": row['dhan_order_id'],
                    "placed_at": row['placed_at'],
                })

            return trades

        except Exception as e:
            log(f"❌ Error fetching open trades: {e}")
            return []

    # ==========================
    # UPDATE EDITABLE FIELDS
    # ==========================
    def update_trade_field(self, setup_id, field_name, new_value):
        """
        Update an editable field in the database.
        Allowed fields: entry_price_executed, exit_price, sl_price, target_price
        """
        allowed_fields = [
            "entry_price_executed",
            "sl_price",
            "target_price"
        ]

        if field_name not in allowed_fields:
            return False, f"Field '{field_name}' is not editable"

        try:
            if not self.db or not _db_available(self.db):
                return False, "DB file missing"
            new_value = float(new_value)

            with sqlite3.connect(self.db) as conn:
                conn.execute(f"""
                    UPDATE executed_orders
                    SET {field_name} = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE setup_id = ?
                """, (new_value, setup_id))

                conn.commit()

            log(f"✅ Updated {setup_id} {field_name} = {new_value}")
            return True, "Update successful"

        except Exception as e:
            log(f"❌ Error updating trade: {e}")
            return False, str(e)

    def update_safety_sl(self, setup_id, safety_sl_pct):
        """
        Update safety SL level and calculate new SL price.
        safety_sl_pct: 0.92 means 8% below entry price
        """
        try:
            if not self.db or not _db_available(self.db):
                return False, "DB file missing"
            safety_sl_pct = float(safety_sl_pct)

            with sqlite3.connect(self.db) as conn:
                # Get entry price
                result = conn.execute("""
                    SELECT entry_price_executed, symbol
                    FROM executed_orders
                    WHERE setup_id = ?
                """, (setup_id,)).fetchone()

                if not result:
                    return False, "Trade not found"

                entry_price, symbol = result
                new_sl = entry_price * safety_sl_pct

                # Update SL in database
                conn.execute("""
                    UPDATE executed_orders
                    SET sl_price = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE setup_id = ?
                """, (round(new_sl, 2), setup_id))

                conn.commit()

            log(f"✅ Updated {symbol} safety SL to {safety_sl_pct:.2%} = ₹{round(new_sl, 2)}")
            return True, f"SL updated to ₹{round(new_sl, 2)}"

        except Exception as e:
            log(f"❌ Error updating safety SL: {e}")
            return False, str(e)

    # ==========================
    # SUMMARY STATS
    # ==========================
    def get_dashboard_summary(self):
        """Get quick summary stats for dashboard."""
        trades = self.get_open_trades_dashboard()

        total_pnl = sum(t['pnl'] for t in trades)
        total_allocation = sum(t['allocation'] for t in trades)
        winning_trades = len([t for t in trades if t['pnl'] > 0])
        losing_trades = len([t for t in trades if t['pnl'] < 0])
        avg_pnl_pct = (sum(t['pnl_pct'] for t in trades) / len(trades)) if trades else 0

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_trades": len(trades),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(avg_pnl_pct, 2),
            "total_allocation": round(total_allocation, 2),
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate_pct": round((winning_trades / len(trades) * 100), 2) if trades else 0,
        }

    # ==========================
    # PRINT DASHBOARD
    # ==========================
    def print_open_trades_table(self):
        """Pretty-print open trades table."""
        trades = self.get_open_trades_dashboard()
        summary = self.get_dashboard_summary()

        print("\n" + "=" * 150)
        print("📊 OPEN TRADES DASHBOARD")
        print("=" * 150)
        print(f"Updated: {summary['timestamp']}\n")

        print(f"{'Symbol':<12} {'Entry':<10} {'Exit':<10} {'SL':<10} {'Target':<10} "
              f"{'Qty':<8} {'Allocation':<12} {'Safety SL':<12} {'RR%':<8} "
              f"{'Chg %':<8} {'Price':<10} {'PnL':<12}")
        print("-" * 150)

        for t in trades:
            symbol = t['symbol']
            entry = f"₹{t['entry_price']:.2f}"
            exit_p = f"₹{t['exit_price']:.2f}"
            sl = f"₹{t['sl']:.2f}"
            target = f"₹{t['target_price']:.2f}"
            qty = f"{t['qty']}"
            alloc = f"₹{t['allocation']:,.0f}"
            safety_sl = f"₹{t['safety_sl_8pct']:.2f}"
            rr = f"{t['rr_pct']:.2f}"
            chg = f"{t['chg_pct']:+.2f}%"
            price = f"₹{t['price']:.2f}"
            pnl_mark = "📗" if t['pnl'] > 0 else "📕"
            pnl = f"{pnl_mark}₹{t['pnl']:,.0f}"

            print(f"{symbol:<12} {entry:<10} {exit_p:<10} {sl:<10} {target:<10} "
                  f"{qty:<8} {alloc:<12} {safety_sl:<12} {rr:<8} {chg:<8} {price:<10} {pnl:<12}")

        print("-" * 150)
        print(f"\n📊 SUMMARY:")
        print(f"   Total Trades: {summary['total_trades']}")
        print(f"   Total P&L: ₹{summary['total_pnl']:,.2f} ({summary['total_pnl_pct']:+.2f}%)")
        print(f"   Total Allocation: ₹{summary['total_allocation']:,.0f}")
        print(f"   Win Rate: {summary['winning_trades']}/{summary['total_trades']} ({summary['win_rate_pct']:.1f}%)")
        print("\n" + "=" * 150)

    # ==========================
    # EXPORT AS JSON
    # ==========================
    def export_dashboard_json(self, output_file="open_trades_dashboard.json"):
        """Export dashboard to JSON for API usage."""
        trades = self.get_open_trades_dashboard()
        summary = self.get_dashboard_summary()

        report = {
            "timestamp": summary['timestamp'],
            "summary": summary,
            "trades": trades,
        }

        with open(output_file, "w") as f:
            json.dump(report, f, indent=2, default=str)

        log(f"✅ Exported to {output_file}")
        return report


# ==========================
# USAGE EXAMPLES
# ==========================
if __name__ == "__main__":
    dashboard = EnhancedPnLDashboard()

    # Sync entry prices with Dhan (optional)
    token = get_dhan_token()
    if token:
        sync_entry_price_with_dhan(token)

    # Print dashboard
    dashboard.print_open_trades_table()

    # Export as JSON
    dashboard.export_dashboard_json()

    # Example: Update a trade field
    # dashboard.update_trade_field("setup_id_here", "sl_price", 5100.00)

    # Example: Update safety SL
    # dashboard.update_safety_sl("setup_id_here", 0.92)  # 8% below entry