# ==============================================
# 🚀 DHAN TRAILING SL ENGINE (FINAL + DEBUG)
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
# TOKEN (STABLE)
# ==========================
def generate_token():

    clean_secret = (DHAN_TOTP_SECRET or "").strip().replace(" ", "")

    while True:
        try:
            totp = pyotp.TOTP(clean_secret).now()
            log("🔐 Generating token...")

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
            log("🔍 TOKEN RESPONSE:", data)

            if data.get("accessToken"):
                expiry = data.get("expiryTime")

                global TOKEN_EXPIRY
                TOKEN_EXPIRY = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)

                log("✅ TOKEN GENERATED")
                return data["accessToken"]

            if "2 minutes" in str(data):
                log("⏳ Rate limit → wait 130 sec")
                time.sleep(130)

        except Exception as e:
            log("⚠️ Token error:", e)
            time.sleep(10)

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
# FETCH LTP (🔥 FIX)
# ==========================
def get_ltp(security_id):

    url = "https://api.dhan.co/v2/marketfeed/ltp"

    payload = {
        "securityId": [security_id],
        "exchangeSegment": "NSE_EQ"
    }

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        data = r.json()

        log("📡 LTP RESPONSE:", data)

        if isinstance(data, dict):
            return float(list(data.values())[0].get("lastPrice", 0))

    except Exception as e:
        log("❌ LTP error:", e)

    return 0

# ==========================
# TRAILING SL LOGIC
# ==========================
def calculate_trailing_sl(entry, ltp, prev_sl=None):

    base_sl = round(entry * 0.92, 2)

    if ltp < entry * 1.02:
        return base_sl

    new_sl = entry

    if ltp >= entry * 1.03:
        new_sl = round(ltp * 0.98, 2)

    if ltp >= entry * 1.05:
        new_sl = round(ltp * 0.99, 2)

    if prev_sl:
        return max(prev_sl, new_sl)

    return new_sl

# ==========================
# FETCH POSITIONS
# ==========================
def fetch_positions():

    url = "https://api.dhan.co/v2/positions"

    headers = {"access-token": get_token()}

    r = requests.get(url, headers=headers, timeout=15)

    if r.status_code != 200:
        log("❌ Positions fetch failed:", r.text)
        return []

    data = r.json()

    log("📊 RAW POSITIONS:", data)

    return data if isinstance(data, list) else []

# ==========================
# FETCH ORDERS
# ==========================
def fetch_orders():

    url = "https://api.dhan.co/v2/orders"

    headers = {"access-token": get_token()}

    r = requests.get(url, headers=headers, timeout=15)

    if r.status_code != 200:
        log("❌ Orders fetch failed:", r.text)
        return []

    data = r.json()

    log("📦 RAW ORDERS:", data)

    if isinstance(data, dict):
        data = data.get("data", [])

    return data if isinstance(data, list) else []

# ==========================
# EXISTING SL
# ==========================
def get_existing_sl(security_id, orders):

    for o in orders:
        if str(o.get("securityId")) == str(security_id):
            if o.get("transactionType") == "SELL" and o.get("orderStatus") in ["PENDING", "TRANSIT"]:
                return {
                    "orderId": o.get("orderId"),
                    "triggerPrice": float(o.get("triggerPrice", 0))
                }
    return None

# ==========================
# MODIFY SL
# ==========================
def modify_sl(order_id, qty, sl):

    trigger = sl
    limit = round(sl * 0.995, 2)

    payload = {
        "orderId": order_id,
        "orderType": "LIMIT",
        "price": limit,
        "triggerPrice": trigger,
        "quantity": qty
    }

    url = "https://api.dhan.co/v2/orders/modify"

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.put(url, json=payload, headers=headers, timeout=15)

    log("🔁 MODIFY:", r.status_code, r.text)

    return r.status_code == 200

# ==========================
# PLACE NEW SL
# ==========================
def place_sl(security_id, qty, sl):

    trigger = sl
    limit = round(sl * 0.995, 2)

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
        "price": limit,
        "triggerPrice": trigger
    }

    url = "https://api.dhan.co/v2/forever/orders"

    headers = {
        "access-token": get_token(),
        "Content-Type": "application/json"
    }

    r = requests.post(url, json=payload, headers=headers, timeout=15)

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
    skipped = 0

    for pos in positions:

        qty = int(pos.get("netQty", 0))
        if qty <= 0:
            continue

        security_id = pos.get("securityId")
        symbol = pos.get("tradingSymbol")

        entry = float(pos.get("avgPrice") or 0)

        # 🔥 FIX: fetch LTP separately
        ltp = get_ltp(security_id)

        if entry == 0 or ltp == 0:
            log(f"⚠️ Missing price → {symbol}")
            continue

        existing = get_existing_sl(security_id, orders)
        prev_sl = existing["triggerPrice"] if existing else None

        new_sl = calculate_trailing_sl(entry, ltp, prev_sl)

        log(f"{symbol} | Entry={entry} | LTP={ltp} | OLD SL={prev_sl} | NEW SL={new_sl}")

        if prev_sl and new_sl <= prev_sl:
            skipped += 1
            continue

        if existing:
            success = modify_sl(existing["orderId"], qty, new_sl)
        else:
            success = place_sl(security_id, qty, new_sl)

        if success:
            updated += 1
            send_telegram(f"📈 SL UPDATED: {symbol} → {new_sl}")

    log("\n✅ ENGINE DONE")

    summary = f"Updated: {updated} | Skipped: {skipped}"
    log(summary)
    send_telegram(summary)

# ==========================
if __name__ == "__main__":
    run()
