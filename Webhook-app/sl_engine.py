# ==============================================
# 🚀 SL ENGINE V14 - FINAL VERSION
#
# FEATURES:
# ✅ IST Timezone fix for close price fetch
# ✅ Smart fallback: Close → LTP → Entry
# ✅ Extensive debug logging for troubleshooting
# ✅ Google Sheets updates with row-by-row fix
# ✅ Portfolio tracking & archiving
# ✅ Telegram alerts
# ==============================================

import os
import requests
import pyotp
import logging
import time
import uuid
import gspread
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

# ==========================
# LOAD ENV
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_PATHS = [
    os.path.join(BASE_DIR, ".env"),
    "/root/trade-execution-webhook/.env",
    os.path.expanduser("~/.env"),
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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DRY_RUN = os.getenv("SL_ENGINE_DRY_RUN", "false").lower() in ("true", "1", "yes")

BASE_SL_PCT = 0.92
TRAIL_PROFIT_LOCK = 0.5
MIN_LTP_BUFFER = 0.05

session = requests.Session()

# ==========================
# LOGGING SETUP
# ==========================
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG to capture all logs
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# IST Timezone
IST = timezone(timedelta(hours=5, minutes=30))

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# ==========================
# TELEGRAM HELPERS
# ==========================
def escape_markdown_v2(text):
    """Escape special characters for Telegram MarkdownV2"""
    if text is None:
        return ""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    text = str(text)
    for ch in escape_chars:
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram_alert(title, content_dict):
    """Send structured Telegram alert"""
    if DRY_RUN:
        print(f"🔕 [DRY_RUN] Would send Telegram: {title}")
        for k, v in content_dict.items():
            print(f"    {k}: {v}")
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram not configured")
        return

    try:
        lines = [f"*{escape_markdown_v2(title)}*", ""]
        for key, value in content_dict.items():
            key_str = escape_markdown_v2(str(key))
            value_str = escape_markdown_v2(str(value))
            lines.append(f"  • *{key_str}:* `{value_str}`")

        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "MarkdownV2"
        }

        r = requests.post(url, data=payload, timeout=10)

        if r.status_code == 200:
            logger.info(f"✅ Telegram alert sent: {title}")
        else:
            logger.warning(f"⚠️ Telegram failed ({r.status_code})")

    except Exception as e:
        logger.error(f"❌ Telegram error: {e}")


# ==========================
# HELPER: Normalize Symbol
# ==========================
def normalize_symbol(symbol):
    """Remove .NS suffix for comparison"""
    if symbol and isinstance(symbol, str):
        return symbol.replace(".NS", "").strip()
    return symbol


