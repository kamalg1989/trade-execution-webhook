# ==============================================
# 🚀 TELEGRAM WEBHOOK (app.py) — FINAL CORRECTED
# Receives Telegram button clicks, validates, calls entry_engine.py
# ==============================================

import os
import requests
import pandas as pd
from flask import Flask, request
import threading
import time
import subprocess
import json

# ==========================
# GLOBAL DEDUP STORAGE (Thread-safe)
# ==========================
PROCESSED_CALLBACKS = set()
PROCESSED_ORDERS = {}
LOCK = threading.Lock()
ORDER_WINDOW = 300  # 5 minutes


# ==========================
# CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Paths (adjust to your setup)
ENTRY_ENGINE_PATH = "/root/trade-execution-webhook/Webhook-app/entry_engine.py"
PROJECT_ROOT = "/root/trade-execution-webhook"
VENV_PYTHON = "/root/trade-execution-webhook/venv/bin/python"


# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)


# ==========================
# LOAD INSTRUMENTS (for symbol validation)
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
# TELEGRAM SEND
# ==========================
def send_telegram(msg):
    """Send message to user via Telegram."""
    try:
        log(f"📨 SENDING TELEGRAM: {msg[:100]}...")
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
        return True  # Allow anyway

    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]
    return not row.empty


# ==========================
# SUBPROCESS EXECUTION
# ==========================
def execute_entry_engine_subprocess(payload):
    """
    Execute entry_engine.py as subprocess on the VPS.

    This ensures:
    ✅ API calls originate from VPS static IP (Dhan whitelisting)
    ✅ Entry engine runs in isolated process
    ✅ Clear separation: webhook validation vs order placement

    Payload should have:
    - setup_id, symbol, qty, entry, sl, target, score
    """

    try:
        # Validate required fields
        required = ["setup_id", "symbol", "qty", "entry", "sl", "target", "score"]
        for field in required:
            if field not in payload or payload[field] is None:
                log(f"❌ Missing field in payload: {field}")
                return False

        # Prepare environment (passed to subprocess)
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
        log(f"   Python: {VENV_PYTHON}")
        log(f"   Script: {ENTRY_ENGINE_PATH}")
        log(f"   CWD: {PROJECT_ROOT}")
        log(f"   Payload: {json.dumps(payload, indent=2)}")

        # Execute entry_engine.py with env vars
        result = subprocess.run(
            [VENV_PYTHON, ENTRY_ENGINE_PATH],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout
        )

        log("📤 SUBPROCESS STDOUT:")
        log(result.stdout)

        if result.stderr:
            log("📤 SUBPROCESS STDERR:")
            log(result.stderr)

        stdout = result.stdout or ""

        log(f"🔍 STDOUT length: {len(stdout)}")

        # --- Preferred: JSON-based success detection ---
        try:
            last_line = stdout.strip().split("\n")[-1]
            parsed = json.loads(last_line)

            if parsed.get("success"):
                log(f"✅ Order success confirmed via JSON: {parsed.get('order_id')}")
                return True
        except Exception as e:
            log(f"⚠️ JSON parse failed: {e}")

        # --- Fallback: string-based detection ---
        if "Order placed successfully" in stdout:
            log("✅ Order success detected via stdout string match")
            return True

        log("❌ Order NOT confirmed from entry engine output")

        if result.returncode != 0:
            log(f"❌ Subprocess failed with code: {result.returncode}")

        return False

    except subprocess.TimeoutExpired:
        log("❌ Entry engine subprocess timeout (30s exceeded)")
        return False
    except Exception as e:
        log(f"❌ Error executing entry engine subprocess: {e}")
        return False


