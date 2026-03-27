# ==============================================
# 🚀 DHAN TRAILING SL ENGINE (FINAL STABLE)
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

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

# ==========================
# LOGGER
# ==========================
def log(*args):
    print(*args, flush=True)

# ==========================
# TOKEN (RATE LIMIT SAFE)
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    for i in range(3):
        log("🔐 Generating token...")

        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=10
        )

        data = r.json()
        log("🔍 TOKEN:", data)

        if "accessToken" in data:
            TOKEN_EXPIRY = datetime.fromisoformat(data["expiryTime"]).replace(tzinfo=timezone.utc)
            log("✅ TOKEN GENERATED")
            return data["accessToken"]

        if "2 minutes" in str(data):
            log("⏳ Waiting due to rate limit...")
            time.sleep(130)

    raise Exception("❌ Token generation failed")

def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > (TOKEN_EXPIRY - timedelta(minutes=5)):
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ==========================
# FETCH POSITIONS
# ==========================
def fetch_positions():
    r = requests.get(
        "https://api.dhan.co/v2/positions",
        headers={"access-token": get_token()},
        timeout=10
    )
    data = r.json()
    log("📊 POSITIONS:", data)
    return data if isinstance(data, list) else []

# ==========================
# FETCH FOREVER ORDERS (FIXED)
# ==========================
def fetch_forever_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/all",
        headers={"access-token": get_token()},
        timeout=10
    )

    if r.status_code != 200:
        log("❌ Orders fetch failed:", r.text)
        return []

    data = r.json()
    log("📦 FOREVER ORDERS:", data)

    return data if isinstance(data, list) else []

# ==========================
# FIND EXISTING SL ORDERS
# ==========================
def get_sl_orders(security_id, orders):
    return [
        o for o in orders
        if str(o.get("securityId")) == str(security_id)
        and o.get("transactionType") == "SELL"
        and o.get("orderStatus") in ["PENDING", "TRANSIT"]
    ]

# ==========================
# CANCEL ORDER
# ==========================
def cancel_order(order_id):
    url = f"https://api.dhan.co/v2/forever/orders/{order_id}"

    r = requests.delete(
        url,
        headers={"access-token": get_token()},
        timeout=10
    )

    log("🗑️ CANCEL:", order_id, r.status_code, r.text)

# ==========================
# MODIFY SL
# ==========================
def modify_sl(order, qty, trigger):

    order_id = order["orderId"]
    limit = round(trigger * 0.995, 2)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "TARGET_LEG",
        "quantity": qty,
        "price": limit,
        "triggerPrice": trigger,
        "validity": "DAY"
    }

    url = f"https://api.dhan.co/v2/forever/orders/{order_id}"

    log("🔁 MODIFY SL:", payload)

    r = requests.put(
        url,
        json=payload,
        headers={
            "access-token": get_token(),
            "Content-Type": "application/json"
        },
        timeout=10
    )

    log("📉 MODIFY RESPONSE:", r.status_code, r.text)

# ==========================
# PLACE NEW SL
# ==========================
def place_sl(security_id, qty, trigger):

    limit = round(trigger * 0.995, 2)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
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

    log("🆕 NEW SL:", payload)

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={
            "access-token": get_token(),
            "Content-Type": "application/json"
        },
        timeout=10
    )

    log("📉 NEW SL RESPONSE:", r.status_code, r.text)

# ==========================
# TRAILING LOGIC
# ==========================
def run():

    log("\n🚀 TRAILING SL ENGINE START\n")

    positions = fetch_positions()
    orders = fetch_forever_orders()

    updated = 0

    for pos in positions:

        qty = int(pos.get("netQty", 0))
        if qty <= 0:
            continue

        symbol = pos.get("tradingSymbol")
        sec_id = pos.get("securityId")
        entry = float(pos.get("buyAvg") or 0)

        pnl = float(pos.get("unrealizedProfit", 0))
        current = entry + (pnl / qty) if qty else entry

        log(f"\n➡️ {symbol} | Entry={entry} | LTP≈{current}")

        # Trailing SL: 50% lock
        new_sl = round(entry + (current - entry) * 0.5, 2)

        sl_orders = get_sl_orders(sec_id, orders)

        # cleanup duplicates
        if len(sl_orders) > 1:
            log("⚠️ Duplicate SL found → cleaning")
            for o in sl_orders[1:]:
                cancel_order(o["orderId"])

        if not sl_orders:
            place_sl(sec_id, qty, new_sl)
            updated += 1
            continue

        existing = float(sl_orders[0].get("triggerPrice", 0))

        log(f"Current SL={existing} → New SL={new_sl}")

        if new_sl > existing:
            modify_sl(sl_orders[0], qty, new_sl)
            updated += 1
        else:
            log("⏭️ No update needed")

    log(f"\n✅ DONE | Updated: {updated}")

# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    run()