# ==========================
# ENV VALIDATION
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

    logger.info(f"✅ ENV OK")


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

        # Get or create worksheets
        try:
            trades_ws = spreadsheet.worksheet("Trades")
            logger.info(f"✅ Using worksheet: Trades")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"⚠️ Worksheet 'Trades' not found, creating...")
            trades_ws = spreadsheet.add_worksheet(title="Trades", rows=1000, cols=25)
            headers = [
                "ID", "Symbol", "Security_ID", "Qty", "Entry_Price", "Entry_Time",
                "Current_Price", "Exit_Price", "Exit_Time", "SL_Price", "Previous_SL_Price",
                "Target_Price", "Status", "Unrealized_PnL", "Realized_PnL", "Unrealized_PnL%",
                "Realized_PnL%", "Win_Loss", "Return_Pct", "Days_Held", "RR_Ratio",
                "Entry_Order_ID", "Dhan_Order_ID", "Exit_Order_ID", "Setup_ID", "Updated_At"
            ]
            trades_ws.insert_row(headers, 1)
            logger.info(f"✅ Created Trades worksheet with headers")

        try:
            portfolio_ws = spreadsheet.worksheet("Portfolio")
            logger.info(f"✅ Using worksheet: Portfolio")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"⚠️ Worksheet 'Portfolio' not found, creating...")
            portfolio_ws = spreadsheet.add_worksheet(title="Portfolio", rows=1000, cols=10)
            headers = [
                "Date", "Active_Count", "Total_Open_PnL", "Total_Realized_PnL",
                "Win_Rate%", "Avg_Days_Held", "Best_Trade%", "Worst_Trade%",
                "Total_Trades", "Updated_At"
            ]
            portfolio_ws.insert_row(headers, 1)
            logger.info(f"✅ Created Portfolio worksheet with headers")

        try:
            archive_ws = spreadsheet.worksheet("Archive")
            logger.info(f"✅ Using worksheet: Archive")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"⚠️ Worksheet 'Archive' not found, creating...")
            archive_ws = spreadsheet.add_worksheet(title="Archive", rows=5000, cols=25)
            headers = [
                "ID", "Symbol", "Security_ID", "Qty", "Entry_Price", "Entry_Time",
                "Current_Price", "Exit_Price", "Exit_Time", "SL_Price", "Previous_SL_Price",
                "Target_Price", "Status", "Unrealized_PnL", "Realized_PnL", "Unrealized_PnL%",
                "Realized_PnL%", "Win_Loss", "Return_Pct", "Days_Held", "RR_Ratio",
                "Entry_Order_ID", "Dhan_Order_ID", "Exit_Order_ID", "Setup_ID", "Updated_At"
            ]
            archive_ws.insert_row(headers, 1)
            logger.info(f"✅ Created Archive worksheet with headers")

        return {
            "trades": trades_ws,
            "portfolio": portfolio_ws,
            "archive": archive_ws
        }

    except Exception as e:
        logger.error(f"❌ Google Sheets init failed: {e}")
        return None


# ==========================
# GET ALL TRADES FROM SHEETS
# ==========================
def get_trades_from_sheets(trades_ws):
    """Get all trades from Google Sheets"""
    try:
        records = trades_ws.get_all_records()
        logger.info(f"✅ Retrieved {len(records)} trades from sheets")
        return records
    except Exception as e:
        logger.error(f"❌ Failed to get trades: {e}")
        return []


# ==========================
# FIND EXISTING TRADE
# ==========================
def find_existing_trade(trades_sheet, symbol, security_id):
    """Find trade by normalized symbol"""
    norm_symbol = normalize_symbol(symbol)

    for trade in trades_sheet:
        trade_symbol = trade.get("Symbol", "")
        trade_sec_id = str(trade.get("Security_ID", ""))
        norm_trade_symbol = normalize_symbol(trade_symbol)

        if norm_trade_symbol == norm_symbol and trade_sec_id == str(security_id):
            return trade

    return None


# ==========================
# INSERT MISSING STOCK
# ==========================
def insert_missing_stock(trades_ws, security_id, symbol, qty, avg_price, sl_price=None):
    """Insert a new stock position"""
    try:
        records = trades_ws.get_all_records()
        if find_existing_trade(records, symbol, security_id):
            logger.info(f"ℹ️ Stock {symbol} already in sheets")
            return True, records[0].get("SL_Price", "")

        if not sl_price:
            sl_price = round(avg_price * BASE_SL_PCT, 2)

        symbol_normalized = normalize_symbol(symbol) + ".NS"
        new_id = str(int(time.time() * 1000))[:10]
        now = datetime.now(timezone.utc).isoformat()

        row = [
            new_id, symbol_normalized, str(security_id), str(qty), str(avg_price), now,
            str(avg_price), "", "", str(sl_price), str(sl_price), "", "INITIAL_SL",
            "0", "", "0", "", "", "", "", "", "", "", "", "", now
        ]

        trades_ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"✅ Inserted: {symbol_normalized} (SL: {sl_price})")
        return True, str(sl_price)

    except Exception as e:
        logger.error(f"❌ Insert stock failed: {e}")
        return False, None


