# ==============================================
# 🚀 SL ENGINE V14 (GOOGLE SHEETS INTEGRATED + TELEGRAM + PORTFOLIO TRACKING)
#
# NEW FEATURES IN V14:
# - Close price fetched from Dhan API (not YahooFinance)
# - Google Sheets updated DAILY with Current_Price, PnL, Status
# - Dynamic Status: INITIAL_SL → TRAILING → CLOSE_BELOW_SL → CLOSED
# - PnL Calculations: Unrealized & Realized
# - Previous_SL_Price for trailing detection fallback
# - Exit_Price, Exit_Time tracking
# - Days_Held, RR_Ratio, Win/Loss indicators
# - Portfolio sheet with daily snapshots
# - Archive sheet for closed trades
# - Batch & row-by-row sheet updates
# - Full Telegram alerting
# ==============================================

import os
import requests
import pyotp
import logging
import time
import uuid
import gspread
import pandas as pd
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

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DRY_RUN = os.getenv("SL_ENGINE_DRY_RUN", "false").lower() in ("true", "1", "yes")

BASE_SL_PCT = 0.92
TRAIL_PROFIT_LOCK = 0.5
MIN_LTP_BUFFER = 0.05

session = requests.Session()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

        # Get or create Trades worksheet
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

        # Get or create Portfolio worksheet
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

        # Get or create Archive worksheet
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
            "archive": archive_ws,
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
        logger.info(f"✅ Retrieved {len(records)} trades from Google Sheets")
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
    """Insert a new stock position to Google Sheets"""
    try:
        records = trades_ws.get_all_records()
        existing = find_existing_trade(records, symbol, security_id)

        if existing:
            logger.info(f"✅ Stock {symbol} already in sheets - skipping")
            return True, existing.get("SL_Price", "")

        if not sl_price:
            sl_price = round(avg_price * BASE_SL_PCT, 2)
            logger.info(f"📊 Using default SL: {sl_price}")

        symbol_normalized = normalize_symbol(symbol) + ".NS"
        new_id = str(int(time.time() * 1000))[:10]
        now = datetime.now(timezone.utc).isoformat()

        row = [
            new_id,              # ID
            symbol_normalized,   # Symbol
            str(security_id),    # Security_ID
            str(qty),            # Qty
            str(avg_price),      # Entry_Price
            now,                 # Entry_Time
            str(avg_price),      # Current_Price
            "",                  # Exit_Price
            "",                  # Exit_Time
            str(sl_price),       # SL_Price
            str(sl_price),       # Previous_SL_Price
            "",                  # Target_Price
            "INITIAL_SL",        # Status (NEW - was "OPEN")
            "0",                 # Unrealized_PnL
            "",                  # Realized_PnL
            "0",                 # Unrealized_PnL%
            "",                  # Realized_PnL%
            "",                  # Win_Loss
            "",                  # Return_Pct
            "",                  # Days_Held
            "",                  # RR_Ratio
            "",                  # Entry_Order_ID
            "",                  # Dhan_Order_ID
            "",                  # Exit_Order_ID
            "",                  # Setup_ID
            now                  # Updated_At
        ]

        trades_ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"✅ Inserted: {symbol_normalized} (Qty: {qty}, SL: {sl_price})")
        return True, str(sl_price)

    except Exception as e:
        logger.error(f"❌ Failed to insert stock {symbol}: {e}")
        return False, None


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
# GET CLOSE PRICE FROM DHAN (NEW)
# ==========================
def get_close_price_from_dhan(security_id, symbol):
    """Fetch today's close price from Dhan API"""
    try:
        token = get_token()
        if not token:
            return None

        now = datetime.now(timezone.utc)
        to_date = now + timedelta(days=1)
        from_date = to_date - timedelta(days=1)

        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d")
        }

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

        if r.status_code == 200:
            data = r.json()
            if data.get("close"):
                close_price = data["close"][-1]
                logger.info(f"📊 {symbol} close (Dhan): {close_price}")
                return close_price

        logger.warning(f"⚠️ No close price from Dhan for {symbol}")
        return None

    except Exception as e:
        logger.warning(f"❌ Dhan close fetch failed for {symbol}: {e}")
        return None


