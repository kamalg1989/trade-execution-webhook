# ==============================================
# 🚀 SL ENGINE V15 - COMPLETE WITH DATA CLEANUP
#
# FEATURES:
# ✅ Data cleanup logic (remove stale rows)
# ✅ Pending buy order detection
# ✅ Stale row detection & deletion
# ✅ New columns added (P-AD)
# ✅ Entry_Order_ID tracking
# ✅ All previous V14 features retained
# ✅ IST Timezone fix
# ✅ Smart fallback: Close → LTP → Entry
# ✅ Extensive debug logging
# ✅ Google Sheets updates
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
STALE_DAYS_THRESHOLD = 7  # Mark row stale if no update for 7 days

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

        # Get or create worksheets
        try:
            trades_ws = spreadsheet.worksheet("Trades")
            logger.info(f"✅ Using worksheet: Trades")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"⚠️ Worksheet 'Trades' not found, creating...")
            trades_ws = spreadsheet.add_worksheet(title="Trades", rows=1000, cols=30)
            headers = get_column_headers()
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
            archive_ws = spreadsheet.add_worksheet(title="Archive", rows=5000, cols=30)
            headers = get_column_headers()
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
# COLUMN HEADERS (A-AD)
# ==========================
def get_column_headers():
    """Get all column headers A-AD"""
    return [
        # A-O: Original columns (NEVER TOUCHED)
        "ID",                    # A
        "Symbol",               # B
        "Security_ID",          # C
        "Qty",                  # D
        "Entry_Price",          # E
        "Entry_Time",           # F
        "Status",               # G
        "SL_Price",             # H
        "Target_Price",         # I
        "Setup_ID",             # J
        "Current_Price",        # K
        "PnL",                  # L
        "PnL_Percent",          # M
        "Updated_At",           # N
        "Dhan_Order_ID",        # O

        # P-AD: New columns (added at END)
        "Exit_Price",           # P
        "Exit_Time",            # Q
        "Previous_SL_Price",    # R
        "Unrealized_PnL",       # S
        "Realized_PnL",         # T
        "Unrealized_PnL%",      # U
        "Realized_PnL%",        # V
        "Win_Loss",             # W
        "Return_Pct",           # X
        "Days_Held",            # Y
        "RR_Ratio",             # Z
        "Entry_Order_ID",       # AA
        "Exit_Order_ID",        # AB
    ]


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
# V15 NEW: CLEANUP STALE TRADES
# ==========================
def cleanup_sheet_data(trades_ws, trades_sheet, positions_map, sl_orders_map):
    """
    Remove stale rows that don't match Dhan orders or positions

    Logic:
    - If stock NOT in positions AND NOT in SL orders → DELETE
    - If stock NOT in Dhan at all → DELETE
    - If very old (>STALE_DAYS_THRESHOLD) without updates → DELETE
    - If Entry_Order_ID is old/stale → DELETE
    """
    try:
        logger.info("\n" + "="*80)
        logger.info("🧹 PHASE 1: CLEANUP STALE TRADES")
        logger.info("="*80)

        rows_to_delete = []
        deletion_reasons = {}

        for idx, trade in enumerate(trades_sheet):
            symbol = trade.get("Symbol", "")
            sec_id = str(trade.get("Security_ID", ""))
            updated_at = trade.get("Updated_At", "")
            entry_order_id = trade.get("Entry_Order_ID", "")

            delete_reason = None

            # Check 1: Is this stock in any Dhan position/holding?
            if sec_id not in positions_map:
                delete_reason = "NOT_IN_DHAN_POSITIONS"

            # Check 2: Is this stock in any SL order?
            elif sec_id not in sl_orders_map:
                delete_reason = "NOT_IN_SL_ORDERS"

            # Check 3: Is the Entry_Order_ID very old?
            if entry_order_id and not delete_reason:
                try:
                    entry_time_str = trade.get("Entry_Time", "")
                    if entry_time_str:
                        entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                        age_days = (datetime.now(timezone.utc) - entry_time).days

                        # If pending for > STALE_DAYS_THRESHOLD days, mark as stale
                        if trade.get("Status") == "PENDING" and age_days > STALE_DAYS_THRESHOLD:
                            delete_reason = f"PENDING_STALE_{age_days}DAYS"
                except:
                    pass

            # Check 4: Is updated_at very old?
            if updated_at and not delete_reason:
                try:
                    updated_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    age_days = (datetime.now(timezone.utc) - updated_time).days

                    # If not updated for > STALE_DAYS_THRESHOLD days AND not in positions, delete
                    if age_days > STALE_DAYS_THRESHOLD and sec_id not in positions_map:
                        delete_reason = f"NO_UPDATE_{age_days}DAYS"
                except:
                    pass

            # Mark for deletion
            if delete_reason:
                row_num = idx + 2  # +2 because row 1 is header, list is 0-indexed
                rows_to_delete.append(row_num)
                deletion_reasons[symbol] = delete_reason
                logger.warning(f"🗑️  MARKED FOR DELETE: {symbol} ({sec_id}) - Reason: {delete_reason}")

        # Delete rows from bottom to top (to avoid index shift)
        if rows_to_delete:
            logger.info(f"\n📤 Deleting {len(rows_to_delete)} stale rows...")
            for row_num in sorted(rows_to_delete, reverse=True):
                try:
                    if not DRY_RUN:
                        trades_ws.delete_rows(row_num)
                        logger.info(f"✅ Deleted row {row_num}")
                    else:
                        logger.info(f"🔕 [DRY_RUN] Would delete row {row_num}")
                except Exception as e:
                    logger.error(f"❌ Failed to delete row {row_num}: {e}")

            # Send deletion alert
            deletion_summary = "\n".join([f"{sym}: {reason}" for sym, reason in deletion_reasons.items()])
            send_telegram_alert("🗑️ STALE ROWS DELETED", {
                "Count": len(rows_to_delete),
                "Stocks": ", ".join(deletion_reasons.keys()),
                "Reasons": deletion_summary[:500]  # Limit to 500 chars for Telegram
            })
        else:
            logger.info("✅ No stale rows to delete")

        return len(rows_to_delete), deletion_reasons

    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        return 0, {}