# ==========================
# UPDATE TRADE ROW - FIXED
# ==========================
def update_trade_row(trades_ws, row_number, updates_dict):
    """Update a single row in Trades sheet"""
    try:
        headers = [
            "ID", "Symbol", "Security_ID", "Qty", "Entry_Price", "Entry_Time",
            "Current_Price", "Exit_Price", "Exit_Time", "SL_Price", "Previous_SL_Price",
            "Target_Price", "Status", "Unrealized_PnL", "Realized_PnL", "Unrealized_PnL%",
            "Realized_PnL%", "Win_Loss", "Return_Pct", "Days_Held", "RR_Ratio",
            "Entry_Order_ID", "Dhan_Order_ID", "Exit_Order_ID", "Setup_ID", "Updated_At"
        ]

        cell_range = trades_ws.range(f'A{row_number}:Y{row_number}')

        for col_idx, cell in enumerate(cell_range):
            col_name = headers[col_idx]
            if col_name in updates_dict:
                cell.value = updates_dict[col_name]

        trades_ws.update_cells(cell_range, value_input_option="USER_ENTERED")
        logger.debug(f"✅ Updated row {row_number} with fields: {', '.join(updates_dict.keys())}")
        return True

    except Exception as e:
        logger.error(f"❌ Row update failed (row {row_number}): {e}")
        return False


# ==========================
# TOKEN
# ==========================
def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        logger.debug(f"✅ Using existing token")
        return CURRENT_TOKEN

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        logger.info("🔑 Generating new token...")

        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            timeout=10
        )

        logger.debug(f"🔐 Token request status: {r.status_code}")

        data = r.json()
        if "accessToken" not in data:
            logger.error(f"❌ Token failed: {data}")
            return None

        CURRENT_TOKEN = data["accessToken"]
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
        logger.info(f"✅ Token generated successfully")
        return CURRENT_TOKEN

    except Exception as e:
        logger.error(f"❌ Token error: {e}")
        return None


# ==========================
# GET POSITIONS & HOLDINGS
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return []

    try:
        r = session.get(
            "https://api.dhan.co/v2/positions",
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=10
        )

        logger.debug(f"📡 Positions API status: {r.status_code}")
        data = r.json()
        result = []

        for p in data:
            if p.get("netQty", 0) > 0:
                result.append({
                    "securityId": str(p["securityId"]),
                    "symbol": p["tradingSymbol"],
                    "qty": p["netQty"],
                    "avgPrice": p.get("buyAvg") or p.get("costPrice")
                })

        logger.info(f"📊 Found {len(result)} positions")
        return result

    except Exception as e:
        logger.error(f"❌ Get positions failed: {e}")
        return []


def get_holdings():
    token = get_token()
    if not token:
        return []

    try:
        r = session.get(
            "https://api.dhan.co/v2/holdings",
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=10
        )

        logger.debug(f"📡 Holdings API status: {r.status_code}")
        data = r.json()
        result = []

        for h in data:
            if h.get("totalQty", 0) > 0:
                result.append({
                    "securityId": str(h["securityId"]),
                    "symbol": h["tradingSymbol"],
                    "qty": h["totalQty"],
                    "avgPrice": h.get("avgCostPrice")
                })

        logger.info(f"📊 Found {len(result)} holdings")
        return result

    except Exception as e:
        logger.error(f"❌ Get holdings failed: {e}")
        return []


# ==========================
# FOREVER ORDERS
# ==========================
def get_forever_orders():
    token = get_token()
    if not token:
        return []

    try:
        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=10
        )

        logger.debug(f"📡 Forever orders API status: {r.status_code}")
        data = r.json()
        logger.info(f"📊 Found {len(data) if isinstance(data, list) else 0} forever orders")
        return data if isinstance(data, list) else []

    except Exception as e:
        logger.error(f"❌ Get forever orders failed: {e}")
        return []


