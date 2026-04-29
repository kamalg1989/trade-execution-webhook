# ==============================================
# 🚀 TELEGRAM WEBHOOK (app.py) — FIXED v3
# Uses /v2/forever/all (correct endpoint)
# Better token handling
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request
import threading
import time
import subprocess
import json
import pyotp

# ==========================
# GLOBAL DEDUP STORAGE
# ==========================
PROCESSED_CALLBACKS = set()
LOCK = threading.Lock()


# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Paths
ENTRY_ENGINE_PATH = "/root/trade-execution-webhook/Webhook-app/entry_engine.py"
PROJECT_ROOT = "/root/trade-execution-webhook"
VENV_PYTHON = "/root/trade-execution-webhook/venv/bin/python"

# Dhan API
CURRENT_TOKEN = None
TOKEN_EXPIRY = None

session = requests.Session()


# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)


# ==========================
# LOAD INSTRUMENTS
# ==========================
def load_instruments():
    try:
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]
        df['SEM_TRADING_SYMBOL'] = (
            df['SEM_TRADING_SYMBOL']
            .astype(str)
            .str.strip()
            .str.upper()
        )
        log(f"✅ Instruments Loaded: {len(df)}")
        return df
    except Exception as e:
        log(f"⚠️ Failed to load instruments: {e}")
        return pd.DataFrame()


INSTRUMENT_DF = load_instruments()


# ==========================
# DHAN TOKEN MANAGEMENT
# ==========================
def generate_dhan_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        log(f"🔐 Generating Dhan token...")

        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=15
        )

        log(f"📡 Token response: {r.status_code}")

        if r.status_code != 200:
            log(f"❌ Token generation failed: {r.status_code} {r.text[:200]}")
            return None

        data = r.json()
        token = data.get("accessToken")

        if token:
            CURRENT_TOKEN = token
            TOKEN_EXPIRY = time.time() + (23 * 3600)
            log(f"✅ Dhan token generated: {token[:20]}...")
            return token
        else:
            log(f"❌ No token in response: {data}")
            return None

    except Exception as e:
        log(f"❌ Token generation exception: {e}")
        return None


def get_dhan_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    now = time.time()
    if CURRENT_TOKEN and TOKEN_EXPIRY and now < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    return generate_dhan_token()


# ==========================
# CHECK DHAN FOR OPEN ORDERS (FIXED)
# ==========================
def get_dhan_all_forever_orders():
    """
    Fetch ALL forever orders from Dhan using /v2/forever/all endpoint.
    Returns: list of orders or empty list if error
    """
    try:
        token = get_dhan_token()
        if not token:
            log("❌ Cannot get Dhan token")
            return []

        log(f"📡 Fetching all forever orders from /v2/forever/all...")

        r = requests.get(
            "https://api.dhan.co/v2/forever/all",
            headers={"access-token": token},
            timeout=30
        )

        log(f"📡 Response: {r.status_code}")

        if r.status_code != 200:
            log(f"⚠️ Dhan API error: {r.status_code} {r.text[:200]}")
            return []

        orders = r.json()
        if not isinstance(orders, list):
            log(f"⚠️ Unexpected response type: {type(orders)}")
            return []

        log(f"✅ Retrieved {len(orders)} total forever orders from Dhan")
        return orders

    except Exception as e:
        log(f"❌ Failed to fetch forever orders: {e}")
        return []


def symbol_has_open_buy_order(symbol):
    """
    Check if Dhan has ANY open BUY order for this symbol.
    Uses /v2/forever/all endpoint.
    Returns: True if open BUY order exists, False otherwise
    """
    try:
        orders = get_dhan_all_forever_orders()

        if not orders:
            log(f"✅ {symbol}: No orders on Dhan - OK to place new order")
            return False

        # Filter for BUY orders matching this symbol
        matching_buys = []
        for order in orders:
            if not isinstance(order, dict):
                continue

            order_symbol = order.get("tradingSymbol", "").strip().upper()
            trans_type = order.get("transactionType", "")
            status = order.get("orderStatus", "")
            order_id = order.get("orderId")

            # Match symbol and look for BUY orders in pending/triggered state
            if order_symbol == symbol.upper() and trans_type == "BUY":
                if status in ["PENDING", "TRIGGERED", "CONFIRM"]:
                    matching_buys.append({
                        "orderId": order_id,
                        "status": status,
                        "qty": order.get("quantity", 0)
                    })
                    log(f"   Found {symbol} BUY order: ID={order_id}, Status={status}")

        if matching_buys:
            log(f"⚠️ {symbol}: Already has {len(matching_buys)} open BUY order(s) - SKIP")
            return True

        log(f"✅ {symbol}: No open BUY orders on Dhan - OK to place new order")
        return False

    except Exception as e:
        log(f"❌ Failed to check symbol orders: {e}")
        return False


