# ==============================================
# 🚀 DHAN TRAILING SL ENGINE (FINAL WORKING)
# ==============================================

import os
import requests
import pyotp
import uuid
import time
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
def log(*args):
    print(*args, flush=True)

# ==========================
# TOKEN (RATE LIMIT SAFE)
# ==========================
def generate_token():

    clean_secret = (DHAN_TOTP_SECRET or "").strip().replace(" ", "")

    while True:
        totp = pyotp.TOTP(clean_secret).now()

        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=20
        )

        data = r.json()
        log("🔍 TOKEN:", data)

        if data.get("accessToken"):
            global TOKEN_EXPIRY
            TOKEN_EXPIRY = datetime.fromisoformat(data["expiryTime"]).replace(tzinfo=timezone.utc)
            return data["accessToken"]

        if "2 minutes" in str(data):
            log("⏳ Waiting 130 sec due to rate limit")
            time.sleep(130)

# ==========================
def is_token_expired():
    if not TOKEN_EXPIRY:
        return True
    return datetime.now(timezone.utc) > (TOKEN_EXPIRY - timedelta(minutes=5))

def get_token():
    global CURRENT_TOKEN
    if CURRENT_TOKEN and not is_token_expired():
        return CURRENT_TOKEN
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
# LTP (FIXED)
# ==========================
def get_ltp(security_id):

    url = "https://api.dhan.co/v2/marketfeed/ltp"

    payload = [
        {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ"
        }
    ]

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers, timeout=10)
    data = r.json()

    log("📡 LTP:", data)

    try:
        return float(list(data["data"].values())[0]["lastPrice"])
    except:
        return 0

# ==========================
# FETCH POSITIONS
# ==========================
def fetch_positions():

    url = "https://api.dhan.co/v2/positions"

    headers = {"access-token": get_token()}

    r = requests.get(url, headers=headers, timeout=15)
    data = r.json()

    log("📊 POSITIONS:", data)

    return data if isinstance(data, list) else []

# ==========================
# FETCH FOREVER ORDERS (FIXED)
# ==========================
def fetch_orders():

    url = "https://api.dhan.co/v2/forever/orders"

    headers = {"access-token": get_token()}

    r = requests.get(url, headers=headers, timeout=15)

    if r.status_code != 200:
        log("❌ Orders error:", r.text)
        return []

    data = r.json()
    log("📦 ORDERS:", data)

    return data if isinstance(data, list) else []

# ==========================
# EXISTING SL
# ==========================
def get_existing_sl(security_id, orders):

    for o in orders:
        if str(o.get("securityId")) == str(security_id):
            if o.get("transactionType") == "SELL":
                return {
                    "orderId": o.get("orderId"),
                    "triggerPrice": float(o.get("triggerPrice", 0))
                }
    return None

# ==========================
# TRAILING LOGIC
# ==========================
def calculate_sl(entry, ltp, prev_sl=None):

    base = entry * 0.92

    if ltp < entry * 1.02:
        return base

    sl = entry

    if ltp >= entry * 1.03:
        sl = ltp * 0.98

    if ltp >= entry * 1.05:
        sl = ltp * 0.99

    if prev_sl:
        return max(prev_sl, sl)

    return sl

# ==========================
# MODIFY SL
# ==========================
def modify_sl(order_id, qty, sl):

    payload = {
        "orderId": order_id,
        "orderType": "LIMIT",
        "price": round(sl * 0.995, 2),
        "triggerPrice": sl,
        "quantity": qty
    }

    url = "https://api.dhan.co/v2/orders/modify"

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.put(url, json=payload, headers=headers, timeout=10)

    log("🔁 MODIFY:", r.status_code, r.text)

    return r.status_code == 200

# ==========================
# PLACE NEW SL
# ==========================
def place_sl(security_id, qty, sl):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": security_id,
        "quantity": qty,
        "price": round(sl * 0.995, 2),
        "triggerPrice": sl
    }

    url = "https://api.dhan.co/v2/forever/orders"

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers, timeout=10)

    log("📉 NEW SL:", r.status_code, r.text)

    return r.status_code == 200

# ==========================
# MAIN
# ==========================
def run():

    log("\n🚀 TRAILING SL ENGINE START\n")

    positions = fetch_positions()
    orders = fetch_orders()

    updated = 0

    for pos in positions:

        qty = int(pos.get("netQty", 0))
        if qty <= 0:
            continue

        sec_id = pos.get("securityId")
        symbol = pos.get("tradingSymbol")

        entry = float(pos.get("buyAvg") or 0)
        ltp = get_ltp(sec_id)

        if entry == 0 or ltp == 0:
            log(f"⚠️ Missing price → {symbol}")
            continue

        existing = get_existing_sl(sec_id, orders)
        prev_sl = existing["triggerPrice"] if existing else None

        new_sl = calculate_sl(entry, ltp, prev_sl)

        log(f"{symbol} | Entry={entry} | LTP={ltp} | OLD={prev_sl} | NEW={new_sl}")

        if prev_sl and new_sl <= prev_sl:
            continue

        if existing:
            success = modify_sl(existing["orderId"], qty, new_sl)
        else:
            success = place_sl(sec_id, qty, new_sl)

        if success:
            updated += 1
            send_telegram(f"📈 SL UPDATED: {symbol} → {round(new_sl,2)}")

    log(f"\n✅ DONE | Updated: {updated}")

# ==========================
if __name__ == "__main__":
    run()