# ==========================
# GET CLOSE PRICE - FIXED IST TIMEZONE
# ==========================
def get_close_price_from_dhan(security_id, symbol):
    """
    Fetch today's close price from Dhan API with IST timezone

    Logic:
    - Use IST timezone (not UTC)
    - If before market close (3:30 PM IST): fetch yesterday's close
    - If after market close (3:30 PM IST): fetch today's close
    """
    try:
        token = get_token()
        if not token:
            logger.warning(f"⚠️ No token for close price fetch ({symbol})")
            return None

        # Get current time in IST
        now_ist = datetime.now(IST)
        logger.debug(f"📍 Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")

        # Determine trade date based on market hours
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

        if now_ist < market_close:
            # Before market close (3:30 PM), get yesterday's close
            trade_date = now_ist - timedelta(days=1)
            logger.debug(f"⏰ Before market close - fetching YESTERDAY'S close")
        else:
            # After market close (3:30 PM), get today's close
            trade_date = now_ist
            logger.debug(f"⏰ After market close - fetching TODAY'S close")

        from_date = trade_date.strftime("%Y-%m-%d")
        to_date = (trade_date + timedelta(days=1)).strftime("%Y-%m-%d")

        logger.info(f"📊 Fetching {symbol} close price | Date: {from_date} to {to_date}")

        payload = {
            "securityId": int(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date
        }

        logger.debug(f"📤 Payload: {payload}")

        headers = {
            "Content-Type": "application/json",
            "access-token": token
        }

        r = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            json=payload,
            headers=headers,
            timeout=15
        )

        logger.debug(f"📡 Response status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            logger.debug(f"📊 Response data: {data}")

            if data.get("close") and len(data.get("close", [])) > 0:
                close_price = float(data["close"][-1])
                logger.info(f"✅ {symbol} close price (IST): {close_price}")
                return close_price
            else:
                logger.warning(f"⚠️ {symbol} close price API returned empty data")
                logger.debug(f"   Full response: {data}")
                return None
        else:
            logger.warning(f"⚠️ {symbol} close price API error: Status {r.status_code}")
            logger.debug(f"   Response: {r.text}")
            return None

    except Exception as e:
        logger.error(f"❌ {symbol} close price fetch exception: {e}")
        return None


# ==========================
# GET LTP FROM DHAN - FALLBACK
# ==========================
def get_ltp_from_dhan(security_id, symbol):
    """
    Fetch intraday LTP as fallback when close price fails
    Uses 60-minute candles for more data availability
    """
    try:
        token = get_token()
        if not token:
            logger.warning(f"⚠️ No token for LTP fetch ({symbol})")
            return None

        now_ist = datetime.now(IST)
        from_date = (now_ist - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        to_date = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"📊 Fetching {symbol} LTP | Date range: {from_date} to {to_date}")

        payload = {
            "securityId": int(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "60",
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date
        }

        logger.debug(f"📤 Payload: {payload}")

        headers = {
            "Content-Type": "application/json",
            "access-token": token
        }

        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            json=payload,
            headers=headers,
            timeout=15
        )

        logger.debug(f"📡 Response status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            logger.debug(f"📊 Response data: {data}")

            if data.get("close") and len(data.get("close", [])) > 0:
                ltp = float(data["close"][-1])
                logger.info(f"✅ {symbol} LTP (intraday): {ltp}")
                return ltp
            else:
                logger.warning(f"⚠️ {symbol} LTP API returned empty data")
                logger.debug(f"   Full response: {data}")
                return None
        else:
            logger.warning(f"⚠️ {symbol} LTP API error: Status {r.status_code}")
            logger.debug(f"   Response: {r.text}")
            return None

    except Exception as e:
        logger.error(f"❌ {symbol} LTP fetch exception: {e}")
        return None


# ==========================
# GET CURRENT PRICE - SMART FALLBACK
# ==========================
def get_current_price(security_id, symbol):
    """
    Get current price with smart fallback strategy
    Priority: Close (IST) → LTP (Intraday) → None
    """
    logger.info(f"🔍 Getting current price for {symbol}...")

    # Try 1: Close price (IST-fixed)
    close_price = get_close_price_from_dhan(security_id, symbol)
    if close_price:
        logger.info(f"✅ Using CLOSE price for {symbol}: {close_price}")
        return close_price, "CLOSE"

    # Try 2: Fallback to LTP
    logger.info(f"⚠️ Close price failed, trying LTP fallback for {symbol}...")
    ltp = get_ltp_from_dhan(security_id, symbol)
    if ltp:
        logger.warning(f"⚠️ Using LTP (fallback) for {symbol}: {ltp}")
        return ltp, "LTP"

    # All failed
    logger.error(f"❌ No price data available for {symbol}")
    return None, "NONE"


# ==========================
# CALCULATIONS
# ==========================
def calculate_sl(entry, ltp, current_sl):
    """Calculate trailing stop-loss"""
    base_sl = entry * BASE_SL_PCT
    new_sl = max(current_sl or 0, base_sl)

    if ltp > entry:
        profit = ltp - entry
        trailing_sl = entry + (profit * TRAIL_PROFIT_LOCK)
        max_allowed_sl = ltp * (1 - MIN_LTP_BUFFER)
        new_sl = max(new_sl, min(trailing_sl, max_allowed_sl))

    return round(new_sl, 2)


def calculate_pnl(entry_price, current_price, qty):
    """Calculate PnL"""
    if not current_price or not entry_price:
        return 0, 0

    pnl = (current_price - entry_price) * qty
    pnl_pct = ((current_price - entry_price) / entry_price) * 100

    return round(pnl, 2), round(pnl_pct, 2)


def determine_status(current_price, sl_price, previous_sl_price, dhan_trigger=None):
    """Determine status"""
    if current_price and sl_price and current_price < sl_price:
        return "CLOSE_BELOW_SL"

    if previous_sl_price and sl_price and sl_price > previous_sl_price:
        return "TRAILING"

    if dhan_trigger and sl_price and dhan_trigger > sl_price:
        return "TRAILING"

    return "INITIAL_SL"


# ==========================
# SL ORDERS
# ==========================
def place_sl(sec_id, qty, avg, symbol):
    """Place SL order"""
    if not avg:
        logger.error(f"❌ Invalid avg price for {symbol}")
        return False

    trigger = calculate_sl(avg, avg, None)
    price = round(trigger * 0.995, 2)

    logger.info(f"📤 Placing SL for {symbol} | Trigger: {trigger} | Price: {price}")

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

    token = get_token()
    if not token:
        logger.error(f"❌ No token for placing SL")
        return False

    try:
        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=30
        )

        logger.debug(f"📡 Place SL response status: {r.status_code}")

        if r.status_code in (200, 201):
            logger.info(f"✅ SL placed for {symbol}")
            send_telegram_alert("📊 SL ORDER PLACED (NEW)", {
                "Symbol": symbol, "Qty": qty, "Entry": avg, "Trigger": trigger
            })
            return True
        else:
            logger.error(f"❌ Place SL failed: {r.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Place SL exception: {e}")
        return False


def modify_sl(order_id, qty, trigger, symbol):
    """Modify SL order"""
    logger.info(f"🔄 Modifying SL for {symbol} | New trigger: {trigger}")

    token = get_token()
    if not token:
        logger.error(f"❌ No token for modifying SL")
        return False

    price = round(trigger * 0.995, 2)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "price": price,
        "triggerPrice": round(trigger, 2),
        "disclosedQuantity": max(1, int(qty * 0.3)),
        "validity": "DAY"
    }

    try:
        r = session.put(
            f"https://api.dhan.co/v2/forever/orders/{order_id}",
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json", "access-token": token},
            timeout=15
        )

        logger.debug(f"📡 Modify SL response status: {r.status_code}")

        if r.status_code not in (200, 201):
            logger.error(f"❌ SL modify failed")
            return False

        logger.info(f"✅ SL modified for {symbol}")
        send_telegram_alert("🔄 SL MODIFIED (TRAILING)", {
            "Symbol": symbol, "Qty": qty, "Trigger": trigger
        })
        return True

    except Exception as e:
        logger.error(f"❌ Modify SL exception: {e}")
        return False