# ==========================
# TELEGRAM SEND
# ==========================
def send_telegram(msg):
    """Send message to user via Telegram."""
    try:
        log(f"📨 TELEGRAM: {msg[:80]}...")
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"❌ Telegram Error: {e}")


# ==========================
# VALIDATE SYMBOL
# ==========================
def validate_symbol(symbol_input):
    """Check if symbol exists in Dhan instruments."""
    symbol = symbol_input.replace(".NS", "").strip().upper()

    if INSTRUMENT_DF.empty:
        log("⚠️ Instruments not loaded, skipping validation")
        return True

    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]
    return not row.empty


# ==========================
# SUBPROCESS EXECUTION
# ==========================
def execute_entry_engine_subprocess(payload):
    """
    Execute entry_engine.py as subprocess.
    """

    try:
        # Validate required fields
        required = ["setup_id", "symbol", "qty", "entry", "sl", "target", "score"]
        for field in required:
            if field not in payload or payload[field] is None:
                log(f"❌ Missing field: {field}")
                return False

        # Prepare environment
        env = os.environ.copy()
        env.update({
            "SYMBOL": str(payload["symbol"]),
            "QTY": str(payload["qty"]),
            "ENTRY": str(payload["entry"]),
            "SL": str(payload["sl"]),
            "TARGET": str(payload["target"]),
            "SCORE": str(payload["score"]),
            "SETUP_ID": str(payload["setup_id"]),
            "BASE_STAGE": str(payload.get("base_stage", "0")),
            "BASE_QUALITY_SCORE": str(payload.get("base_quality_score", "0")),
            "TICK_SIZE": str(payload.get("tick_size", "0.05")),
        })

        log("🚀 EXECUTING ENTRY ENGINE SUBPROCESS")
        log(f"   Script: {ENTRY_ENGINE_PATH}")
        log(f"   Symbol: {payload['symbol']}, Qty: {payload['qty']}")

        result = subprocess.run(
            [VENV_PYTHON, ENTRY_ENGINE_PATH],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30
        )

        log("📤 SUBPROCESS OUTPUT:")
        log(result.stdout)

        if result.stderr:
            log("📤 STDERR:")
            log(result.stderr)

        stdout = result.stdout or ""

        # Check for success via JSON
        try:
            last_line = stdout.strip().split("\n")[-1]
            if last_line.startswith("{"):
                parsed = json.loads(last_line)
                if parsed.get("success"):
                    log(f"✅ Order success: {parsed.get('order_id')}")
                    return True
        except:
            pass

        # Check for success via string match
        if "Order placed successfully" in stdout or "✅ Order placed" in stdout:
            log("✅ Order success detected")
            return True

        log("❌ Order NOT confirmed from entry engine")
        return False

    except subprocess.TimeoutExpired:
        log("❌ Entry engine timeout")
        return False
    except Exception as e:
        log(f"❌ Error executing entry engine: {e}")
        return False


