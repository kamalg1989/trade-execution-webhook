# ==============================================
# 🚀 ENTRY ENGINE v2.5 (GOOGLE SHEETS VERSION)
# UPDATED: SQLite → Google Sheets (Excel in Drive)
# Accepts token via env var from app.py
# All CRUD operations: Create, Read, Update, Delete
# ==============================================

import os
import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
import uuid
import pandas as pd
import json
import math

# ==========================
# CONFIG
# ==========================
INSTRUMENT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_TOKEN = os.getenv("DHAN_TOKEN")

# Google Sheets Config
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH")
SHEET_NAME = "Trades"  # Name of the sheet in your Google Sheet

# Tick size cache
TICK_SIZE_CACHE = {}

session = requests.Session()

# Global Google Sheets client
gsheet = None


# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


# ==========================
# GOOGLE SHEETS INIT
# ==========================
def init_google_sheets():
    """
    Initialize Google Sheets client using service account.
    Returns: gspread.Worksheet object for the Trades sheet
    """
    global gsheet

    try:
        if not SPREADSHEET_ID:
            log("❌ SPREADSHEET_ID not set in environment")
            return None

        if not SERVICE_ACCOUNT_KEY_PATH:
            log("❌ SERVICE_ACCOUNT_KEY_PATH not set in environment")
            return None

        # Authenticate with service account
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]

        credentials = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_KEY_PATH,
            scopes=scopes
        )

        client = gspread.authorize(credentials)
        log(f"✅ Authenticated with Google Sheets")

        # Open the spreadsheet
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        log(f"✅ Opened spreadsheet: {spreadsheet.title}")

        # Get or create the Trades worksheet
        try:
            gsheet = spreadsheet.worksheet(SHEET_NAME)
            log(f"✅ Using existing sheet: {SHEET_NAME}")
        except gspread.exceptions.WorksheetNotFound:
            log(f"⚠️ Sheet '{SHEET_NAME}' not found, creating...")
            gsheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=15)
            # Add header row
            headers = [
                "ID", "Symbol", "Security_ID", "Qty", "Entry_Price",
                "Entry_Time", "Status", "SL_Price", "Target_Price",
                "Setup_ID", "Current_Price", "PnL", "PnL_Percent",
                "Updated_At", "Dhan_Order_ID"
            ]
            gsheet.insert_row(headers, 1)
            log(f"✅ Created sheet '{SHEET_NAME}' with headers")

        return gsheet

    except Exception as e:
        log(f"❌ Failed to initialize Google Sheets: {e}")
        return None


# ==========================
# TICK SIZE LOGIC (SAME AS BEFORE)
# ==========================
def convert_tick_multiplier_to_decimal(tick_multiplier):
    """
    Convert SEM_TICK_SIZE multiplier to actual decimal tick value.
    """
    try:
        multiplier = float(tick_multiplier)
        if multiplier <= 0:
            return 0.05
        decimal_tick = multiplier / 100.0
        return round(decimal_tick, 4)
    except (ValueError, TypeError):
        return 0.05


def load_tick_sizes():
    """
    Load tick sizes from Dhan instrument master CSV.
    Caches result globally to avoid repeated downloads.
    """
    global TICK_SIZE_CACHE

    if TICK_SIZE_CACHE:
        log(f"✅ Using cached tick sizes ({len(TICK_SIZE_CACHE)} symbols)")
        return TICK_SIZE_CACHE

    try:
        log("📥 Loading tick sizes from Dhan instrument master...")
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df = pd.read_csv(url, low_memory=False)

        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]

        for _, row in df.iterrows():
            symbol = str(row.get('SEM_TRADING_SYMBOL', '')).strip().upper()
            tick_multiplier = row.get('SEM_TICK_SIZE', 5)
            tick_decimal = convert_tick_multiplier_to_decimal(tick_multiplier)

            if symbol:
                TICK_SIZE_CACHE[symbol] = tick_decimal

        log(f"✅ Loaded tick sizes for {len(TICK_SIZE_CACHE)} NSE equity symbols")
        return TICK_SIZE_CACHE

    except Exception as e:
        log(f"❌ Failed to load tick sizes: {e}")
        return {}


def get_tick_size(symbol):
    """Get tick size for a symbol"""
    global TICK_SIZE_CACHE

    if not TICK_SIZE_CACHE:
        load_tick_sizes()

    symbol_clean = symbol.replace(".NS", "").strip().upper()
    tick = TICK_SIZE_CACHE.get(symbol_clean, 0.05)

    log(f"   [{symbol}] Tick size: ₹{tick:.4f}")
    return tick


