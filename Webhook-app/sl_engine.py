# ==============================================
# 🚀 SL ENGINE V13 (GOOGLE SHEETS INTEGRATED)
# - READ SL_Price FROM GOOGLE SHEETS
# - CHECK CLOSE vs SL_Price
# - MODIFY FOREVER ORDERS FOR EXIT
# - INSERT MISSING STOCKS
# ==============================================

import os
import requests
import pyotp
import logging
import time
import uuid
import yfinance as yf
import gspread
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

# ==========================
# LOAD ENV (CRITICAL FIX)
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_PATHS = [
    os.path.join(BASE_DIR, ".env"),                                # local
    "/root/trade-execution-webhook/.env",                          # VPS root
    os.path.expanduser("~/.env"),                                  # home directory
]

env_loaded = False
for path in ENV_PATHS:
    if os.path.exists(path):
        load_dotenv(path)
        print(f"✅ Loaded .env from: {path}")
        env_loaded = True
        break

if not env_loaded:
    print("⚠️ WARNING: .env NOT FOUND - using environment variables")

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH")

BASE_SL_PCT = 0.92              # 8% initial SL
TRAIL_PROFIT_LOCK = 0.5         # Lock 50% of profit
MIN_LTP_BUFFER = 0.05           # Maintain 5% gap from LTP

session = requests.Session()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# ==========================
# ENV VALIDATION (CRITICAL)
# ==========================
def validate_env():
    missing = []

    if not DHAN_CLIENT_ID:
        missing.append("DHAN_CLIENT_ID")
    if not DHAN_PIN:
        missing.append("DHAN_PIN")
    if not DHAN_TOTP_SECRET:
        missing.append("DHAN_TOTP_SECRET")
    if not SPREADSHEET_ID:
        missing.append("SPREADSHEET_ID")
    if not SERVICE_ACCOUNT_KEY_PATH:
        missing.append("SERVICE_ACCOUNT_KEY_PATH")

    if missing:
        raise ValueError(f"❌ Missing ENV: {', '.join(missing)}")

    logger.info(f"✅ ENV OK | CLIENT_ID={DHAN_CLIENT_ID}")

# ==========================
# GOOGLE SHEETS INIT
# ==========================
def init_google_sheets():
    """Initialize Google Sheets connection"""
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]

        if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
            logger.error(f"❌ Key file not found: {SERVICE_ACCOUNT_KEY_PATH}")
            return None

        credentials = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_KEY_PATH,
            scopes=scopes
        )

        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)

        logger.info(f"✅ Google Sheets connected: {spreadsheet.title}")

        # Get or create Trades worksheet
        try:
            worksheet = spreadsheet.worksheet("Trades")
            logger.info(f"✅ Using worksheet: Trades")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"⚠️ Worksheet 'Trades' not found, creating...")
            worksheet = spreadsheet.add_worksheet(title="Trades", rows=1000, cols=15)

            # Add headers
            headers = [
                "ID", "Symbol", "Security_ID", "Qty", "Entry_Price",
                "Entry_Time", "Status", "SL_Price", "Target_Price",
                "Setup_ID", "Current_Price", "PnL", "PnL_Percent",
                "Updated_At", "Dhan_Order_ID"
            ]
            worksheet.insert_row(headers, 1)
            logger.info(f"✅ Created worksheet with headers")

        return worksheet

    except Exception as e:
        logger.error(f"❌ Google Sheets init failed: {e}")
        return None

# ==========================
# GET ALL TRADES FROM SHEETS
# ==========================
def get_trades_from_sheets(worksheet):
    """Get all trades from Google Sheets"""
    try:
        records = worksheet.get_all_records()
        logger.info(f"✅ Retrieved {len(records)} trades from Google Sheets")
        return records
    except Exception as e:
        logger.error(f"❌ Failed to get trades: {e}")
        return []