# ==========================
# GET LTP FROM DHAN
# ==========================
def get_ltp_from_dhan(security_id, symbol):
    """Fetch Last Traded Price from Dhan intraday"""
    try:
        token = get_token()
        if not token:
            return None

        now = datetime.now(timezone.utc)
        from_date = now - timedelta(days=5)

        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "60",
            "oi": False,
            "fromDate": from_date.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": now.strftime("%Y-%m-%d %H:%M:%S")
        }

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

        if r.status_code == 200:
            data = r.json()
            if data.get("close"):
                ltp = data["close"][-1]
                logger.info(f"📊 {symbol} LTP (Dhan): {ltp}")
                return ltp

        return None

    except Exception as e:
        logger.warning(f"❌ Dhan LTP fetch failed for {symbol}: {e}")
        return None


# ==========================
# SL CALCULATION LOGIC
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


# ==========================
# CALCULATE PNL
# ==========================
def calculate_pnl(entry_price, current_price, qty):
    """Calculate unrealized PnL"""
    if not current_price or not entry_price:
        return 0, 0

    pnl = (current_price - entry_price) * qty
    pnl_pct = ((current_price - entry_price) / entry_price) * 100

    return round(pnl, 2), round(pnl_pct, 2)


# ==========================
# CALCULATE REALIZED PNL
# ==========================
def calculate_realized_pnl(entry_price, exit_price, qty):
    """Calculate realized PnL"""
    if not exit_price or not entry_price:
        return 0, 0

    pnl = (exit_price - entry_price) * qty
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100

    return round(pnl, 2), round(pnl_pct, 2)


# ==========================
# CALCULATE RR RATIO
# ==========================
def calculate_rr_ratio(entry_price, exit_price, sl_price):
    """Calculate Risk:Reward ratio"""
    if not exit_price or not entry_price or not sl_price:
        return 0

    reward = exit_price - entry_price
    risk = entry_price - sl_price

    if risk <= 0:
        return 0

    return round(reward / risk, 2)


# ==========================
# DETERMINE WIN/LOSS
# ==========================
def determine_win_loss(realized_pnl):
    """Determine +1, -1, or 0"""
    if realized_pnl > 0:
        return 1
    elif realized_pnl < 0:
        return -1
    else:
        return 0


# ==========================
# DETERMINE STATUS
# ==========================
def determine_status(current_price, sl_price, previous_sl_price,
                     dhan_trigger=None, qty_in_holdings=None, sheet_qty=None):
    """Determine current status"""

    # If position closed (qty reduced in Dhan)
    if qty_in_holdings is not None and sheet_qty is not None:
        if qty_in_holdings < sheet_qty:
            return "CLOSED"

    # If close < SL_Price
    if current_price and sl_price:
        if current_price < sl_price:
            return "CLOSE_BELOW_SL"

    # Check if SL was trailed (compare with previous)
    if previous_sl_price and sl_price:
        if sl_price > previous_sl_price:
            return "TRAILING"

    # Fallback: Check Dhan trigger vs current SL
    if dhan_trigger and sl_price:
        if dhan_trigger > sl_price:
            return "TRAILING"

    # Default
    return "INITIAL_SL"


# ==========================
# UPDATE TRADE ROW
# ==========================
def update_trade_row(trades_ws, row_index, update_data):
    """Update a single row in Trades sheet"""
    try:
        # Build update dict: column letter → value
        # Use all 25 columns
        columns = [
            "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
            "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y"
        ]

        cells_to_update = []
        for col, value in update_data.items():
            if col in columns:
                col_idx = columns.index(col) + 1
                cell_ref = f"{col}{row_index}"
                cells_to_update.append((cell_ref, value))

        # Update cells
        for cell_ref, value in cells_to_update:
            trades_ws.update(cell_ref, value)

        logger.info(f"✅ Updated row {row_index}")
        return True

    except Exception as e:
        logger.error(f"❌ Row update failed: {e}")
        return False