def round_to_tick(price, tick, mode="up"):
    """Round price to nearest tick"""
    if tick <= 0:
        return round(price, 4)

    steps = price / tick

    if mode == "up":
        rounded_price = math.ceil(steps) * tick
    elif mode == "down":
        rounded_price = math.floor(steps) * tick
    else:
        rounded_price = round(steps) * tick

    return round(rounded_price, 4)


# ==========================
# LOAD INSTRUMENTS
# ==========================
def load_instruments():
    try:
        df = pd.read_csv(INSTRUMENT_URL, low_memory=False)
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]
        df['SEM_TRADING_SYMBOL'] = df['SEM_TRADING_SYMBOL'].astype(str).str.strip().str.upper()
        log(f"✅ Instruments Loaded: {len(df)}")
        return df
    except Exception as e:
        log(f"❌ Failed to load instruments: {e}")
        return pd.DataFrame()


INSTRUMENT_DF = load_instruments()


# ==========================
# GET SECURITY ID
# ==========================
def get_security_id(stock):
    symbol = stock.replace(".NS", "").strip().upper()
    row = INSTRUMENT_DF[INSTRUMENT_DF['SEM_TRADING_SYMBOL'] == symbol]

    if row.empty:
        log(f"❌ Security ID NOT FOUND: {symbol}")
        return None

    sec_id = str(row.iloc[0]['SEM_SMST_SECURITY_ID'])
    log(f"✅ {symbol} → Security ID: {sec_id}")
    return sec_id


# ==========================
# GOOGLE SHEETS CRUD OPERATIONS
# ==========================

def get_all_trades():
    """
    Get all trades from Google Sheet as list of dicts
    Returns: List of trade dictionaries or empty list if error
    """
    try:
        if not gsheet:
            log("❌ Google Sheets not initialized")
            return []

        # Get all values (including header)
        all_values = gsheet.get_all_values()

        if len(all_values) < 2:
            log("⚠️ No trades found in sheet")
            return []

        headers = all_values[0]
        trades = []

        for row in all_values[1:]:
            if len(row) == len(headers):
                trade = dict(zip(headers, row))
                trades.append(trade)

        log(f"✅ Retrieved {len(trades)} trades from Google Sheets")
        return trades

    except Exception as e:
        log(f"❌ Error reading trades: {e}")
        return []


def find_trade_row(symbol, dhan_order_id=None):
    """
    Find row number of a trade by symbol or dhan_order_id
    Returns: row_number (int) or None if not found
    """
    try:
        if not gsheet:
            return None

        all_values = gsheet.get_all_values()

        if len(all_values) < 2:
            return None

        headers = all_values[0]
        symbol_col = headers.index("Symbol") + 1
        order_id_col = headers.index("Dhan_Order_ID") + 1

        for idx, row in enumerate(all_values[1:], start=2):
            if dhan_order_id and len(row) > order_id_col - 1:
                if row[order_id_col - 1] == dhan_order_id:
                    return idx

            if len(row) > symbol_col - 1:
                if row[symbol_col - 1].upper() == symbol.upper():
                    return idx

        return None

    except Exception as e:
        log(f"❌ Error finding trade: {e}")
        return None


def save_trade(symbol, sec_id, qty, entry_price, sl_price, target_price, setup_id, dhan_order_id):
    """
    Add new trade to Google Sheet
    Returns: True/False
    """
    try:
        if not gsheet:
            log("❌ Google Sheets not initialized")
            return False

        ts = datetime.now(timezone.utc).isoformat()
        trade_id = str(uuid.uuid4())[:8]

        new_row = [
            trade_id,           # ID
            symbol,             # Symbol
            sec_id,             # Security_ID
            qty,                # Qty
            entry_price,        # Entry_Price
            ts,                 # Entry_Time
            "OPEN",             # Status
            sl_price,           # SL_Price
            target_price,       # Target_Price
            setup_id,           # Setup_ID
            entry_price,        # Current_Price (initially same as entry)
            0,                  # PnL
            0,                  # PnL_Percent
            ts,                 # Updated_At
            dhan_order_id       # Dhan_Order_ID
        ]

        gsheet.append_row(new_row)
        log(f"✅ Trade saved: {symbol} (Order ID: {dhan_order_id})")
        return True

    except Exception as e:
        log(f"❌ Failed to save trade: {e}")
        return False