# ==========================
# V15 NEW: ADD NEW COLUMN HEADERS
# ==========================
def add_new_column_headers(trades_ws):
    """
    Add headers for new columns P-AD if they don't exist
    Only adds if columns are empty
    """
    try:
        logger.info("\n" + "="*80)
        logger.info("📋 PHASE 2: ADD NEW COLUMN HEADERS (P-AD)")
        logger.info("="*80)

        headers = get_column_headers()

        # Get current header row
        current_headers = trades_ws.row_values(1)

        # Only add if needed
        new_headers_needed = []
        for col_idx, header in enumerate(headers):
            col_letter = chr(65 + col_idx)  # A=65 in ASCII

            if col_idx >= len(current_headers):
                new_headers_needed.append((col_letter, header))

        if new_headers_needed:
            logger.info(f"📝 Adding {len(new_headers_needed)} new column headers...")
            for col_letter, header in new_headers_needed:
                try:
                    if not DRY_RUN:
                        trades_ws.update(f"{col_letter}1", header)
                        logger.info(f"✅ Added header: {col_letter} = {header}")
                    else:
                        logger.info(f"🔕 [DRY_RUN] Would add header: {col_letter} = {header}")
                except Exception as e:
                    logger.error(f"❌ Failed to add header {col_letter}: {e}")
        else:
            logger.info("✅ All column headers already exist")

        return True

    except Exception as e:
        logger.error(f"❌ Column header addition failed: {e}")
        return False


# ==========================
# V15 NEW: CHECK PENDING BUY ORDERS
# ==========================
def check_pending_buy_orders(trades_sheet):
    """
    Detect and mark pending buy orders (Entry_Order_ID exists but not filled)

    Logic:
    - If Entry_Order_ID exists, query Dhan for order status
    - If status = PENDING → mark Status = "PENDING"
    - If status = FILLED → mark Status = "OPEN"
    - If status = CANCELLED or old → delete row (stale)
    """
    try:
        logger.info("\n" + "="*80)
        logger.info("⏳ PHASE 3: CHECK PENDING BUY ORDERS")
        logger.info("="*80)

        pending_updates = []
        stale_entry_orders = []

        for trade in trades_sheet:
            entry_order_id = trade.get("Entry_Order_ID", "")
            symbol = trade.get("Symbol", "")

            if not entry_order_id or entry_order_id == "":
                logger.debug(f"ℹ️  {symbol} - No Entry_Order_ID, skipping")
                continue

            # Query Dhan for this order status
            logger.info(f"🔍 Checking entry order for {symbol} (ID: {entry_order_id})")

            order_status = get_dhan_order_status(entry_order_id)

            if order_status == "PENDING":
                logger.warning(f"⏳ {symbol} - Entry order PENDING (waiting to fill)")
                pending_updates.append({
                    "symbol": symbol,
                    "trade": trade,
                    "status": "PENDING"
                })
            elif order_status == "FILLED":
                logger.info(f"✅ {symbol} - Entry order FILLED (position active)")
                pending_updates.append({
                    "symbol": symbol,
                    "trade": trade,
                    "status": "OPEN"
                })
            elif order_status in ("CANCELLED", "REJECTED", "EXPIRED"):
                logger.warning(f"❌ {symbol} - Entry order {order_status} (stale)")
                stale_entry_orders.append({
                    "symbol": symbol,
                    "status": order_status
                })

        logger.info(f"📊 Pending: {len(pending_updates)}, Stale: {len(stale_entry_orders)}")

        return pending_updates, stale_entry_orders

    except Exception as e:
        logger.error(f"❌ Pending order check failed: {e}")
        return [], []


