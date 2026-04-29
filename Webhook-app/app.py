# ==============================================
# 🚀 TELEGRAM WEBHOOK (app.py) v6 - PASSES TOKEN TO SUBPROCESS
# Token is generated ONCE in parent, passed to entry_engine
# Entry_engine uses it WITHOUT regenerating
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
from datetime import datetime, timezone, timedelta

# ==========================
# GLOBAL STATE
# ==========================
PROCESSED_CALLBACKS = set()
LOCK = threading.Lock()

# Token caching (REUSE across all API calls)
CURRENT_TOKEN = None
TOKEN_EXPIRY = None

# Reusable session
session = requests.Session()


# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
ENTRY_ENGINE_PATH = "/root/trade-execution-webhook/Webhook-app/entry_engine.py"
PROJECT_ROOT = "/root/trade-execution-webhook"
VENV_PYTHON = "/root/trade-execution-webhook/venv/bin/python"


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
# TOKEN MANAGEMENT - ONLY GENERATES ONCE
# ==========================
def generate_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    try:
        log("🔐 Generating Dhan token (ONE TIME)...")

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

        log(f"✅ Token generated & cached: {token[:30]}...")
        return token

    except Exception as e:
        log(f"❌ Token generation exception: {e}")
        return None


def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    now = datetime.now(timezone.utc)

    if CURRENT_TOKEN and TOKEN_EXPIRY and now < TOKEN_EXPIRY:
        log(f"✅ Reusing cached token (expires in 23h)")
        return CURRENT_TOKEN

    log(f"⏳ Token missing/expired, generating...")
    return generate_token()


# ==========================
# CHECK DHAN FOR OPEN ORDERS (USING CACHED TOKEN)
# ==========================
def get_dhan_forever_orders(token):
    """
    Fetch ALL forever orders from Dhan using /v2/forever/orders endpoint.
    Uses provided token (already cached from parent)
    """
    try:
        if not token:
            log("❌ No token provided to get_dhan_forever_orders")
            return []

        log(f"📡 GET /v2/forever/orders...")

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        log(f"   Status: {r.status_code}")

        if r.status_code != 200:
            log(f"⚠️ API error: {r.status_code}")
            return []

        orders = r.json()
        if not isinstance(orders, list):
            log(f"⚠️ Expected list, got: {type(orders)}")
            return []

        log(f"✅ Retrieved {len(orders)} orders from Dhan")
        return orders

    except Exception as e:
        log(f"❌ Failed to fetch orders: {e}")
        return []


def check_for_existing_buy_order(symbol, token):
    """
    Check if symbol already has an open BUY order on Dhan.
    Uses cached token from parent.
    """
    try:
        orders = get_dhan_forever_orders(token)

        if not orders:
            log(f"✅ {symbol}: No orders on Dhan (empty list)")
            return False

        symbol_upper = symbol.upper().replace(".NS", "")

        for order in orders:
            if not isinstance(order, dict):
                continue

            order_symbol = order.get("tradingSymbol", "").strip().upper()
            trans_type = order.get("transactionType", "")
            status = order.get("orderStatus", "")

            if order_symbol == symbol_upper and trans_type == "BUY":
                if status in ["PENDING", "TRIGGERED", "CONFIRM", "ACCEPTED"]:
                    log(f"⚠️ {symbol}: Found open BUY order (Status={status})")
                    return True

        log(f"✅ {symbol}: No open BUY orders on Dhan")
        return False

    except Exception as e:
        log(f"❌ Error checking orders: {e}")
        return False


# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        log(f"📨 TELEGRAM: {msg[:80]}...")
        session.post(
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
    symbol = symbol_input.replace(".NS", "").strip().upper()
    if INSTRUMENT_DF.empty:
        log("⚠️ Instruments not loaded, skipping validation")
        return True
    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]
    return not row.empty