# ==========================
# FLASK APP
# ==========================
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Receives Telegram callback when user clicks "✅ Confirm Buy" button.

    Callback format: BUY|setup_id|symbol|qty|entry|sl|target|score
    """

    try:
        data = request.get_json(force=True)
        log("=" * 70)
        log("📩 TELEGRAM WEBHOOK RECEIVED")
        log("=" * 70)
        log(json.dumps(data, indent=2))

        # Check for callback_query
        if not data or "callback_query" not in data:
            log("⚠️ No callback_query in payload")
            return "OK"

        query = data["callback_query"]
        callback_id = query["id"]

        # ✅ CALLBACK DEDUP (Telegram may send duplicate callbacks)
        with LOCK:
            if callback_id in PROCESSED_CALLBACKS:
                log(f"⚠️ Duplicate callback ignored: {callback_id}")
                return "OK"
            PROCESSED_CALLBACKS.add(callback_id)

        # Parse callback data
        raw_callback = query.get("data", "")
        log(f"📋 RAW CALLBACK: {raw_callback}")

        parts = raw_callback.split("|")

        # Expected format: BUY|setup_id|symbol|qty|entry|sl|target|score
        if len(parts) < 8:
            log(f"❌ Invalid callback format (expected 8 parts, got {len(parts)})")
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

        # Base stage (optional, index 8)
        base_stage = safe_int(parts[8]) if len(parts) > 8 else 0
        base_quality_score = safe_float(parts[9]) if len(parts) > 9 else 0.0
        tick_size = safe_float(parts[10]) if len(parts) > 10 else 0.05

        log("✅ PARSED CALLBACK:")
        log(f"   Action: {action}")
        log(f"   Setup ID: {setup_id}")
        log(f"   Symbol: {symbol}")
        log(f"   Qty: {qty}")
        log(f"   Entry: {entry}")
        log(f"   SL: {sl}")
        log(f"   Target: {target}")
        log(f"   Score: {score}")
        log(f"   Base Stage: {base_stage}")
        log(f"   Base Quality: {base_quality_score}")
        log(f"   Tick Size: {tick_size}")

        # ==== VALIDATION (before calling entry engine) ====

        # 1. Setup ID
        if not setup_id:
            log(f"❌ VALIDATION: Missing setup_id")
            send_telegram(f"❌ Missing setup_id")
            return "OK"

        # 2. Quantities
        if qty is None or qty <= 0:
            log(f"❌ VALIDATION: Invalid qty={qty}")
            send_telegram(f"❌ Invalid qty: {qty}")
            return "OK"

        # 3. Prices
        if entry is None or entry <= 0:
            log(f"❌ VALIDATION: Invalid entry={entry}")
            send_telegram(f"❌ Invalid entry price: {entry}")
            return "OK"

        if sl is None or sl <= 0:
            log(f"❌ VALIDATION: Invalid sl={sl}")
            send_telegram(f"❌ Invalid SL: {sl}")
            return "OK"

        if target is None or target <= 0:
            log(f"❌ VALIDATION: Invalid target={target}")
            send_telegram(f"❌ Invalid target: {target}")
            return "OK"

        # 4. Price order validation (CRITICAL)
        if not (sl < entry < target):
            log(f"❌ VALIDATION: Invalid price order - SL={sl} ENTRY={entry} TARGET={target}")
            send_telegram(f"❌ Invalid price order: SL={sl} < ENTRY={entry} < TARGET={target}")
            return "OK"

        # 5. Symbol validation
        if not validate_symbol(symbol):
            log(f"❌ VALIDATION: Symbol not found in Dhan: {symbol}")
            send_telegram(f"❌ Symbol not found: {symbol}")
            return "OK"

        # 6. Action check
        if action != "BUY":
            log(f"❌ VALIDATION: Unsupported action={action}")
            send_telegram(f"❌ Unsupported action: {action}")
            return "OK"

        # 7. Order dedup (check if we already processed this setup_id recently)
        key = setup_id
        now = time.time()

        with LOCK:
            if key in PROCESSED_ORDERS:
                time_since_last = now - PROCESSED_ORDERS[key]
                if time_since_last < ORDER_WINDOW:
                    log(f"⚠️ DEDUP: Order {key} already processed {time_since_last:.1f}s ago")
                    send_telegram(f"⚠️ Order already processed for {symbol}")
                    return "OK"

            PROCESSED_ORDERS[key] = now

        # ==== ALL VALIDATIONS PASSED ====
        log("✅ ALL VALIDATIONS PASSED")

        # Prepare payload for entry engine
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

        # Send immediate ack to user
        send_telegram(f"⏳ Processing order for {symbol} | Qty: {qty}")

        # 🚀 Execute entry engine as subprocess
        # This will place the order on Dhan and save to DB
        success = execute_entry_engine_subprocess(payload)

        # Notify user of result
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
    """Health check."""
    return "Webhook running - ready to accept Telegram callbacks"


@app.route("/health", methods=["GET"])
def health():
    """Health endpoint for monitoring."""
    return {"status": "ok", "timestamp": str(pd.Timestamp.now())}, 200


if __name__ == "__main__":
    log("=" * 70)
    log("🚀 TELEGRAM WEBHOOK (app.py) STARTING")
    log("=" * 70)
    log(f"ENTRY_ENGINE_PATH: {ENTRY_ENGINE_PATH}")
    log(f"PROJECT_ROOT: {PROJECT_ROOT}")
    log(f"VENV_PYTHON: {VENV_PYTHON}")
    log(f"TELEGRAM_TOKEN: {TELEGRAM_TOKEN[:20]}...")
    log(f"CHAT_ID: {CHAT_ID}")

    # Run Flask app
    # In production, use: gunicorn -w 4 -b 0.0.0.0:5000 app:app
    app.run(host="0.0.0.0", port=5000, debug=False)