def modify_sl_for_exit(order_id, qty, symbol):
    """Exit position"""
    logger.info(f"🔴 Exiting position for {symbol}")

    token = get_token()
    if not token:
        logger.error(f"❌ No token for exit")
        return False

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "MARKET",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "validity": "DAY"
    }

    try:
        r = session.put(
            f"https://api.dhan.co/v2/forever/orders/{order_id}",
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json", "access-token": token},
            timeout=15
        )

        logger.debug(f"📡 Exit order response status: {r.status_code}")

        if r.status_code not in (200, 201):
            logger.error(f"❌ Exit failed")
            return False

        logger.info(f"✅ Exit order placed for {symbol}")
        send_telegram_alert("🔴 SL MODIFIED (CLOSE BELOW SL)", {
            "Symbol": symbol, "Qty": qty, "Action": "MARKET EXIT"
        })
        return True

    except Exception as e:
        logger.error(f"❌ Exit exception: {e}")
        return False


# ==========================
# PORTFOLIO METRICS
# ==========================
def calculate_portfolio_metrics(trades_sheet):
    """Calculate portfolio metrics"""
    try:
        active = [t for t in trades_sheet if t.get("Status") != "CLOSED"]
        closed = [t for t in trades_sheet if t.get("Status") == "CLOSED"]

        open_pnl = sum(float(t.get("Unrealized_PnL", 0) or 0) for t in active)
        realized_pnl = sum(float(t.get("Realized_PnL", 0) or 0) for t in closed)

        win_rate = (sum(1 for t in closed if float(t.get("Realized_PnL", 0) or 0) > 0) / len(closed) * 100) if closed else 0

        logger.info(f"📊 Portfolio metrics: Active={len(active)}, Closed={len(closed)}, Open PnL={open_pnl}, Realized={realized_pnl}")

        return {
            "active_count": len(active),
            "open_pnl": round(open_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "win_rate": round(win_rate, 2),
            "avg_days": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "total_trades": len(closed)
        }
    except Exception as e:
        logger.error(f"❌ Metrics failed: {e}")
        return None


def update_portfolio_sheet(portfolio_ws, metrics):
    """Update portfolio sheet"""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).isoformat()

        row = [today, metrics["active_count"], metrics["open_pnl"], metrics["realized_pnl"],
               metrics["win_rate"], 0, 0, 0, metrics["total_trades"], now]

        portfolio_ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"✅ Updated Portfolio sheet")
        return True
    except Exception as e:
        logger.error(f"❌ Portfolio update failed: {e}")
        return False