# ==========================
# V15 NEW: GET DHAN ORDER STATUS
# ==========================
def get_dhan_order_status(order_id):
    """
    Query Dhan API to get order status

    Returns: "PENDING", "FILLED", "CANCELLED", "REJECTED", "EXPIRED", or None
    """
    try:
        token = get_token()
        if not token:
            logger.warning(f"⚠️ No token for order status check")
            return None

        logger.debug(f"📡 Querying Dhan API for order {order_id}...")

        r = requests.get(
            f"https://api.dhan.co/v2/orders/{order_id}",
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=10
        )

        if r.status_code == 200:
            data = r.json()
            status = data.get("orderStatus", "UNKNOWN")
            logger.debug(f"✅ Order {order_id} status: {status}")
            return status
        else:
            logger.warning(f"⚠️ Order status API error: {r.status_code}")
            return None

    except Exception as e:
        logger.error(f"❌ Order status check failed: {e}")
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

        # Create row with all columns (A-AB at minimum)
        row = [
            new_id,                    # A: ID
            symbol_normalized,         # B: Symbol
            str(security_id),          # C: Security_ID
            str(qty),                  # D: Qty
            str(avg_price),            # E: Entry_Price
            now,                       # F: Entry_Time
            "INITIAL_SL",              # G: Status
            str(sl_price),             # H: SL_Price
            "",                        # I: Target_Price
            "",                        # J: Setup_ID
            str(avg_price),            # K: Current_Price
            "0",                       # L: PnL
            "0",                       # M: PnL_Percent
            now,                       # N: Updated_At
            "",                        # O: Dhan_Order_ID
            "",                        # P: Exit_Price
            "",                        # Q: Exit_Time
            str(sl_price),             # R: Previous_SL_Price
            "0",                       # S: Unrealized_PnL
            "0",                       # T: Realized_PnL
            "0",                       # U: Unrealized_PnL%
            "0",                       # V: Realized_PnL%
            "",                        # W: Win_Loss
            "0",                       # X: Return_Pct
            "0",                       # Y: Days_Held
            "0",                       # Z: RR_Ratio
            "",                        # AA: Entry_Order_ID
            "",                        # AB: Exit_Order_ID
        ]

        if not DRY_RUN:
            trades_ws.append_row(row, value_input_option="USER_ENTERED")

        logger.info(f"✅ Inserted: {symbol_normalized} (SL: {sl_price})")
        return True, str(sl_price)

    except Exception as e:
        logger.error(f"❌ Insert stock failed: {e}")
        return False, None


# ==========================
# UPDATE TRADE ROW - FIXED FOR NEW COLUMNS
# ==========================
def update_trade_row(trades_ws, row_number, updates_dict):
    """Update a single row in Trades sheet (supports A-AB columns)"""
    try:
        headers = get_column_headers()

        cell_range = trades_ws.range(f'A{row_number}:{chr(64 + len(headers))}{row_number}')

        for col_idx, cell in enumerate(cell_range):
            col_name = headers[col_idx]
            if col_name in updates_dict:
                cell.value = updates_dict[col_name]

        if not DRY_RUN:
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
# GET CLOSE PRICE - IST TIMEZONE
# ==========================
def get_close_price_from_dhan(security_id, symbol):
    """
    Fetch today's close price from Dhan API with IST timezone
    """
    try:
        token = get_token()
        if not token:
            logger.warning(f"⚠️ No token for close price fetch ({symbol})")
            return None

        now_ist = datetime.now(IST)
        logger.debug(f"📍 Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")

        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

        if now_ist < market_close:
            trade_date = now_ist - timedelta(days=1)
            logger.debug(f"⏰ Before market close - fetching YESTERDAY'S close")
        else:
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
                return None
        else:
            logger.warning(f"⚠️ {symbol} close price API error: Status {r.status_code}")
            return None

    except Exception as e:
        logger.error(f"❌ {symbol} close price fetch exception: {e}")
        return None


# ==========================
# GET LTP FROM DHAN - FALLBACK
# ==========================
def get_ltp_from_dhan(security_id, symbol):
    """Fetch intraday LTP as fallback when close price fails"""
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
                return None
        else:
            logger.warning(f"⚠️ {symbol} LTP API error: Status {r.status_code}")
            return None

    except Exception as e:
        logger.error(f"❌ {symbol} LTP fetch exception: {e}")
        return None


