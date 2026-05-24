# ==============================================
# 🚀 TELEGRAM WEBHOOK (app.py) v10 - AGGREGATE-RISK GATE ADDED
# Webhook + entry-engine execution (dashboard retired)
# Token passed to subprocess, no double generation
# Smart token validation - tests with Dhan API
# NEW (v10): advisory open-risk gate at Confirm time (§10, warn-only)
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request, jsonify
import threading
import time
import subprocess
import json
import pyotp
from datetime import datetime, timezone, timedelta
import sys

# Ensure current directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

# Consolidated data layer (single source of truth for sheet ops — §9)
import google_sheets_db as gsdb

# ==========================
# GLOBAL STATE
# ==========================
PROCESSED_CALLBACKS = set()
LOCK = threading.Lock()

# Token caching
CURRENT_TOKEN = None
TOKEN_EXPIRY = None

# Tick size cache
TICK_SIZE_CACHE = {}

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
# AGGREGATE-RISK GATE CONFIG (advisory / warn-only — §10)
# Read from env; fall back to in-code defaults if env param not available.
# ==========================
def _env_float(name, default):
    try:
        v = os.getenv(name)
        return float(v) if v not in (None, "") else float(default)
    except (ValueError, TypeError):
        log(f"⚠️ Bad value for {name!r}; using default {default}")
        return float(default)

CAPITAL = _env_float("CAPITAL", 1_000_000)                 # ₹ total capital
MAX_OPEN_RISK_PCT = _env_float("MAX_OPEN_RISK_PCT", 0.10)  # 10% of capital


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
# TOKEN VALIDATION & MANAGEMENT (UPDATED v8)
# ==========================

def validate_token_with_dhan(token):
    """
    🔐 Validate token by calling Dhan API.
    Uses /v2/profile endpoint to check if token is valid.
    This is the official test endpoint recommended by Dhan.

    Returns: True if token is valid, False otherwise
    """
    try:
        if not token:
            log("⚠️ No token provided for validation")
            return False

        log("🔐 Validating token with Dhan /v2/profile API...")

        response = session.get(
            "https://api.dhan.co/v2/profile",
            headers={"access-token": token},
            timeout=5  # Fail fast - 5 second timeout
        )

        if response.status_code == 200:
            data = response.json()
            token_validity = data.get("tokenValidity", "unknown")
            dhan_client_id = data.get("dhanClientId", "unknown")

            log(f"✅ Token is VALID")
            log(f"   Client ID: {dhan_client_id}")
            log(f"   Token expires: {token_validity}")
            return True
        else:
            log(f"❌ Token validation failed: HTTP {response.status_code}")
            log(f"   Response: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        log(f"⚠️ Token validation timed out (5s)")
        return False
    except Exception as e:
        log(f"⚠️ Token validation error: {e}")
        return False


def generate_token():
    """
    🔐 Generate a fresh token from Dhan API.
    Uses DHAN_CLIENT_ID, DHAN_PIN, and DHAN_TOTP_SECRET from environment.
    """
    global CURRENT_TOKEN, TOKEN_EXPIRY

    try:
        log("🔐 Generating fresh Dhan token...")

        if not all([DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET]):
            log("❌ Missing Dhan credentials in environment")
            return None

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
            log(f"   Response: {response.text[:200]}")
            return None

        data = response.json()
        token = data.get("accessToken")

        if not token:
            log(f"❌ No accessToken in response: {data}")
            return None

        CURRENT_TOKEN = token
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)

        log(f"✅ Token generated & cached: {token[:30]}...")
        return token

    except Exception as e:
        log(f"❌ Token generation exception: {e}")
        return None


def get_token():
    """
    🔐 Smart token management (v8).

    Flow:
    1. If we have a cached token → TEST it with Dhan API
    2. If test passes → REUSE the token (no regeneration)
    3. If test fails → REGENERATE a new token
    4. If no cached token → GENERATE a new one

    This approach:
    ✅ Detects actual token invalidity (not just time-based)
    ✅ Reuses valid tokens longer (no unnecessary regeneration)
    ✅ Regenerates only when Dhan actually rejects it
    ✅ More reliable than time-based expiry
    """
    global CURRENT_TOKEN, TOKEN_EXPIRY

    # If we have a cached token, TEST it first
    if CURRENT_TOKEN:
        log(f"🔐 Testing cached token...")

        if validate_token_with_dhan(CURRENT_TOKEN):
            log(f"✅ Cached token is still valid, reusing it")
            return CURRENT_TOKEN
        else:
            log(f"⚠️ Cached token is invalid, generating new one...")
            CURRENT_TOKEN = None  # Clear invalid token
            # Fall through to generate new token

    # No cached token or validation failed → generate fresh token
    log(f"⏳ Generating fresh token...")
    return generate_token()