# ==========================
# MODIFY SL ORDER (EXIT)
# ==========================
def modify_sl_for_exit(order_id, qty, symbol):
    """Modify SL order to exit"""
    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for exit on {symbol}")
        return False

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
        alert_content = {
            "Symbol": symbol,
            "Quantity": qty,
            "Reason": "Close < SL_Price",
            "Error": r.text[:100],
        }
        send_telegram_alert("❌ EXIT ORDER FAILED", alert_content)
        return False

    logger.info(f"✅ Exit order placed for {symbol}")

    alert_content = {
        "Symbol": symbol,
        "Quantity": qty,
        "Order ID": order_id,
        "Action": "MARKET EXIT",
        "Reason": "Close < SL_Price",
    }
    send_telegram_alert("🔴 SL MODIFIED (CLOSE BELOW SL)", alert_content)

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

    if r.status_code in (200, 201):
        alert_content = {
            "Symbol": symbol,
            "Quantity": qty,
            "Entry Price": avg,
            "Trigger Price": trigger,
            "Limit Price": price,
        }
        send_telegram_alert("📊 SL ORDER PLACED (NEW)", alert_content)
        return True
    else:
        logger.error(f"❌ Place SL failed for {symbol}: {r.text}")
        alert_content = {
            "Symbol": symbol,
            "Quantity": qty,
            "Trigger": trigger,
            "Error": r.text[:100],
        }
        send_telegram_alert("❌ SL PLACEMENT FAILED", alert_content)
        return False


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

    alert_content = {
        "Symbol": symbol,
        "Quantity": qty,
        "New Trigger": trigger,
        "New Limit": price,
        "Order ID": order_id,
    }
    send_telegram_alert("🔄 SL MODIFIED (TRAILING)", alert_content)

    return True


# ==========================
# CALCULATE PORTFOLIO METRICS
# ==========================
def calculate_portfolio_metrics(trades_sheet):
    """Calculate portfolio metrics for Portfolio sheet"""
    try:
        active_trades = [t for t in trades_sheet if t.get("Status") != "CLOSED"]
        closed_trades = [t for t in trades_sheet if t.get("Status") == "CLOSED"]

        # Total Open PnL
        total_open_pnl = 0
        for t in active_trades:
            try:
                pnl = float(t.get("Unrealized_PnL", 0) or 0)
                total_open_pnl += pnl
            except:
                pass

        # Total Realized PnL
        total_realized_pnl = 0
        for t in closed_trades:
            try:
                pnl = float(t.get("Realized_PnL", 0) or 0)
                total_realized_pnl += pnl
            except:
                pass

        # Win Rate
        if closed_trades:
            wins = sum(1 for t in closed_trades if float(t.get("Realized_PnL", 0) or 0) > 0)
            win_rate = (wins / len(closed_trades)) * 100
        else:
            win_rate = 0

        # Avg Days Held
        if closed_trades:
            total_days = 0
            count = 0
            for t in closed_trades:
                try:
                    days = int(t.get("Days_Held", 0) or 0)
                    if days > 0:
                        total_days += days
                        count += 1
                except:
                    pass
            avg_days = total_days / count if count > 0 else 0
        else:
            avg_days = 0

        # Best Trade %
        best_trade = 0
        for t in closed_trades:
            try:
                pct = float(t.get("Return_Pct", 0) or 0)
                best_trade = max(best_trade, pct)
            except:
                pass

        # Worst Trade %
        worst_trade = 0
        for t in closed_trades:
            try:
                pct = float(t.get("Return_Pct", 0) or 0)
                worst_trade = min(worst_trade, pct)
            except:
                pass

        return {
            "active_count": len(active_trades),
            "open_pnl": round(total_open_pnl, 2),
            "realized_pnl": round(total_realized_pnl, 2),
            "win_rate": round(win_rate, 2),
            "avg_days": round(avg_days, 1),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            "total_trades": len(closed_trades)
        }

    except Exception as e:
        logger.error(f"❌ Portfolio metrics calculation failed: {e}")
        return None


# ==========================
# UPDATE PORTFOLIO SHEET
# ==========================
def update_portfolio_sheet(portfolio_ws, metrics):
    """Add daily portfolio snapshot"""
    try:
        now = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        row = [
            today,                           # Date
            metrics["active_count"],         # Active_Count
            metrics["open_pnl"],            # Total_Open_PnL
            metrics["realized_pnl"],        # Total_Realized_PnL
            metrics["win_rate"],            # Win_Rate%
            metrics["avg_days"],            # Avg_Days_Held
            metrics["best_trade"],          # Best_Trade%
            metrics["worst_trade"],         # Worst_Trade%
            metrics["total_trades"],        # Total_Trades
            now                             # Updated_At
        ]

        portfolio_ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"✅ Updated Portfolio sheet")
        return True

    except Exception as e:
        logger.error(f"❌ Portfolio update failed: {e}")
        return False