# ==========================
# INSERT MISSING STOCK
# ==========================
def insert_missing_stock(worksheet, security_id, symbol, qty, avg_price):
    """Insert a new stock position that's missing from Google Sheets"""
    try:
        # Check if already exists
        records = worksheet.get_all_records()
        if any(r.get("Symbol") == symbol and r.get("Security_ID") == str(security_id) for r in records):
            logger.info(f"ℹ️ Stock {symbol} already in sheets")
            return True

        # New row data
        new_id = str(int(time.time() * 1000))[:10]
        now = datetime.now(timezone.utc).isoformat()

        row = [
            new_id,              # ID
            symbol,              # Symbol
            str(security_id),    # Security_ID
            str(qty),            # Qty
            str(avg_price),      # Entry_Price
            now,                 # Entry_Time
            "OPEN",              # Status
            "",                  # SL_Price (blank - user fills)
            "",                  # Target_Price (blank - user fills)
            "",                  # Setup_ID (blank)
            str(avg_price),      # Current_Price
            "0",                 # PnL
            "0",                 # PnL_Percent
            now,                 # Updated_At
            ""                   # Dhan_Order_ID (blank)
        ]

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"✅ Inserted missing stock: {symbol} (Qty: {qty})")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to insert stock {symbol}: {e}")
        return False

# ==========================
# TOKEN
# ==========================
def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    try:
        if not DHAN_TOTP_SECRET:
            raise ValueError("TOTP secret missing")

        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()

        logger.info("🔑 Generating token...")

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=10
        )

        logger.info(f"🔐 Token status: {r.status_code}")

        data = r.json()

        if "accessToken" not in data:
            logger.error(f"❌ Token failed: {data}")
            return None

        CURRENT_TOKEN = data["accessToken"]
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)

        logger.info("✅ Token generated")
        return CURRENT_TOKEN

    except Exception as e:
        logger.error(f"❌ Token error: {e}")
        return None

# ==========================
# POSITIONS
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return []

    r = session.get(
        "https://api.dhan.co/v2/positions",
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        }
    )

    logger.info(f"📡 Positions status: {r.status_code}")

    data = r.json()
    result = []

    for p in data:
        if p.get("netQty", 0) > 0:
            avg = p.get("buyAvg") or p.get("costPrice")

            result.append({
                "securityId": str(p["securityId"]),
                "symbol": p["tradingSymbol"],
                "qty": p["netQty"],
                "avgPrice": avg
            })

    logger.info(f"📊 Positions: {len(result)}")
    return result

# ==========================
# HOLDINGS
# ==========================
def get_holdings():
    token = get_token()
    if not token:
        return []

    r = session.get(
        "https://api.dhan.co/v2/holdings",
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        }
    )

    logger.info(f"📡 Holdings status: {r.status_code}")

    data = r.json()
    result = []

    for h in data:
        qty = h.get("totalQty", 0)

        if qty > 0:
            result.append({
                "securityId": str(h["securityId"]),
                "symbol": h["tradingSymbol"],
                "qty": qty,
                "avgPrice": h.get("avgCostPrice")
            })

    logger.info(f"📊 Holdings: {len(result)}")
    return result

# ==========================
# FOREVER ORDERS
# ==========================
def get_forever_orders():
    token = get_token()
    if not token:
        return []

    r = session.get(
        "https://api.dhan.co/v2/forever/orders",
        headers={"access-token": token}
    )

    logger.info(f"📡 Forever status: {r.status_code}")

    data = r.json()
    logger.info(f"📊 Forever count: {len(data) if isinstance(data, list) else 'INVALID'}")

    return data if isinstance(data, list) else []

# ==========================
# FETCH LTP USING YFINANCE
# ==========================
def get_ltp(symbol):
    """Fetch Last Traded Price using yfinance"""
    try:
        ticker = yf.Ticker(symbol + ".NS")
        ltp = ticker.fast_info["lastPrice"]
        logger.info(f"📊 {symbol} LTP: {ltp}")
        return ltp
    except Exception as e:
        logger.warning(f"⚠️ LTP fetch failed for {symbol}: {e}")
        return None