def update_trade(symbol=None, dhan_order_id=None, **kwargs):
    """
    Update an existing trade by symbol or dhan_order_id

    Example:
        update_trade(symbol="ONGC", status="CLOSED", current_price=150.5)
        update_trade(dhan_order_id="12345", exit_price=150.5, status="CLOSED")
    """
    try:
        if not gsheet:
            log("❌ Google Sheets not initialized")
            return False

        row_num = find_trade_row(symbol, dhan_order_id)

        if not row_num:
            log(f"❌ Trade not found: {symbol or dhan_order_id}")
            return False

        # Get headers
        headers = gsheet.row_values(1)

        # Get current row
        current_row = gsheet.row_values(row_num)

        # Update with provided values
        for key, value in kwargs.items():
            if key in headers:
                col_idx = headers.index(key) + 1
                gsheet.update_cell(row_num, col_idx, value)

        # Update timestamp
        updated_at_col = headers.index("Updated_At") + 1
        ts = datetime.now(timezone.utc).isoformat()
        gsheet.update_cell(row_num, updated_at_col, ts)

        log(f"✅ Trade updated: {symbol or dhan_order_id}")
        return True

    except Exception as e:
        log(f"❌ Failed to update trade: {e}")
        return False


def delete_trade(symbol=None, dhan_order_id=None):
    """
    Delete a trade from Google Sheet by symbol or dhan_order_id
    """
    try:
        if not gsheet:
            log("❌ Google Sheets not initialized")
            return False

        row_num = find_trade_row(symbol, dhan_order_id)

        if not row_num:
            log(f"❌ Trade not found: {symbol or dhan_order_id}")
            return False

        gsheet.delete_rows(row_num)
        log(f"✅ Trade deleted: {symbol or dhan_order_id}")
        return True

    except Exception as e:
        log(f"❌ Failed to delete trade: {e}")
        return False


def filter_trades(status=None, symbol=None):
    """
    Filter trades by status (OPEN/CLOSED) or symbol
    Returns: List of filtered trades
    """
    try:
        trades = get_all_trades()
        filtered = trades

        if status:
            filtered = [t for t in filtered if t.get("Status", "").upper() == status.upper()]

        if symbol:
            filtered = [t for t in filtered if t.get("Symbol", "").upper() == symbol.upper()]

        log(f"✅ Filtered trades: {len(filtered)}")
        return filtered

    except Exception as e:
        log(f"❌ Error filtering trades: {e}")
        return []


# ==========================
# CHECK DHAN FOR EXISTING ORDERS
# ==========================
def check_dhan_for_existing_buy(symbol, token):
    """
    Check /v2/forever/orders for existing BUY orders on this symbol.
    """
    try:
        if not token:
            log("❌ No token provided from parent")
            return False

        log(f"📡 GET /v2/forever/orders (using parent token)...")

        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=30
        )

        if r.status_code != 200:
            log(f"⚠️ API error: {r.status_code}")
            return False

        orders = r.json()
        if not isinstance(orders, list):
            log(f"⚠️ Expected list, got {type(orders)}")
            return False

        log(f"   Total orders: {len(orders)}")

        symbol_upper = symbol.upper().replace(".NS", "")

        for order in orders:
            if not isinstance(order, dict):
                continue

            order_symbol = order.get("tradingSymbol", "").strip().upper()
            trans_type = order.get("transactionType", "")
            status = order.get("orderStatus", "")

            if order_symbol == symbol_upper and trans_type == "BUY":
                if status in ["PENDING", "TRIGGERED", "CONFIRM", "ACCEPTED"]:
                    log(f"⚠️ Found open BUY: Status={status}")
                    return True

        log(f"✅ No open BUY orders for {symbol}")
        return False

    except Exception as e:
        log(f"❌ Error checking Dhan: {e}")
        return False