# ==========================
# ARCHIVE CLOSED TRADES
# ==========================
def archive_closed_trades(trades_ws, archive_ws, trades_sheet):
    """Move closed trades to archive sheet"""
    try:
        closed_trades = [t for t in trades_sheet if t.get("Status") == "CLOSED"]

        if not closed_trades:
            logger.info("✅ No closed trades to archive")
            return True

        # Get all archive records
        archive_records = archive_ws.get_all_records()
        archived_ids = [r.get("ID") for r in archive_records]

        # Find trades not yet archived
        to_archive = [t for t in closed_trades if t.get("ID") not in archived_ids]

        if not to_archive:
            logger.info("✅ All closed trades already archived")
            return True

        # Add to archive
        for trade in to_archive:
            row = [
                trade.get("ID", ""),
                trade.get("Symbol", ""),
                trade.get("Security_ID", ""),
                trade.get("Qty", ""),
                trade.get("Entry_Price", ""),
                trade.get("Entry_Time", ""),
                trade.get("Current_Price", ""),
                trade.get("Exit_Price", ""),
                trade.get("Exit_Time", ""),
                trade.get("SL_Price", ""),
                trade.get("Previous_SL_Price", ""),
                trade.get("Target_Price", ""),
                trade.get("Status", ""),
                trade.get("Unrealized_PnL", ""),
                trade.get("Realized_PnL", ""),
                trade.get("Unrealized_PnL%", ""),
                trade.get("Realized_PnL%", ""),
                trade.get("Win_Loss", ""),
                trade.get("Return_Pct", ""),
                trade.get("Days_Held", ""),
                trade.get("RR_Ratio", ""),
                trade.get("Entry_Order_ID", ""),
                trade.get("Dhan_Order_ID", ""),
                trade.get("Exit_Order_ID", ""),
                trade.get("Setup_ID", ""),
                trade.get("Updated_At", "")
            ]
            archive_ws.append_row(row, value_input_option="USER_ENTERED")

        # Delete from main sheet
        for trade in to_archive:
            trade_id = trade.get("ID")
            # Find row index
            for idx, record in enumerate(trades_sheet, start=2):
                if record.get("ID") == trade_id:
                    trades_ws.delete_rows(idx)
                    break

        logger.info(f"✅ Archived {len(to_archive)} closed trades")
        return True

    except Exception as e:
        logger.error(f"❌ Archive failed: {e}")
        return False