# ==========================
# FETCH CLOSE PRICE
# ==========================
def get_close_price(symbol):
    """Fetch yesterday's close price (for end-of-day checking)"""
    try:
        ticker = yf.Ticker(symbol + ".NS")
        # Get last 1 day of history
        hist = ticker.history(period="1d")
        if not hist.empty:
            close = hist['Close'].iloc[-1]
            logger.info(f"📊 {symbol} Close: {close}")
            return close
        return None
    except Exception as e:
        logger.warning(f"⚠️ Close price fetch failed for {symbol}: {e}")
        return None

# ==========================
# SL CALCULATION LOGIC
# ==========================
def calculate_sl(entry, ltp, current_sl):
    """Calculate trailing stop-loss with minimum 5% buffer from LTP"""
    base_sl = entry * BASE_SL_PCT
    new_sl = max(current_sl or 0, base_sl)

    if ltp > entry:
        profit = ltp - entry
        trailing_sl = entry + (profit * TRAIL_PROFIT_LOCK)
        max_allowed_sl = ltp * (1 - MIN_LTP_BUFFER)
        new_sl = max(new_sl, min(trailing_sl, max_allowed_sl))

    return round(new_sl, 2)

# ==========================
# MODIFY SL ORDER (EXIT)
# ==========================
def modify_sl_for_exit(order_id, qty, symbol):
    """Modify SL order to exit at market close price (set trigger to 0.01)"""
    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for exit on {symbol}")
        return False

    # Set a very low trigger to ensure exit (market will hit this)
    trigger = 0.01
    price = 0.01

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "MARKET",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "validity": "DAY"
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token
    }

    logger.info(f"🔴 EXITING {symbol} (Close < SL_Price)")

    r = session.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers=headers,
        timeout=15
    )

    logger.info(f"📡 Exit order status ({symbol}): {r.status_code}")

    if r.status_code not in (200, 201):
        logger.error(f"❌ Exit order FAILED for {symbol}: {r.text}")
        return False

    logger.info(f"✅ Exit order placed for {symbol}")
    return True

# ==========================
# PLACE SL
# ==========================
def place_sl(sec_id, qty, avg, symbol):
    """Place initial stop-loss order"""
    if not avg:
        logger.error(f"❌ Invalid avg price for {sec_id}")
        return False

    trigger = calculate_sl(avg, avg, None)
    price = round(trigger * 0.995, 2)

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
        "price": price,
        "triggerPrice": trigger
    }

    logger.info(f"📤 Placing SL → {symbol} | trigger={trigger} | price={price}")

    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for SL on {symbol}")
        return False

    r = session.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        },
        timeout=30
    )

    logger.info(f"📡 SL Place status ({symbol}): {r.status_code}")

    return r.status_code in (200, 201)

# ==========================
# MODIFY SL (TRAILING)
# ==========================
def modify_sl(order_id, qty, trigger, symbol):
    """Modify SL order with new trigger price"""
    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for modifying SL on {symbol}")
        return False

    price = round(trigger * 0.995, 2)
    disclosed_qty = max(1, int(qty * 0.3))

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "price": price,
        "triggerPrice": round(trigger, 2),
        "disclosedQuantity": disclosed_qty,
        "validity": "DAY"
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token
    }

    logger.info(f"🔄 Modifying SL for {symbol}: trigger={trigger}")

    r = session.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers=headers,
        timeout=15
    )

    logger.info(f"📡 SL Modify status ({symbol}): {r.status_code}")

    if r.status_code not in (200, 201):
        logger.error(f"❌ SL MODIFY FAILED for {symbol}")
        return False

    logger.info(f"✅ SL trailed for {symbol} to {trigger}")
    return True