# ==========================
# MAIN ENGINE
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V14 - START (IST Timezone Fix + Debug Logging)")
    logger.info("=" * 80)

    validate_env()

    sheets = init_google_sheets()
    if not sheets:
        logger.error("❌ Google Sheets initialization failed")
        return

    trades_ws = sheets["trades"]
    portfolio_ws = sheets["portfolio"]
    trades_sheet = get_trades_from_sheets(trades_ws)

    positions = get_positions()
    holdings = get_holdings()

    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 Total positions: {len(all_pos)}")

    forever = get_forever_orders()
    sl_map = {str(o["securityId"]): o for o in forever if o.get("transactionType") == "SELL" and o.get("orderStatus") == "PENDING"}

    logger.info(f"📊 Existing SL orders: {len(sl_map)}")

    placed = modified = marked_exit = 0

    # Process each position
    for sec_id, pos in all_pos.items():
        symbol = pos['symbol']
        logger.info(f"\n{'='*80}")
        logger.info(f"📍 Processing: {symbol} (Qty: {pos['qty']}, Avg: {pos['avgPrice']})")

        trade = find_existing_trade(trades_sheet, symbol, sec_id)
        if not trade:
            logger.warning(f"⚠️ {symbol} not in sheets, inserting...")
            insert_missing_stock(trades_ws, sec_id, symbol, pos["qty"], pos["avgPrice"])
            trades_sheet = get_trades_from_sheets(trades_ws)
            trade = find_existing_trade(trades_sheet, symbol, sec_id)

        if not trade:
            logger.error(f"❌ Could not get trade details for {symbol}")
            continue

        entry_price = float(trade.get("Entry_Price") or 0)
        sl_price = float(trade.get("SL_Price") or round(entry_price * BASE_SL_PCT, 2))
        previous_sl_price = float(trade.get("Previous_SL_Price") or sl_price)

        # FIXED: Get current price with IST timezone + LTP fallback
        logger.info(f"🔍 Fetching current price for {symbol}...")
        current_price, price_source = get_current_price(sec_id, symbol)

        if not current_price:
            logger.warning(f"⚠️ No price data, using entry price as fallback")
            current_price = entry_price

        logger.info(f"✅ Current Price Source: {price_source} | Price: {current_price} | Entry: {entry_price} | SL: {sl_price}")

        unrealized_pnl, unrealized_pnl_pct = calculate_pnl(entry_price, current_price, pos["qty"])

        dhan_trigger = sl_map.get(sec_id, {}).get("triggerPrice") if sec_id in sl_map else None
        status = determine_status(current_price, sl_price, previous_sl_price, dhan_trigger)

        logger.info(f"📊 Status: {status} | PnL: {unrealized_pnl} ({unrealized_pnl_pct}%)")

        now = datetime.now(timezone.utc).isoformat()
        update_data = {
            "Current_Price": current_price,
            "Status": status,
            "Unrealized_PnL": unrealized_pnl,
            "Unrealized_PnL%": unrealized_pnl_pct,
            "Updated_At": now
        }

        # Check exit
        if current_price < sl_price:
            logger.warning(f"🔴 {symbol} price ({current_price}) < SL ({sl_price}) - EXIT TRIGGERED")
            if sec_id in sl_map:
                if modify_sl_for_exit(sl_map[sec_id]["orderId"], pos["qty"], symbol):
                    marked_exit += 1
        else:
            # Check trailing
            if sec_id in sl_map:
                current_trigger = sl_map[sec_id].get("triggerPrice")
                new_trigger = calculate_sl(entry_price, current_price, current_trigger)
                logger.debug(f"   SL calc: {current_trigger} → {new_trigger}")

                if new_trigger > current_trigger:
                    if modify_sl(sl_map[sec_id]["orderId"], pos["qty"], new_trigger, symbol):
                        modified += 1
                        update_data["SL_Price"] = new_trigger
                        update_data["Previous_SL_Price"] = current_trigger
            else:
                logger.warning(f"⚠️ No SL order for {symbol}, placing new...")
                if place_sl(sec_id, pos["qty"], entry_price, symbol):
                    placed += 1

        # Update row
        for idx, record in enumerate(trades_sheet, start=2):
            if record.get("ID") == trade.get("ID"):
                update_trade_row(trades_ws, idx, update_data)
                break

        time.sleep(0.5)

    # Update portfolio
    trades_sheet = get_trades_from_sheets(trades_ws)
    metrics = calculate_portfolio_metrics(trades_sheet)
    if metrics:
        update_portfolio_sheet(portfolio_ws, metrics)

    logger.info(f"\n{'='*80}")
    logger.info(f"✅ SL ENGINE COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"   📊 SL Placed: {placed}")
    logger.info(f"   🔄 SL Modified (Trailing): {modified}")
    logger.info(f"   🔴 SL Modified (Exit): {marked_exit}")
    logger.info(f"{'='*80}")

    send_telegram_alert("🚀 SL ENGINE V14 COMPLETED", {
        "Placed": placed,
        "Modified": modified,
        "Exit": marked_exit,
        "Total": len(all_pos),
        "Active": metrics["active_count"] if metrics else 0,
        "Open PnL": f"₹{metrics['open_pnl']}" if metrics else "N/A"
    })


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()