# ==========================
# EXECUTE ENTRY ENGINE
# ==========================
def execute_entry_engine_subprocess(payload, token):
    """
    Execute entry_engine.py with token passed via env var.
    Token is generated ONCE in parent, reused in subprocess.
    """
    try:
        required = ["setup_id", "symbol", "qty", "entry", "sl", "target", "score"]
        for field in required:
            if field not in payload or payload[field] is None:
                log(f"❌ Missing field: {field}")
                return False

        env = os.environ.copy()
        env.update({
            "SYMBOL": str(payload["symbol"]),
            "QTY": str(payload["qty"]),
            "ENTRY": str(payload["entry"]),
            "SL": str(payload["sl"]),
            "TARGET": str(payload["target"]),
            "SCORE": str(payload["score"]),
            "SETUP_ID": str(payload["setup_id"]),
            "DHAN_TOKEN": token,  # 🔑 PASS TOKEN TO SUBPROCESS!
        })

        log("🚀 EXECUTING ENTRY ENGINE SUBPROCESS")
        log(f"   Symbol: {payload['symbol']}, Qty: {payload['qty']}")
        log(f"   Token passed: {token[:30]}...")

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

        # Check for success
        if "✅ ORDER PLACED SUCCESSFULLY" in stdout:
            log("✅ Order success detected")
            return True

        if "Order placed successfully" in stdout:
            log("✅ Order success detected")
            return True

        try:
            last_line = stdout.strip().split("\n")[-1]
            if last_line.startswith("{"):
                parsed = json.loads(last_line)
                if parsed.get("success"):
                    log(f"✅ Order success: {parsed.get('order_id')}")
                    return True
        except:
            pass

        log("❌ Order NOT confirmed")
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
    try:
        data = request.get_json(force=True)
        log("=" * 80)
        log("📩 TELEGRAM WEBHOOK RECEIVED")
        log("=" * 80)

        if not data or "callback_query" not in data:
            log("⚠️ No callback_query")
            return "OK"

        query = data["callback_query"]
        callback_id = query["id"]

        # Dedup
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

        log("✅ PARSED:")
        log(f"   Symbol: {symbol}, Qty: {qty}, Entry: {entry}, SL: {sl}, Target: {target}")

        # ==== VALIDATIONS ====

        if not setup_id or not symbol:
            log(f"❌ Missing setup_id or symbol")
            send_telegram("❌ Missing setup_id or symbol")
            return "OK"

        if qty is None or qty <= 0:
            log(f"❌ Invalid qty: {qty}")
            send_telegram(f"❌ Invalid qty: {qty}")
            return "OK"

        if entry is None or entry <= 0:
            log(f"❌ Invalid entry: {entry}")
            send_telegram(f"❌ Invalid entry: {entry}")
            return "OK"

        if sl is None or sl <= 0 or target is None or target <= 0:
            log(f"❌ Invalid SL or target")
            send_telegram(f"❌ Invalid SL/target")
            return "OK"

        if not (sl < entry < target):
            log(f"❌ Invalid price order")
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

        # ==== GET TOKEN (ONCE) ====
        log(f"\n🔐 Getting token for API calls...")
        token = get_token()

        if not token:
            log(f"❌ Failed to get token")
            send_telegram(f"❌ Token generation failed")
            return "OK"

        log(f"✅ Token ready: {token[:30]}...")

        # ==== CHECK DHAN FOR EXISTING ORDERS ====
        log(f"\n🔍 Checking Dhan for existing orders on {symbol}...")

        if check_for_existing_buy_order(symbol, token):
            log(f"⚠️ {symbol} already has open BUY order on Dhan")
            send_telegram(f"⚠️ {symbol} already has open order on Dhan - skipping")
            return "OK"

        log(f"✅ {symbol} is clear on Dhan - proceeding\n")

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
        }

        send_telegram(f"⏳ Processing {symbol} | Qty: {qty}")

        # Execute entry engine (PASS TOKEN!)
        success = execute_entry_engine_subprocess(payload, token)

        if success:
            send_telegram(f"✅ ORDER EXECUTED\n{symbol} | Qty: {qty}\nSL: {sl} | Target: {target}")
        else:
            send_telegram(f"❌ ORDER FAILED\n{symbol} | Check logs")

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
    log("🚀 TELEGRAM WEBHOOK (app.py) v6 - FINAL")
    log("=" * 80)
    log("✅ Token generated ONCE in parent")
    log("✅ Token passed to subprocess via env var")
    log("✅ NO double generation = NO rate limit!")
    log("=" * 80)

    app.run(host="0.0.0.0", port=5000, debug=False)