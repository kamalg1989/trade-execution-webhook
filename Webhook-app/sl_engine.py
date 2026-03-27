# ==============================================
# 🚀 TRAILING SL ENGINE (DEDUP + FIXED)
# ==============================================

import os, requests, pyotp, uuid, time
from datetime import datetime, timedelta, timezone

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")

CURRENT_TOKEN = None
TOKEN_EXPIRY = None

def log(*args):
    print(*args, flush=True)

# ================= TOKEN =================
def generate_token():
    global TOKEN_EXPIRY

    while True:
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
            log("⏳ Waiting due to rate limit...")
            time.sleep(130)

def get_token():
    global CURRENT_TOKEN
    if not CURRENT_TOKEN or datetime.now(timezone.utc) > (TOKEN_EXPIRY - timedelta(minutes=5)):
        CURRENT_TOKEN = generate_token()
    return CURRENT_TOKEN

# ================= FETCH =================
def fetch_positions():
    r = requests.get(
        "https://api.dhan.co/v2/positions",
        headers={"access-token": get_token()}
    )
    return r.json()

def fetch_orders():
    r = requests.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": get_token()}
    )
    data = r.json()
    log("📦 ORDERS:", data)
    return data if isinstance(data, list) else []

# ================= SL HELPERS =================
def get_sl_orders(sec_id, orders):
    return [
        o for o in orders
        if str(o.get("securityId")) == str(sec_id)
        and o.get("transactionType") == "SELL"
        and o.get("orderStatus") in ["PENDING", "TRANSIT"]
    ]

# ================= CANCEL =================
def cancel_order(order_id):

    url = f"https://api.dhan.co/v2/forever/orders/{order_id}"

    r = requests.delete(
        url,
        headers={"access-token": get_token()},
        timeout=10
    )

    log(f"🗑️ CANCELLED: {order_id} | {r.status_code} | {r.text}")

# ================= DEDUP =================
def cleanup_duplicate_sl(sec_id, orders):

    sl_orders = get_sl_orders(sec_id, orders)

    if len(sl_orders) <= 1:
        return sl_orders

    log(f"⚠️ Duplicate SL found ({len(sl_orders)}) → cleaning")

    # sort by best SL (highest trigger)
    sl_orders.sort(key=lambda x: float(x.get("triggerPrice", 0)), reverse=True)

    keep = sl_orders[0]

    for o in sl_orders[1:]:
        cancel_order(o["orderId"])

    log(f"✅ Keeping SL @ {keep.get('triggerPrice')}")

    return [keep]

# ================= TRAILING =================
def calculate_sl(entry, ltp, prev_sl=None):

    base = entry * 0.92

    if ltp <= entry:
        return base

    profit = ltp - entry

    new_sl = entry + (profit * 0.5)

    # ensure below LTP
   