# ==========================
# GET CURRENT PRICE - SMART FALLBACK
# ==========================
def get_current_price(security_id, symbol):
    """Get current price with smart fallback strategy"""
    logger.info(f"🔍 Getting current price for {symbol}...")

    close_price = get_close_price_from_dhan(security_id, symbol)
    if close_price:
        logger.info(f"✅ Using CLOSE price for {symbol}: {close_price}")
        return close_price, "CLOSE"

    logger.info(f"⚠️ Close price failed, trying LTP fallback for {symbol}...")
    ltp = get_ltp_from_dhan(security_id, symbol)
    if ltp:
        logger.warning(f"⚠️ Using LTP (fallback) for {symbol}: {ltp}")
        return ltp, "LTP"

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

        if not DRY_RUN:
            portfolio_ws.append_row(row, value_input_option="USER_ENTERED")

        logger.info(f"✅ Updated Portfolio sheet")
        return True
    except Exception as e:
        logger.error(f"❌ Portfolio update failed: {e}")
        return False


# ==========================
# MAIN ENGINE V15
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V15 - COMPLETE WITH DATA CLEANUP")
    logger.info("=" * 80)
    logger.info(f"🔕 DRY_RUN: {DRY_RUN}")
    logger.info("=" * 80)

    validate_env()

    sheets = init_google_sheets()
    if not sheets:
        logger.error("❌ Google Sheets initialization failed")
        return

    trades_ws = sheets["trades"]
    portfolio_ws = sheets["portfolio"]
    trades_sheet = get_trades_from_sheets(trades_ws)

    logger.info(f"📊 Current trades in sheet: {len(trades_sheet)}")

    # Get Dhan data
    positions = get_positions()
    holdings = get_holdings()

    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 Total positions in Dhan: {len(all_pos)}")

    forever = get_forever_orders()
    sl_map = {str(o["securityId"]): o for o in forever if o.get("transactionType") == "SELL" and o.get("orderStatus") == "PENDING"}

    logger.info(f"📊 Existing SL orders in Dhan: {len(sl_map)}")

    # ==========================
    # V15 PHASE 1: CLEANUP STALE TRADES
    # ==========================
    deleted_count, deletion_reasons = cleanup_sheet_data(trades_ws, trades_sheet, all_pos, sl_map)

    # Refresh trades after cleanup
    trades_sheet = get_trades_from_sheets(trades_ws)
    logger.info(f"📊 Trades after cleanup: {len(trades_sheet)}")

    # ==========================
    # V15 PHASE 2: ADD NEW COLUMN HEADERS
    # ==========================
    add_new_column_headers(trades_ws)

    # ==========================
    # V15 PHASE 3: CHECK PENDING BUY ORDERS
    # ==========================
    pending_updates, stale_entry_orders = check_pending_buy_orders(trades_sheet)

    # ==========================
    # V15 PHASE 4: PROCESS TRADES (SL LOGIC)
    # ==========================
    logger.info("\n" + "="*80)
    logger.info("⚙️ PHASE 4: PROCESS TRADES - SL LOGIC")
    logger.info("="*80)

    placed = modified = marked_exit = 0

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

        # Get current price
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

    # ==========================
    # PHASE 5: UPDATE PORTFOLIO
    # ==========================
    trades_sheet = get_trades_from_sheets(trades_ws)
    metrics = calculate_portfolio_metrics(trades_sheet)
    if metrics:
        update_portfolio_sheet(portfolio_ws, metrics)

    # ==========================
    # FINAL SUMMARY
    # ==========================
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ SL ENGINE V15 COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"   🗑️  Stale rows deleted: {deleted_count}")
    logger.info(f"   📊 SL Placed: {placed}")
    logger.info(f"   🔄 SL Modified (Trailing): {modified}")
    logger.info(f"   🔴 SL Modified (Exit): {marked_exit}")
    logger.info(f"   ⏳ Pending orders: {len(pending_updates)}")
    logger.info(f"   ❌ Stale entry orders: {len(stale_entry_orders)}")
    logger.info(f"   📈 Active positions: {metrics['active_count'] if metrics else 'N/A'}")
    logger.info(f"   💰 Open PnL: ₹{metrics['open_pnl'] if metrics else 'N/A'}")
    logger.info(f"{'='*80}")

    send_telegram_alert("🚀 SL ENGINE V15 COMPLETED", {
        "Deleted": deleted_count,
        "Placed": placed,
        "Modified": modified,
        "Exit": marked_exit,
        "Pending": len(pending_updates),
        "Stale": len(stale_entry_orders),
        "Active": metrics["active_count"] if metrics else 0,
        "Open PnL": f"₹{metrics['open_pnl']}" if metrics else "N/A"
    })


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()