# ==========================
# FLASK APP
# ==========================
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Receives Telegram callback: BUY|setup_id|symbol|qty|entry|sl|target|score
    """

    try:
        data = request.get_json(force=True)
        log("=" * 80)
        log("📩 TELEGRAM WEBHOOK RECEIVED")
        log("=" * 80)
        log(json.dumps(data, indent=2))

        if not data or "callback_query" not in data:
            log("⚠️ No callback_query")
            return "OK"

        query = data["callback_query"]
        callback_id = query["id"]

        # ==== CALLBACK DEDUP (Telegram level only) ====
        with LOCK:
            if callback_id in PROCESSED_CALLBACKS:
                log(f"⚠️ Duplicate callback: {callback_id}")
                return "OK"
            PROCESSED_CALLBACKS.add(callback_id)

        # Parse callback
        raw_callback = query.get("data", "")
        log(f"📋 Callback: {raw_callback}")

        parts = raw_callback.split("|")

        if len(parts) < 8:
            log(f"❌ Invalid format")
            send_telegram("❌ Invalid payload format")
            return "OK"

        action = parts[0]
        setup_id = parts[1]
        symbol = parts[2]

        def safe_int(x):
            try:
                return int(x)
            except:
                return None

        def safe_float(x):
            try:
                return float(x)
            except:
                return None

        qty = safe_int(parts[3])
        entry = safe_float(parts[4])
        sl = safe_float(parts[5])
        target = safe_float(parts[6])
        score = safe_float(parts[7])
        base_stage = safe_int(parts[8]) if len(parts) > 8 else 0
        base_quality_score = safe_float(parts[9]) if len(parts) > 9 else 0.0
        tick_size = safe_float(parts[10]) if len(parts) > 10 else 0.05

        log("✅ PARSED:")
        log(f"   Symbol: {symbol}, Qty: {qty}, Entry: {entry}, SL: {sl}, Target: {target}")

        # ==== VALIDATION ====

        if not setup_id or not symbol:
            log(f"❌ Missing setup_id or symbol")
            send_telegram("❌ Missing setup_id or symbol")
            return "OK"

        if qty is None or qty <= 0:
            log(f"❌ Invalid qty")
            send_telegram(f"❌ Invalid qty: {qty}")
            return "OK"

        if entry is None or entry <= 0:
            log(f"❌ Invalid entry")
            send_telegram(f"❌ Invalid entry: {entry}")
            return "OK"

        if sl is None or sl <= 0 or target is None or target <= 0:
            log(f"❌ Invalid SL or target")
            send_telegram(f"❌ Invalid SL/target")
            return "OK"

        if not (sl < entry < target):
            log(f"❌ Invalid price order: SL={sl} < ENTRY={entry} < TARGET={target}")
            send_telegram(f"❌ Invalid price order")
            return "OK"

        if not validate_symbol(symbol):
            log(f"❌ Symbol not found: {symbol}")
            send_telegram(f"❌ Symbol not found: {symbol}")
            return "OK"

        if action != "BUY":
            log(f"❌ Invalid action: {action}")
            send_telegram(f"❌ Invalid action: {action}")
            return "OK"

        # ==== CHECK DHAN FOR EXISTING ORDERS (FIXED) ====
        log(f"\n🔍 Checking Dhan for existing orders on {symbol}...")

        if symbol_has_open_buy_order(symbol):
            log(f"⚠️ {symbol} already has open BUY order on Dhan - SKIP")
            send_telegram(f"⚠️ {symbol} already has open order on Dhan")
            return "OK"

        log(f"✅ {symbol} is clear on Dhan - proceeding to place order\n")

        # ==== ALL VALIDATIONS PASSED ====
        log("✅ ALL VALIDATIONS PASSED")

        payload = {
            "setup_id": setup_id,
            "symbol": symbol,
            "qty": qty,
            "entry": entry,
            "sl": sl,
            "target": target,
            "score": score,
            "base_stage": base_stage,
            "base_quality_score": base_quality_score,
            "tick_size": tick_size,
        }

        send_telegram(f"⏳ Processing {symbol} | Qty: {qty}")

        # Execute entry engine
        success = execute_entry_engine_subprocess(payload)

        if success:
            send_telegram(f"✅ ORDER EXECUTED\n{symbol} | Qty: {qty}\nSL: {sl} | Target: {target}")
        else:
            send_telegram(f"❌ ORDER FAILED\n{symbol} | Please check logs")

        return "OK"

    except Exception as e:
        log(f"❌ WEBHOOK EXCEPTION: {e}")
        send_telegram(f"❌ Webhook error: {str(e)[:50]}")
        return "OK"


@app.route("/", methods=["GET"])
def home():
    return "Webhook running"


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    log("=" * 80)
    log("🚀 TELEGRAM WEBHOOK (app.py) v3 - FIXED")
    log("=" * 80)
    log(f"Uses /v2/forever/all endpoint (correct API)")
    log(f"Entry Engine: {ENTRY_ENGINE_PATH}")

    app.run(host="0.0.0.0", port=5000, debug=False)