# ==========================
# CHECK DHAN FOR OPEN ORDERS
# ==========================
def get_dhan_forever_orders(token):
    """Fetch ALL forever orders from Dhan."""
    try:
        if not token:
            log("❌ No token provided")
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
    """Check if symbol already has an open BUY order on Dhan."""
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
# AGGREGATE-RISK GATE (advisory / warn-only — §10)
# ==========================
def check_aggregate_risk(symbol, entry, sl, qty):
    """Advisory open-risk check at Confirm time.

    Sums open risk across all live trades (OPEN / PARTIAL / EXIT_PENDING),
    adds this new trade's risk, and compares against CAPITAL * MAX_OPEN_RISK_PCT.

    WARN-ONLY: this function NEVER blocks. It does not return anything used
    for flow control — it just fires a Telegram warning if the cap would be
    breached, or if open risk can't be read. The caller proceeds regardless.

    To make this a HARD gate later: have this return True on breach (False
    otherwise), then at the call site: `if check_aggregate_risk(...): return "OK"`.
    """
    cap = CAPITAL * MAX_OPEN_RISK_PCT

    # --- new trade's risk (from the webhook payload) ---
    try:
        new_trade_risk = (float(entry) - float(sl)) * int(qty)
    except (ValueError, TypeError) as e:
        log(f"⚠️ Risk gate: bad new-trade inputs ({e}); skipping risk check")
        return

    # --- sum open risk from the sheet (fail-OPEN on any read error) ---
    try:
        open_trades = gsdb.get_open_trades()
    except Exception as e:
        log(f"⚠️ Risk gate: could not read open trades ({e}); "
            f"allowing trade (fail-open)")
        send_telegram(
            f"⚠️ Could not verify open risk for {symbol} "
            f"(sheet read failed) — proceeding anyway. Check positions manually."
        )
        return

    total_open_risk = 0.0
    for t in open_trades:
        try:
            t_entry = float(t.get("Entry_Price") or 0)
            t_sl = float(t.get("Structural_SL") or 0)
            t_qty = float(t.get("Remaining_Qty") or 0)
        except (ValueError, TypeError):
            log(f"⚠️ Risk gate: skipping malformed row {t.get('Symbol', '?')}")
            continue

        # Guard against blank/garbage values inflating risk in a warn-only world.
        if t_sl <= 0 or t_entry <= 0 or t_qty <= 0:
            log(f"⚠️ Risk gate: row {t.get('Symbol', '?')} has "
                f"entry={t_entry} sl={t_sl} qty={t_qty}; skipping from sum")
            continue

        row_risk = (t_entry - t_sl) * t_qty
        if row_risk < 0:
            log(f"⚠️ Risk gate: {t.get('Symbol', '?')} negative risk "
                f"(SL above entry?); skipping")
            continue
        total_open_risk += row_risk

    projected = total_open_risk + new_trade_risk

    log(f"📊 Risk gate: open=₹{total_open_risk:,.0f} "
        f"+ new=₹{new_trade_risk:,.0f} = ₹{projected:,.0f} vs cap ₹{cap:,.0f}")

    if projected > cap:
        msg = (
            f"⚠️ RISK WARNING: {symbol} | "
            f"Open risk ₹{total_open_risk:,.0f} + new risk ₹{new_trade_risk:,.0f} "
            f"= ₹{projected:,.0f} exceeds cap ₹{cap:,.0f} "
            f"({MAX_OPEN_RISK_PCT*100:.0f}% of ₹{CAPITAL:,.0f}). "
            f"Proceeding anyway (advisory)."
        )
        log(msg)
        send_telegram(msg)
    else:
        log(f"✅ Risk gate: within cap (₹{projected:,.0f} ≤ ₹{cap:,.0f})")


# ==========================
# EXECUTE ENTRY ENGINE
# ==========================
def execute_entry_engine_subprocess(payload, token):
    """Execute entry_engine.py with token passed via env var."""
    try:
        required = ["setup_id", "symbol", "qty", "entry", "sl", "target", "score"]
        for field in required:
            if field not in payload or payload[field] is None:
                log(f"❌ Missing field: {field}")
                return False

        env = os.environ.copy()
        webhook_app_path = "/root/trade-execution-webhook/Webhook-app"
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{webhook_app_path}:{existing_pythonpath}"

            if existing_pythonpath

            else webhook_app_path

        )
        env.update({
            "SYMBOL": str(payload["symbol"]),
            "QTY": str(payload["qty"]),
            "ENTRY": str(payload["entry"]),
            "SL": str(payload["sl"]),
            "TARGET": str(payload["target"]),
            "SCORE": str(payload["score"]),
            "SETUP_ID": str(payload["setup_id"]),
            "DHAN_TOKEN": token,
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

        if "✅ ORDER PLACED SUCCESSFULLY" in stdout or "Order placed successfully" in stdout:
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


# ==========================
# WEBHOOK ROUTES
# ==========================

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

        # ==== GET TOKEN (WITH SMART VALIDATION) ====
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

        log(f"✅ {symbol} is clear on Dhan\n")

        # ==== AGGREGATE-RISK GATE (advisory / warn-only — §10) ====
        # Non-blocking: warns via Telegram if cap would be breached, then proceeds.
        check_aggregate_risk(symbol, entry, sl, qty)

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


# ==========================
# HEALTH CHECK
# ==========================

@app.route("/", methods=["GET"])
def home():
    return "Webhook running"


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


# ==========================
# MAIN
# ==========================

if __name__ == "__main__":
    log("=" * 80)
    log("🚀 TELEGRAM WEBHOOK (app.py v10) - AGGREGATE-RISK GATE ADDED")
    log("=" * 80)
    log("✅ Webhook on /webhook")
    log("✅ Smart token validation with /v2/profile")
    log("✅ Regenerates token only when Dhan rejects it")
    log(f"✅ Risk gate (advisory): cap ₹{CAPITAL * MAX_OPEN_RISK_PCT:,.0f} "
        f"({MAX_OPEN_RISK_PCT*100:.0f}% of ₹{CAPITAL:,.0f})")
    log("=" * 80)

    app.run(host="0.0.0.0", port=5000, debug=False)