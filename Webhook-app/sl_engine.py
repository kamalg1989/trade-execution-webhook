# ==============================================
# 🚀 DHAN TRAILING SL ENGINE (FINAL WORKING)
# ==============================================

import os
import requests
import pyotp
import uuid
import time
from datetime import datetime, timezone

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

CURRENT_TOKEN = None
TOKEN_EXPIRY = None


def log(*args):
    print(*args, flush=True)


# ==========================
# TOKEN
# ==========================
def generate_token():
    global TOKEN_EXPIRY

    for _ in range(3):
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
            return data["accessToken"]

        if "2 minutes" in str(data):
            log("⏳ Rate limit → waiting 130 sec")
            time.sleep(130)

    raise Exception("Token failed")


def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > TOKEN_EXPIRY:
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
# FETCH FOREVER ORDERS ✅ FIXED
# ==========================
def fetch_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()},
        timeout=10
    )

    if r.status_code != 200:
        log("❌ Orders fetch failed:", r.text)
        return []

    data = r.json()
    log("📦 ORDERS:", data)

    return data if isinstance(data, list) else []


# ==========================
# LTP API ✅ FIXED
# ==========================
def get_ltp(sec_id, pos=None):

    if pos:
        entry = float(pos.get("buyAvg") or 0)
        pnl = float(pos.get("unrealizedProfit") or 0)
        qty = max(int(pos.get("netQty", 0)), 1)

        if entry > 0:
            ltp = entry + (pnl / qty)
            ltp = round(ltp, 2)

            log(f"🧮 LTP fallback → Entry={entry} PnL={pnl} Qty={qty} LTP={ltp}")

            return ltp

    return None


# ==========================
# SL LOGIC
# ==========================
def calculate_sl(entry, ltp, prev_sl=None):

    base = entry * 0.92

    if ltp <= entry:
        return base

    profit = ltp - entry
    new_sl = entry + (profit * 0.5)

    new_sl = min(new_sl, ltp * 0.995)

    if prev_sl:
        return max(prev_sl, new_sl)

    return new_sl


# ==========================
# ORDER HELPERS
# ==========================
def get_sl_orders(sec_id, orders):
    return [
        o for o in orders
        if str(o.get("securityId")) == str(sec_id)
        and o.get("transactionType") == "SELL"
        and o.get("orderStatus") in ["PENDING", "TRANSIT"]
    ]


def cancel_order(order_id):
    r = requests.delete(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        headers={"access-token": get_token()},
        timeout=10
    )
    log("🗑️ CANCEL:", order_id, r.status_code, r.text)


def cleanup_duplicate_sl(sec_id, orders, ltp):

    sl_orders = get_sl_orders(sec_id, orders)

    if not sl_orders:
        return []

    valid = []

    for o in sl_orders:
        tp = float(o.get("triggerPrice", 0))

        if tp < ltp:
            valid.append(o)
        else:
            log("❌ Removing invalid SL:", tp)
            cancel_order(o["orderId"])

    if len(valid) <= 1:
        return valid

    log(f"⚠️ Duplicate SL found ({len(valid)})")

    valid.sort(key=lambda x: float(x.get("triggerPrice")), reverse=True)

    keep = valid[0]

    for o in valid[1:]:
        cancel_order(o["orderId"])

    log(f"✅ Keeping SL @ {keep.get('triggerPrice')}")

    return [keep]


# ==========================
# MODIFY / PLACE
# ==========================
def modify_sl(order_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger,
        "validity": "DAY"
    }

    log("🔄 MODIFY:", payload)

    r = requests.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers={"access-token": get_token()},
        timeout=10
    )

    log("📉 MODIFY:", r.status_code, r.text)

    return r.status_code == 200


def place_sl(sec_id, qty, trigger):

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": round(trigger * 0.995, 2),
        "triggerPrice": trigger
    }

    log("🆕 NEW SL:", payload)

    r = requests.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={"access-token": get_token()},
        timeout=10
    )

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

        sec_id = pos["securityId"]
        symbol = pos["tradingSymbol"]
        entry = float(pos.get("buyAvg", 0))

        ltp = get_ltp(sec_id)

        if not ltp:
            log(f"⚠️ Missing price → {symbol}")
            continue

        log(f"\n➡️ {symbol} | Entry={entry} | LTP={ltp}")

        sl_orders = cleanup_duplicate_sl(sec_id, orders, ltp)

        prev_sl = None
        order_id = None

        if sl_orders:
            prev_sl = float(sl_orders[0]["triggerPrice"])
            order_id = sl_orders[0]["orderId"]

        new_sl = calculate_sl(entry, ltp, prev_sl)

        log(f"SL OLD={prev_sl} → NEW={new_sl}")

        if prev_sl and new_sl <= prev_sl:
            log("⏭️ No update")
            continue

        if order_id:
            if modify_sl(order_id, qty, new_sl):
                updated += 1
        else:
            if place_sl(sec_id, qty, new_sl):
                updated += 1

    log(f"\n✅ DONE | Updated: {updated}")


if __name__ == "__main__":
    run()
