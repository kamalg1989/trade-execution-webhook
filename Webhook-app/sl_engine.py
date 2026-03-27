# ==============================================
# 🚀 DHAN SL ENGINE (RUN VIA GITHUB ACTIONS)
# ==============================================

import os
import requests
import pyotp
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

    raise Exception("Token generation failed")


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
# CHECK EXISTING SL
# ==========================
def has_exit_order(security_id):
    url = "https://api.dhan.co/v2/orders"

    headers = {
        "access-token": get_token()
    }

    r = requests.get(url, headers=headers, timeout=10)
    orders = r.json()

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

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": f"SL_{security_id}_{int(datetime.now().timestamp())}",
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

    log("SL:", r.status_code, r.text)

# ==========================
# MAIN ENGINE
# ==========================
def run():

    log("🚀 SL ENGINE START")

    url = "https://api.dhan.co/v2/positions"

    headers = {
        "access-token": get_token()
    }

    r = requests.get(url, headers=headers, timeout=10)
    positions = r.json()

    for pos in positions:

        qty = int(pos.get("netQty", 0))
        if qty <= 0:
            continue

        security_id = pos.get("securityId")
        entry = float(pos.get("avgPrice", 0))

        if entry == 0:
            continue

        if has_exit_order(security_id):
            continue

        sl_price = calculate_sl(entry)

        place_sl(security_id, qty, sl_price)

        send_telegram(f"📉 SL PLACED: {security_id} @ {sl_price}")

    log("✅ SL ENGINE DONE")


if __name__ == "__main__":
    run()
