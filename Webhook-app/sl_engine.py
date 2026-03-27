# ==============================================
# 🚀 DHAN SL ENGINE (WITH FULL LOGGING)
# ==============================================

import os
import requests
import pyotp
import uuid
from datetime import datetime, timedelta, timezone

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)

# ==========================
# TOKEN
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

    params = {
        "dhanClientId": DHAN_CLIENT_ID,
        "pin": DHAN_PIN,
        "totp": totp
    }

    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params=params,
        timeout=10
    )

    data = r.json()

    token = data.get("accessToken")
    expiry = data.get("expiryTime")

    if token and expiry:
        TOKEN_EXPIRY = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
        log("✅ TOKEN GENERATED")
        return token

    raise Exception(f"Token failed: {data}")


def is_token_expired():
    if not TOKEN_EXPIRY:
        return True
    return datetime.now(timezone.utc) > (TOKEN_EXPIRY - timedelta(minutes=5))


def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or is_token_expired():
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# TELEGRAM
# ==========================
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# ==========================
# SL CALC
# ==========================
def calculate_sl(price):
    return round(price * 0.92, 2)

# ==========================
# FETCH ORDERS
# ==========================
def fetch_orders():
    url = "https://api.dhan.co/v2/orders"

    headers = {
        "access-token": get_token()
    }

    r = requests.get(url, headers=headers, timeout=10)

    if r.status_code != 200:
        log("❌ Orders fetch failed:", r.text)
        return []

    data = r.json()

    if not isinstance(data, list):
        log("❌ Invalid orders response:", data)
        return []

    return data

# ==========================
# FETCH POSITIONS
# ==========================
def fetch_positions():
    url = "https://api.dhan.co/v2/positions"

    headers = {
        "access-token": get_token()
    }

    r = requests.get(url, headers=headers, timeout=10)

    if r.status_code != 200:
        log("❌ Positions fetch failed:", r.text)
        return []

    data = r.json()

    if not isinstance(data, list):
        log("❌ Invalid positions response:", data)
        return []

    return data

# ==========================
# CHECK EXISTING SL
# ==========================
def has_exit_order(security_id, orders):
    for o in orders:
        if str(o.get("securityId")) == str(security_id):
            if o.get("transactionType") == "SELL":
                if o.get("orderStatus") in ["PENDING", "TRANSIT"]:
                    return True
    return False

# ==========================
# PLACE SL
# ==========================
def place_sl(security_id, qty, sl_price):

    correlation_id = str(uuid.uuid4()).replace("-", "")[:20]

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": correlation_id,
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "STOP_LOSS",
        "validity": "DAY",
        "securityId": security_id,
        "quantity": qty,
        "price": sl_price,
        "triggerPrice": sl_price
    }

    url = "https://api.dhan.co/v2/orders"

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers, timeout=10)

    log("📉 SL ORDER:", security_id, "|", r.status_code, r.text)

# ==========================
# MAIN ENGINE
# ==========================
def run():

    log("\n🚀 SL ENGINE START\n")

    positions = fetch_positions()
    orders = fetch_orders()

    # ==========================
    # PRINT HOLDINGS
    # ==========================
    log("📊 CURRENT HOLDINGS")
    log("----------------------------------")

    for pos in positions:
        qty = int(pos.get("netQty", 0))
        if qty > 0:
            log(
                pos.get("tradingSymbol"),
                "| Qty:", qty,
                "| Entry:", pos.get("avgPrice"),
                "| PnL:", pos.get("unrealizedProfit")
            )

    # ==========================
    # PRINT PENDING ORDERS
    # ==========================
    log("\n📌 PENDING ORDERS")
    log("----------------------------------")

    for o in orders:
        if o.get("orderStatus") in ["PENDING", "TRANSIT"]:
            log(
                o.get("tradingSymbol"),
                "|", o.get("transactionType"),
                "| Qty:", o.get("quantity"),
                "| Price:", o.get("price")
            )

    # ==========================
    # SL LOGIC
    # ==========================
    sl_count = 0
    skip_count = 0

    log("\n⚙️ PROCESSING SL\n")

    for pos in positions:

        qty = int(pos.get("netQty", 0))
        if qty <= 0:
            continue

        security_id = pos.get("securityId")
        entry = float(pos.get("avgPrice", 0))
        symbol = pos.get("tradingSymbol")

        if entry == 0:
            continue

        log(f"➡️ Checking {symbol} | Qty={qty} | Entry={entry}")

        if has_exit_order(security_id, orders):
            log(f"⏭️ SL exists → Skipping {symbol}")
            skip_count += 1
            continue

        sl_price = calculate_sl(entry)

        place_sl(security_id, qty, sl_price)

        send_telegram(f"📉 SL PLACED: {symbol} @ {sl_price}")

        sl_count += 1

    # ==========================
    # SUMMARY
    # ==========================
    log("\n✅ SL ENGINE DONE")

    summary = f"""
📊 SL ENGINE SUMMARY

Total Positions: {len(positions)}
SL Placed: {sl_count}
Skipped: {skip_count}
"""

    log(summary)
    send_telegram(summary)


if __name__ == "__main__":
    run()