# ==========================
# MAIN SL ENGINE
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V13 START")
    logger.info("=" * 80)

    # Validate environment
    validate_env()

    # Initialize Google Sheets
    worksheet = init_google_sheets()
    if not worksheet:
        logger.error("❌ Failed to initialize Google Sheets")
        return

    # Get all trades from Google Sheets
    trades_sheet = get_trades_from_sheets(worksheet)
    trades_by_symbol = {t.get("Symbol"): t for t in trades_sheet if t.get("Symbol")}

    logger.info(f"📊 Trades in Google Sheets: {len(trades_by_symbol)}")

    # Get positions and holdings from Dhan
    positions = get_positions()
    holdings = get_holdings()

    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 Total positions in Dhan: {len(all_pos)}")

    # Get forever orders
    forever = get_forever_orders()
    sl_map = {
        str(o["securityId"]): o
        for o in forever
        if o.get("transactionType") == "SELL"
           and o.get("orderStatus") == "PENDING"
    }

    logger.info(f"📊 Existing SL Orders: {len(sl_map)}")

    # Statistics
    placed = 0
    modified = 0
    marked_exit = 0
    inserted = 0

    # ===== PROCESS EACH POSITION =====
    for sec_id, pos in all_pos.items():
        symbol = pos['symbol']
        logger.info(f"\n{'='*80}")
        logger.info(f"📍 Processing: {symbol} (Qty: {pos['qty']}, Avg: {pos['avgPrice']})")

        # Check if stock exists in Google Sheets
        if symbol not in trades_by_symbol:
            logger.warning(f"⚠️ {symbol} NOT in Google Sheets - inserting...")
            if insert_missing_stock(worksheet, sec_id, symbol, pos["qty"], pos["avgPrice"]):
                inserted += 1
            # Refresh trades from sheet
            trades_sheet = get_trades_from_sheets(worksheet)
            trades_by_symbol = {t.get("Symbol"): t for t in trades_sheet if t.get("Symbol")}

        # Get trade details from sheet
        trade = trades_by_symbol.get(symbol)
        if not trade:
            logger.error(f"❌ Could not get trade details for {symbol}")
            continue

        entry_price = float(trade.get("Entry_Price") or 0)
        sl_price = trade.get("SL_Price", "")

        # If SL_Price is not set, skip (user needs to set it)
        if not sl_price or sl_price == "":
            logger.warning(f"⚠️ SL_Price not set for {symbol} in Google Sheets - skipping")
            continue

        sl_price = float(sl_price)

        # Get current close price
        close_price = get_close_price(symbol)
        if not close_price:
            logger.warning(f"⚠️ Could not fetch close price for {symbol}")
            continue

        logger.info(f"   Entry: {entry_price} | Close: {close_price} | SL_Price: {sl_price}")

        # ===== KEY LOGIC: Check if close < SL_Price =====
        if close_price < sl_price:
            logger.warning(f"🔴 {symbol} CLOSE ({close_price}) < SL_Price ({sl_price}) - MARKING FOR EXIT")

            if sec_id in sl_map:
                sl_order = sl_map[sec_id]
                if modify_sl_for_exit(sl_order["orderId"], pos["qty"], symbol):
                    marked_exit += 1
            else:
                logger.warning(f"⚠️ No SL order found for {symbol} to modify")
        else:
            logger.info(f"✅ {symbol} close price OK (Close >= SL_Price)")

            # SL exists - check for trailing adjustment (only if not marked for exit)
            if sec_id in sl_map:
                existing_order = sl_map[sec_id]
                current_trigger = existing_order.get("triggerPrice")

                # Get LTP for trailing logic
                ltp = get_ltp(symbol)
                if ltp:
                    new_trigger = calculate_sl(entry_price, ltp, current_trigger)
                    logger.info(f"   Current SL: {current_trigger} → Calculated: {new_trigger}")

                    if new_trigger > current_trigger:
                        if modify_sl(existing_order["orderId"], pos["qty"], new_trigger, symbol):
                            modified += 1
                    else:
                        logger.info(f"✅ SL optimal for {symbol} (no change)")
            else:
                logger.warning(f"⚠️ No SL order for {symbol} - placing new SL")
                if place_sl(sec_id, pos["qty"], entry_price, symbol):
                    placed += 1

            time.sleep(0.5)

    # ===== SUMMARY =====
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ SL ENGINE COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"   📊 SL Placed (new): {placed}")
    logger.info(f"   🔄 SL Modified (trailed): {modified}")
    logger.info(f"   🔴 SL Modified (exit): {marked_exit}")
    logger.info(f"   ➕ Stocks Inserted: {inserted}")
    logger.info(f"{'='*80}")

# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()