# ==============================================
# 🚀 SL ENGINE V15 - FINAL PRODUCTION VERSION
#
# FEATURES:
# ✅ Data cleanup (remove stale rows)
# ✅ Pending order detection
# ✅ Add new columns at END (no corruption)
# ✅ IST timezone fix for close price
# ✅ Smart price fallback (Close → LTP)
# ✅ Extensive debug logging
# ✅ Status auto-detection (PENDING/OPEN/TRAILING/CLOSE_BELOW_SL)
# ✅ Google Sheets updates (A-O untouched)
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
    level=logging.DEBUG,
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

        try:
            trades_ws = spreadsheet.worksheet("Trades")
            logger.info(f"✅ Using worksheet: Trades")
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"❌ Worksheet 'Trades' not found")
            return None

        try:
            portfolio_ws = spreadsheet.worksheet("Portfolio")
            logger.info(f"✅ Using worksheet: Portfolio")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"⚠️ Worksheet 'Portfolio' not found, creating...")
            portfolio_ws = spreadsheet.add_worksheet(title="Portfolio", rows=1000, cols=10)
            headers = ["Date", "Active_Count", "Total_Open_PnL", "Total_Realized_PnL",
                       "Win_Rate%", "Avg_Days_Held", "Best_Trade%", "Worst_Trade%",
                       "Total_Trades", "Updated_At"]
            portfolio_ws.insert_row(headers, 1)
            logger.info(f"✅ Created Portfolio worksheet")

        return {
            "trades": trades_ws,
            "portfolio": portfolio_ws,
            "spreadsheet": spreadsheet
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
# TOKEN
# ==========================
def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
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

        data = r.json()
        if "accessToken" not in data:
            logger.error(f"❌ Token failed: {data}")
            return None

        CURRENT_TOKEN = data["accessToken"]
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
        logger.info(f"✅ Token generated")
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

        data = r.json()
        logger.info(f"📊 Found {len(data) if isinstance(data, list) else 0} forever orders")
        return data if isinstance(data, list) else []

    except Exception as e:
        logger.error(f"❌ Get forever orders failed: {e}")
        return []


# ==========================
# ADD NEW COLUMN HEADERS - FIXED
# ==========================
def add_new_column_headers(trades_ws):
    """Add headers for new columns P-AB using CORRECT gspread API"""
    try:
        logger.info("\n" + "="*80)
        logger.info("📋 ADDING NEW COLUMN HEADERS (P-AB)")
        logger.info("="*80)

        current_headers = trades_ws.row_values(1)
        logger.info(f"📊 Current headers in row 1: {len(current_headers)} columns")

        new_headers = [
            "Exit_Price",           # P (column 16)
            "Exit_Time",            # Q (column 17)
            "Previous_SL_Price",    # R (column 18)
            "Unrealized_PnL",       # S (column 19)
            "Realized_PnL",         # T (column 20)
            "Unrealized_PnL%",      # U (column 21)
            "Realized_PnL%",        # V (column 22)
            "Win_Loss",             # W (column 23)
            "Return_Pct",           # X (column 24)
            "Days_Held",            # Y (column 25)
            "RR_Ratio",             # Z (column 26)
            "Entry_Order_ID",       # AA (column 27)
            "Exit_Order_ID",        # AB (column 28)
        ]

        logger.info(f"🔧 Adding {len(new_headers)} headers using batch update...")

        # Use update_cells with proper Cell objects - THIS IS THE FIX!
        cell_list = []
        for col_idx, header_name in enumerate(new_headers, start=16):
            cell_list.append(gspread.Cell(1, col_idx, header_name))

        if not DRY_RUN:
            trades_ws.update_cells(cell_list, value_input_option="USER_ENTERED")

        for col_idx, header_name in enumerate(new_headers, start=16):
            col_letter = chr(64 + col_idx)
            logger.info(f"✅ Added header: {col_letter}1 = {header_name}")

        logger.info(f"✅ New column headers added successfully!")
        return True

    except Exception as e:
        logger.error(f"❌ Add headers failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ==========================
# CLEANUP STALE TRADES
# ==========================
def cleanup_stale_trades(trades_ws, trades_sheet):
    """Remove rows not in active Dhan orders/positions"""
    try:
        logger.info("\n" + "=" * 80)
        logger.info("🧹 CLEANING UP STALE TRADES")
        logger.info("=" * 80)

        positions = get_positions()
        holdings = get_holdings()
        sl_orders = get_forever_orders()

        active_sec_ids = set()
        for p in positions:
            active_sec_ids.add(p["securityId"])
        for h in holdings:
            active_sec_ids.add(h["securityId"])
        for o in sl_orders:
            active_sec_ids.add(str(o.get("securityId", "")))

        logger.info(f"📊 Active stocks in Dhan: {len(active_sec_ids)}")

        rows_to_delete = []
        rows_to_keep = []

        for idx, trade in enumerate(trades_sheet):
            sec_id = str(trade.get("Security_ID", "")).strip()
            symbol = trade.get("Symbol", "")
            row_num = idx + 2

            if sec_id not in active_sec_ids:
                rows_to_delete.append((row_num, symbol, sec_id))
                logger.warning(f"🗑️ Marked: {symbol} (SEC_ID: {sec_id})")
            else:
                rows_to_keep.append((row_num, symbol, sec_id))
                logger.info(f"✅ Keeping: {symbol} (SEC_ID: {sec_id})")

        logger.info(f"\n🗑️ Deleting {len(rows_to_delete)} stale rows...")
        for row_num, symbol, sec_id in sorted(rows_to_delete, reverse=True):
            try:
                if not DRY_RUN:
                    trades_ws.delete_rows(row_num)
                logger.info(f"✅ Deleted: {symbol} (row {row_num})")
            except Exception as e:
                logger.error(f"❌ Failed to delete row {row_num}: {e}")

        logger.info(f"\n✅ Cleanup complete: Deleted={len(rows_to_delete)}, Kept={len(rows_to_keep)}")

        if rows_to_delete:
            deleted_symbols = ", ".join([r[1] for r in rows_to_delete[:5]])
            send_telegram_alert("🧹 STALE TRADES REMOVED", {
                "Deleted": len(rows_to_delete),
                "Symbols": deleted_symbols,
                "Remaining": len(rows_to_keep)
            })

        return rows_to_delete, rows_to_keep

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        return [], []


# ==========================
# GET CLOSE PRICE - IST FIXED
# ==========================
def get_close_price_from_dhan(security_id, symbol, debug=False):
    """Fetch close price with IST timezone and optional debug logging"""
    try:
        token = get_token()
        if not token:
            return None

        now_ist = datetime.now(IST)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

        if now_ist < market_close:
            trade_date = now_ist - timedelta(days=1)
            date_reason = "Before market close - using yesterday"
        else:
            trade_date = now_ist
            date_reason = "After market close - using today"

        from_date = trade_date.strftime("%Y-%m-%d")
        to_date = (trade_date + timedelta(days=1)).strftime("%Y-%m-%d")

        if debug:
            logger.debug(f"🔍 [DEBUG] {symbol} close price fetch:")
            logger.debug(f"   Current IST: {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.debug(f"   Market close: {market_close.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.debug(f"   Date reason: {date_reason}")
            logger.debug(f"   From: {from_date}, To: {to_date}")

        logger.info(f"📊 Fetching {symbol} close | Date: {from_date} to {to_date}")

        payload = {
            "securityId": int(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date
        }

        if debug:
            logger.debug(f"   Payload: {payload}")

        r = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            json=payload,
            headers={"Content-Type": "application/json", "access-token": token},
            timeout=15
        )

        logger.debug(f"📡 Response status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()

            if debug:
                logger.debug(f"   Response keys: {data.keys()}")
                logger.debug(f"   Close data length: {len(data.get('close', []))}")

            if data.get("close") and len(data.get("close", [])) > 0:
                close_price = float(data["close"][-1])
                logger.info(f"✅ {symbol} close: {close_price}")
                return close_price
            else:
                logger.warning(f"⚠️ {symbol} close empty")
                if debug:
                    logger.debug(f"   Full response: {data}")
                return None
        else:
            logger.warning(f"⚠️ {symbol} close API error: {r.status_code}")
            if debug:
                logger.debug(f"   Response: {r.text}")
            return None

    except Exception as e:
        logger.error(f"❌ {symbol} close error: {e}")
        return None


# ==========================
# GET LTP - FALLBACK
# ==========================
def get_ltp_from_dhan(security_id, symbol):
    """Fetch LTP as fallback"""
    try:
        token = get_token()
        if not token:
            return None

        now_ist = datetime.now(IST)
        from_date = (now_ist - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        to_date = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "securityId": int(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "60",
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date
        }

        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            json=payload,
            headers={"Content-Type": "application/json", "access-token": token},
            timeout=15
        )

        if r.status_code == 200:
            data = r.json()
            if data.get("close") and len(data.get("close", [])) > 0:
                ltp = float(data["close"][-1])
                logger.info(f"✅ {symbol} LTP: {ltp}")
                return ltp

        return None

    except Exception as e:
        logger.error(f"❌ {symbol} LTP error: {e}")
        return None


# ==========================
# GET CURRENT PRICE
# ==========================
def get_current_price(security_id, symbol):
    """Get price: Close → LTP → None"""
    logger.info(f"🔍 Getting price for {symbol}...")

    close_price = get_close_price_from_dhan(security_id, symbol)
    if close_price:
        logger.info(f"✅ Using CLOSE: {close_price}")
        return close_price, "CLOSE"

    logger.info(f"⚠️ Close failed, trying LTP...")
    ltp = get_ltp_from_dhan(security_id, symbol)
    if ltp:
        logger.warning(f"⚠️ Using LTP (fallback): {ltp}")
        return ltp, "LTP"

    logger.error(f"❌ No price data")
    return None, "NONE"


# ==========================
# CALCULATIONS
# ==========================
def calculate_sl(entry, ltp, current_sl):
    """Calculate SL"""
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


def determine_status(current_price, sl_price, dhan_trigger=None):
    """Determine status"""
    if current_price and sl_price and current_price < sl_price:
        return "CLOSE_BELOW_SL"

    if dhan_trigger and sl_price and dhan_trigger > sl_price:
        return "TRAILING"

    return "OPEN"


# ==========================
# SL ORDERS
# ==========================
def place_sl(sec_id, qty, avg, symbol):
    """Place SL"""
    if not avg:
        return False

    trigger = calculate_sl(avg, avg, None)
    price = round(trigger * 0.995, 2)

    logger.info(f"📤 Placing SL: {symbol} | Trigger: {trigger}")

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
        return False

    try:
        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=30
        )

        if r.status_code in (200, 201):
            logger.info(f"✅ SL placed: {symbol}")
            send_telegram_alert("📊 SL PLACED (NEW)", {
                "Symbol": symbol, "Qty": qty, "Trigger": trigger
            })
            return True
        else:
            logger.error(f"❌ Place SL failed")
            return False

    except Exception as e:
        logger.error(f"❌ Place SL exception: {e}")
        return False


def modify_sl(order_id, qty, trigger, symbol):
    """Modify SL"""
    logger.info(f"🔄 Modifying: {symbol} | Trigger: {trigger}")

    token = get_token()
    if not token:
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

        if r.status_code in (200, 201):
            logger.info(f"✅ SL modified: {symbol}")
            send_telegram_alert("🔄 SL MODIFIED (TRAILING)", {
                "Symbol": symbol, "Trigger": trigger
            })
            return True
        else:
            logger.error(f"❌ SL modify failed")
            return False

    except Exception as e:
        logger.error(f"❌ Modify SL exception: {e}")
        return False


def modify_sl_for_exit(order_id, qty, symbol):
    """Exit position with improved error handling"""
    logger.info(f"🔴 Exiting: {symbol}")

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

        logger.debug(f"📡 Exit response status: {r.status_code}")

        if r.status_code in (200, 201):
            logger.info(f"✅ Exit placed: {symbol}")
            send_telegram_alert("🔴 EXIT (CLOSE < SL)", {
                "Symbol": symbol, "Qty": qty, "Action": "MARKET EXIT", "Status": "SUCCESS"
            })
            return True

        elif r.status_code == 400:
            # 400 error - might be order state issue, try to get details
            logger.warning(f"⚠️ Exit got 400 error for {symbol} (order_id: {order_id})")
            logger.debug(f"   Response: {r.text}")

            # Check if order already triggered/cancelled
            try:
                order_status = get_dhan_order_status(order_id)
                if order_status:
                    logger.warning(f"   Order status: {order_status}")
                    if order_status == "TRIGGERED":
                        logger.info(f"✅ Order already TRIGGERED for {symbol}")
                        return True
                    elif order_status in ("CANCELLED", "REJECTED", "EXPIRED"):
                        logger.error(f"❌ Order {order_status} for {symbol} - cannot exit")
                        send_telegram_alert("⚠️ EXIT FAILED", {
                            "Symbol": symbol, "Reason": f"Order {order_status}", "Status": "NEED_MANUAL_EXIT"
                        })
                        return False
            except Exception as e:
                logger.debug(f"   Could not check order status: {e}")

            # Mark for retry
            logger.warning(f"⚠️ Exit will retry on next run for {symbol}")
            send_telegram_alert("⚠️ EXIT FAILED - WILL RETRY", {
                "Symbol": symbol, "OrderID": order_id, "Status": "PENDING_RETRY"
            })
            return False

        elif r.status_code == 401:
            logger.error(f"❌ Authentication failed - token may be invalid")
            return False

        else:
            logger.error(f"❌ Exit failed: HTTP {r.status_code}")
            logger.debug(f"   Response: {r.text}")
            send_telegram_alert("❌ EXIT FAILED", {
                "Symbol": symbol, "Status": f"HTTP {r.status_code}"
            })
            return False

    except Exception as e:
        logger.error(f"❌ Exit exception: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ==========================
# UPDATE TRADE ROW
# ==========================
def update_trade_row(trades_ws, row_number, updates_dict):
    """Update row - ONLY columns A-O"""
    try:
        headers = [
            "ID", "Symbol", "Security_ID", "Qty", "Entry_Price", "Entry_Time",
            "Status", "SL_Price", "Target_Price", "Setup_ID", "Current_Price",
            "PnL", "PnL_Percent", "Updated_At", "Dhan_Order_ID"
        ]

        cell_range = trades_ws.range(f'A{row_number}:O{row_number}')

        for col_idx, cell in enumerate(cell_range):
            if col_idx < len(headers):
                col_name = headers[col_idx]
                if col_name in updates_dict:
                    cell.value = updates_dict[col_name]

        trades_ws.update_cells(cell_range, value_input_option="USER_ENTERED")
        logger.debug(f"✅ Updated row {row_number}")
        return True

    except Exception as e:
        logger.error(f"❌ Row update failed: {e}")
        return False


# ==========================
# PORTFOLIO METRICS
# ==========================
def calculate_portfolio_metrics(trades_sheet):
    """Calculate portfolio metrics"""
    try:
        active = [t for t in trades_sheet if t.get("Status") not in ["CLOSED", "PENDING"]]
        open_pnl = sum(float(t.get("PnL", 0) or 0) for t in active)

        logger.info(f"📊 Portfolio: Active={len(active)}, PnL={open_pnl}")

        return {
            "active_count": len(active),
            "open_pnl": round(open_pnl, 2)
        }
    except Exception as e:
        logger.error(f"❌ Metrics failed: {e}")
        return {"active_count": 0, "open_pnl": 0}


def update_portfolio_sheet(portfolio_ws, metrics):
    """Update portfolio"""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).isoformat()

        row = [today, metrics["active_count"], metrics["open_pnl"], 0, 0, 0, 0, 0, 0, now]
        portfolio_ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"✅ Portfolio updated")
        return True
    except Exception as e:
        logger.error(f"❌ Portfolio update failed: {e}")
        return False


# ==========================
# MAIN ENGINE
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V15 - START")
    logger.info("=" * 80)

    validate_env()

    sheets = init_google_sheets()
    if not sheets:
        return

    trades_ws = sheets["trades"]
    portfolio_ws = sheets["portfolio"]

    trades_sheet = get_trades_from_sheets(trades_ws)
    logger.info(f"📊 Trades before cleanup: {len(trades_sheet)}")

    # CLEANUP STALE TRADES
    cleanup_stale_trades(trades_ws, trades_sheet)

    # ADD NEW HEADERS
    add_new_column_headers(trades_ws)

    # REFRESH TRADES
    trades_sheet = get_trades_from_sheets(trades_ws)
    logger.info(f"📊 Trades after cleanup: {len(trades_sheet)}")

    positions = get_positions()
    holdings = get_holdings()
    forever = get_forever_orders()
    sl_map = {str(o["securityId"]): o for o in forever if o.get("transactionType") == "SELL" and o.get("orderStatus") == "PENDING"}

    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 Total positions: {len(all_pos)}")
    logger.info(f"📊 SL orders: {len(sl_map)}")

    placed = modified = marked_exit = 0

    # Process each position
    for sec_id, pos in all_pos.items():
        symbol = pos['symbol']
        logger.info(f"\n{'='*80}")
        logger.info(f"📍 {symbol} (Qty: {pos['qty']}, Avg: {pos['avgPrice']})")

        trade = find_existing_trade(trades_sheet, symbol, sec_id)
        if not trade:
            continue

        entry_price = float(trade.get("Entry_Price") or 0)
        sl_price = float(trade.get("SL_Price") or round(entry_price * BASE_SL_PCT, 2))

        # GET CURRENT PRICE
        current_price, price_source = get_current_price(sec_id, symbol)
        if not current_price:
            current_price = entry_price

        logger.info(f"   Entry: {entry_price} | Current: {current_price} | SL: {sl_price}")

        unrealized_pnl, unrealized_pnl_pct = calculate_pnl(entry_price, current_price, pos["qty"])

        dhan_trigger = sl_map.get(sec_id, {}).get("triggerPrice") if sec_id in sl_map else None
        status = determine_status(current_price, sl_price, dhan_trigger)

        logger.info(f"   Status: {status} | PnL: {unrealized_pnl} ({unrealized_pnl_pct}%)")

        now = datetime.now(timezone.utc).isoformat()
        update_data = {
            "Current_Price": current_price,
            "Status": status,
            "PnL": unrealized_pnl,
            "PnL_Percent": unrealized_pnl_pct,
            "Updated_At": now
        }

        # CHECK EXIT
        if current_price < sl_price:
            logger.warning(f"🔴 CLOSE < SL - EXIT")
            if sec_id in sl_map:
                if modify_sl_for_exit(sl_map[sec_id]["orderId"], pos["qty"], symbol):
                    marked_exit += 1
        else:
            # CHECK TRAILING
            if sec_id in sl_map:
                current_trigger = sl_map[sec_id].get("triggerPrice")
                new_trigger = calculate_sl(entry_price, current_price, current_trigger)

                if new_trigger > current_trigger:
                    if modify_sl(sl_map[sec_id]["orderId"], pos["qty"], new_trigger, symbol):
                        modified += 1
                        update_data["SL_Price"] = new_trigger
            else:
                if place_sl(sec_id, pos["qty"], entry_price, symbol):
                    placed += 1

        # UPDATE ROW
        for idx, record in enumerate(trades_sheet, start=2):
            if record.get("ID") == trade.get("ID"):
                update_trade_row(trades_ws, idx, update_data)
                break

        time.sleep(0.5)

    # PORTFOLIO
    trades_sheet = get_trades_from_sheets(trades_ws)
    metrics = calculate_portfolio_metrics(trades_sheet)
    update_portfolio_sheet(portfolio_ws, metrics)

    logger.info(f"\n{'='*80}")
    logger.info(f"✅ COMPLETED | Placed: {placed} | Modified: {modified} | Exit: {marked_exit}")
    logger.info(f"{'='*80}")

    send_telegram_alert("🚀 SL ENGINE V15 COMPLETED", {
        "Placed": placed,
        "Modified": modified,
        "Exit": marked_exit,
        "Total": len(all_pos),
        "Active": metrics["active_count"]
    })


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()