# ==========================
# PLACE ORDER
# ==========================
def place_order(sec_id, qty, entry, symbol, token):
    """
    Place BUY order on Dhan using token from parent.
    """
    try:
        tick = get_tick_size(symbol)
        trigger = round_to_tick(entry, tick, mode="down")
        price = round_to_tick(entry * 1.002, tick, mode="up")

        if price <= trigger:
            price = round_to_tick(trigger + tick, tick, mode="up")

        log(f"   Tick size: ₹{tick:.4f}")
        log(f"   Raw entry: {entry}")
        log(f"   Rounded trigger: {trigger}")
        log(f"   Rounded price: {price}")

        payload = {
            "dhanClientId": DHAN_CLIENT_ID,
            "correlationId": str(uuid.uuid4()).replace("-", "")[:20],
            "orderFlag": "SINGLE",
            "transactionType": "BUY",
            "exchangeSegment": "NSE_EQ",
            "productType": "CNC",
            "orderType": "LIMIT",
            "validity": "DAY",
            "securityId": sec_id,
            "quantity": qty,
            "price": price,
            "triggerPrice": trigger
        }

        if not token:
            log("❌ No token provided from parent")
            return False, {"error": "no_token"}

        try:
            log(f"📤 Placing BUY: Qty={qty}, Entry={entry}, Trigger={trigger}, Price={price}")

            r = session.post(
                "https://api.dhan.co/v2/forever/orders",
                json=payload,
                headers={
                    "access-token": token,
                    "Content-Type": "application/json"
                },
                timeout=15
            )

            if r.status_code not in (200, 201):
                log(f"❌ Order placement failed: {r.status_code}")
                log(f"   Response: {r.text[:200]}")
                return False, {"error": f"http_{r.status_code}"}

            data = r.json()
            log(f"   Response: {data}")

            return True, data

        except Exception as e:
            log(f"❌ Order placement exception: {e}")
            return False, {"error": "exception"}

    except Exception as e:
        log(f"❌ Error in place_order: {e}")
        return False, {"error": "exception"}


# ==========================
# MAIN
# ==========================
def run():
    global gsheet

    log("=" * 80)
    log("🚀 ENTRY ENGINE v2.5 (GOOGLE SHEETS VERSION)")
    log("=" * 80)

    # Initialize Google Sheets
    gsheet = init_google_sheets()
    if not gsheet:
        log("❌ Failed to initialize Google Sheets")
        return

    # Read env vars
    symbol = os.getenv("SYMBOL", "").strip()
    qty = int(os.getenv("QTY", "0") or "0")
    entry = float(os.getenv("ENTRY", "0") or "0.0")
    sl = float(os.getenv("SL", "0") or "0.0")
    target = float(os.getenv("TARGET", "0") or "0.0")
    score = float(os.getenv("SCORE", "0") or "0.0")
    setup_id = os.getenv("SETUP_ID", "")

    token = os.getenv("DHAN_TOKEN")

    log(f"Input: {symbol} | Qty={qty} | Entry={entry} | SL={sl} | Target={target}")
    log(f"Token from parent: {token[:30] if token else 'NOT PROVIDED'}...")

    # ==== VALIDATION ====
    if not symbol or qty <= 0 or entry <= 0:
        log("❌ Invalid inputs")
        return

    if sl <= 0 or target <= 0:
        log("❌ SL or TARGET missing")
        return

    if not (sl < entry < target):
        log(f"❌ Invalid price order: SL={sl} < ENTRY={entry} < TARGET={target}")
        return

    if not token:
        log("❌ No token provided from parent!")
        return

    # ==== GET SECURITY ID ====
    sec_id = get_security_id(symbol)
    if not sec_id:
        log(f"❌ Security ID not found for {symbol}")
        return

    # ==== CHECK DHAN FOR EXISTING ORDERS ====
    log(f"\n🔍 Checking Dhan for existing orders on {symbol}...")

    if check_dhan_for_existing_buy(symbol, token):
        log(f"⚠️ {symbol} already has open BUY order - SKIPPING")
        return

    log(f"✅ {symbol} is clear on Dhan\n")

    # ==== PLACE ORDER ====
    log("=" * 80)
    log("📤 PLACING ORDER ON DHAN")
    log("=" * 80)

    success, response = place_order(sec_id, qty, entry, symbol, token)

    if not success:
        log(f"❌ Order placement failed")
        log(f"   Error: {response.get('error')}")
        return

    # ==== SUCCESS ====
    dhan_order_id = response.get("orderId")
    order_status = response.get("orderStatus")

    if not dhan_order_id:
        log(f"❌ No orderId in response")
        return

    log(f"\n✅ ORDER PLACED SUCCESSFULLY!")
    log(f"   Order ID: {dhan_order_id}")
    log(f"   Status: {order_status}")
    log(f"   Symbol: {symbol}")
    log(f"   Qty: {qty}")

    # Save to Google Sheets
    if save_trade(symbol, sec_id, qty, entry, sl, target, setup_id, dhan_order_id):
        log(f"✅ Trade recorded in Google Sheets")

        result = {
            "success": True,
            "order_id": dhan_order_id,
            "symbol": symbol,
            "qty": qty,
            "entry": entry,
            "message": "Order placed successfully"
        }
        print(json.dumps(result))

    log("=" * 80)


if __name__ == "__main__":
    run()