# ==========================
# MAIN SL ENGINE
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V14 START (with Sheets Updates & Portfolio Tracking)")
    logger.info("=" * 80)

    # Validate environment
    validate_env()

    # Initialize Google Sheets
    sheets = init_google_sheets()
    if not sheets:
        logger.error("❌ Failed to initialize Google Sheets")
        return

    trades_ws = sheets["trades"]
    portfolio_ws = sheets["portfolio"]
    archive_ws = sheets["archive"]

    # Get all trades from Google Sheets
    trades_sheet = get_trades_from_sheets(trades_ws)
    logger.info(f"📊 Trades in Google Sheets: {len(trades_sheet)}")

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

        # Check if stock exists
        existing_trade = find_existing_trade(trades_sheet, symbol, sec_id)

        if not existing_trade:
            logger.warning(f"⚠️ {symbol} NOT in Google Sheets - inserting...")
            success, sl_price = insert_missing_stock(trades_ws, sec_id, symbol, pos["qty"], pos["avgPrice"])
            if success:
                inserted += 1
            trades_sheet = get_trades_from_sheets(trades_ws)
        else:
            existing_trade = find_existing_trade(trades_sheet, symbol, sec_id)
            sl_price = existing_trade.get("SL_Price", "") if existing_trade else ""

        # Get fresh trade details
        trade = find_existing_trade(trades_sheet, symbol, sec_id)
        if not trade:
            logger.error(f"❌ Could not get trade details for {symbol}")
            continue

        entry_price = float(trade.get("Entry_Price") or 0)
        sl_price = trade.get("SL_Price", "")
        previous_sl_price = trade.get("Previous_SL_Price", "")

        if not sl_price or sl_price == "":
            sl_price = round(entry_price * BASE_SL_PCT, 2)
            logger.warning(f"⚠️ SL_Price not set for {symbol} - using default: {sl_price}")
        else:
            sl_price = float(sl_price)

        if not previous_sl_price or previous_sl_price == "":
            previous_sl_price = sl_price
        else:
            previous_sl_price = float(previous_sl_price)

        # Fetch close price from Dhan (NEW)
        close_price = get_close_price_from_dhan(sec_id, symbol)
        if not close_price:
            logger.warning(f"⚠️ Could not fetch close price for {symbol}")
            # Use entry price as fallback
            close_price = entry_price

        # Get LTP for trailing
        ltp = get_ltp_from_dhan(sec_id, symbol)
        if not ltp:
            ltp = close_price

        logger.info(f"   Entry: {entry_price} | Close: {close_price} | LTP: {ltp} | SL: {sl_price}")

        # Calculate PnL (NEW)
        unrealized_pnl, unrealized_pnl_pct = calculate_pnl(entry_price, close_price, pos["qty"])

        # Determine Status (NEW)
        dhan_trigger = None
        if sec_id in sl_map:
            dhan_trigger = sl_map[sec_id].get("triggerPrice")

        status = determine_status(close_price, sl_price, previous_sl_price,
                                  dhan_trigger=dhan_trigger,
                                  qty_in_holdings=pos["qty"],
                                  sheet_qty=pos["qty"])

        logger.info(f"   Status: {status} | Unrealized PnL: {unrealized_pnl} ({unrealized_pnl_pct}%)")

        # Prepare update data
        now = datetime.now(timezone.utc).isoformat()
        update_data = {
            "G": close_price,               # Current_Price
            "M": status,                    # Status
            "N": unrealized_pnl,            # Unrealized_PnL
            "P": unrealized_pnl_pct,        # Unrealized_PnL%
            "Y": now                        # Updated_At
        }

        # ===== KEY LOGIC: Check if close < SL_Price =====
        if close_price < sl_price:
            logger.warning(f"🔴 {symbol} CLOSE ({close_price}) < SL ({sl_price}) - MARKING FOR EXIT")

            if sec_id in sl_map:
                sl_order = sl_map[sec_id]
                if modify_sl_for_exit(sl_order["orderId"], pos["qty"], symbol):
                    marked_exit += 1
                    update_data["M"] = "CLOSE_BELOW_SL"
            else:
                logger.warning(f"⚠️ No SL order found for {symbol}")
        else:
            logger.info(f"✅ {symbol} close price OK")

            # SL exists - check for trailing
            if sec_id in sl_map:
                existing_order = sl_map[sec_id]
                current_trigger = existing_order.get("triggerPrice")

                if ltp:
                    new_trigger = calculate_sl(entry_price, ltp, current_trigger)
                    logger.info(f"   Current SL: {current_trigger} → Calculated: {new_trigger}")

                    if new_trigger > current_trigger:
                        if modify_sl(existing_order["orderId"], pos["qty"], new_trigger, symbol):
                            modified += 1
                            update_data["K"] = new_trigger  # Previous_SL_Price
                            update_data["J"] = new_trigger  # SL_Price
                            update_data["M"] = "TRAILING"
                    else:
                        logger.info(f"✅ SL optimal for {symbol}")
            else:
                logger.warning(f"⚠️ No SL order for {symbol} - placing new SL")
                if place_sl(sec_id, pos["qty"], entry_price, symbol):
                    placed += 1

            time.sleep(0.5)

        # Find row index and update
        for idx, record in enumerate(trades_sheet, start=2):
            if record.get("ID") == trade.get("ID"):
                update_trade_row(trades_ws, idx, update_data)
                break

    # ===== PORTFOLIO & ARCHIVE =====
    logger.info(f"\n{'='*80}")
    logger.info(f"📊 UPDATING PORTFOLIO & ARCHIVING")

    # Refresh trades
    trades_sheet = get_trades_from_sheets(trades_ws)

    # Calculate & update portfolio
    metrics = calculate_portfolio_metrics(trades_sheet)
    if metrics:
        update_portfolio_sheet(portfolio_ws, metrics)

    # Archive closed trades
    archive_closed_trades(trades_ws, archive_ws, trades_sheet)

    # ===== SUMMARY =====
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ SL ENGINE COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"   📊 SL Placed (new): {placed}")
    logger.info(f"   🔄 SL Modified (trailed): {modified}")
    logger.info(f"   🔴 SL Modified (exit): {marked_exit}")
    logger.info(f"   ➕ Stocks Inserted: {inserted}")
    logger.info(f"{'='*80}")

    # Send summary alert
    summary_content = {
        "SL Placed (new)": placed,
        "SL Modified (trailed)": modified,
        "SL Modified (exit)": marked_exit,
        "Stocks Inserted": inserted,
        "Total Positions": len(all_pos),
        "Active Trades": metrics["active_count"] if metrics else 0,
        "Total Open PnL": f"₹{metrics['open_pnl']}" if metrics else "N/A",
        "Total Realized PnL": f"₹{metrics['realized_pnl']}" if metrics else "N/A",
        "Timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_telegram_alert("🚀 SL ENGINE V14 DAILY RUN COMPLETED", summary_content)


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()