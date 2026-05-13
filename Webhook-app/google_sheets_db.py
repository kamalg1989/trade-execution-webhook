# ==============================================
# 📊 GOOGLE SHEETS DATABASE MODULE
# Standalone module for all trades database operations
# Can be imported and used separately from entry_engine
# ==============================================

import os
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
import uuid
import json

# ==========================
# CONFIG
# ==========================
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH")
SHEET_NAME = "Trades"

# Global sheet object
_gsheet = None


# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


# ==========================
# INITIALIZATION
# ==========================
def init_sheets():
    """
    Initialize Google Sheets connection
    Returns: gspread.Worksheet object
    """
    global _gsheet

    try:
        if not SPREADSHEET_ID or not SERVICE_ACCOUNT_KEY_PATH:
            raise ValueError("SPREADSHEET_ID or SERVICE_ACCOUNT_KEY_PATH not set")

        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]

        credentials = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_KEY_PATH,
            scopes=scopes
        )

        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)

        try:
            _gsheet = spreadsheet.worksheet(SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            log(f"Creating sheet '{SHEET_NAME}'...")
            _gsheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=15)

            headers = [
                "ID", "Symbol", "Security_ID", "Qty", "Entry_Price",
                "Entry_Time", "Status", "SL_Price", "Target_Price",
                "Setup_ID", "Current_Price", "PnL", "PnL_Percent",
                "Updated_At", "Dhan_Order_ID"
            ]
            _gsheet.insert_row(headers, 1)

        log("✅ Google Sheets initialized")
        return _gsheet

    except Exception as e:
        log(f"❌ Failed to init sheets: {e}")
        raise


# ==========================
# CRUD OPERATIONS
# ==========================

def get_sheet():
    """Get the sheet object, initialize if needed"""
    global _gsheet
    if not _gsheet:
        init_sheets()
    return _gsheet


def add_trade(symbol, sec_id, qty, entry_price, sl_price, target_price, setup_id, dhan_order_id):
    """
    Add a new trade to the sheet

    Args:
        symbol: Stock symbol (e.g., "ONGC")
        sec_id: Security ID from Dhan
        qty: Quantity
        entry_price: Entry price
        sl_price: Stop loss price
        target_price: Target price
        setup_id: Setup ID (optional)
        dhan_order_id: Dhan order ID

    Returns: Trade ID or None if failed
    """
    try:
        sheet = get_sheet()
        ts = datetime.now(timezone.utc).isoformat()
        trade_id = str(uuid.uuid4())[:8]

        row = [
            trade_id, symbol, sec_id, qty, entry_price, ts, "OPEN",
            sl_price, target_price, setup_id, entry_price, 0, 0, ts, dhan_order_id
        ]

        sheet.append_row(row)
        log(f"✅ Added trade: {symbol} (ID: {trade_id})")
        return trade_id

    except Exception as e:
        log(f"❌ Failed to add trade: {e}")
        return None


def get_trade(symbol=None, trade_id=None, dhan_order_id=None):
    """
    Get a single trade by symbol, trade_id, or dhan_order_id
    Returns: Trade dict or None
    """
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if len(all_values) < 2:
            return None

        headers = all_values[0]

        for row in all_values[1:]:
            if len(row) < len(headers):
                continue

            trade = dict(zip(headers, row))

            if symbol and trade.get("Symbol", "").upper() == symbol.upper():
                return trade

            if trade_id and trade.get("ID") == trade_id:
                return trade

            if dhan_order_id and trade.get("Dhan_Order_ID") == dhan_order_id:
                return trade

        return None

    except Exception as e:
        log(f"❌ Failed to get trade: {e}")
        return None


def get_all_trades():
    """Get all trades as list of dicts"""
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if len(all_values) < 2:
            return []

        headers = all_values[0]
        trades = []

        for row in all_values[1:]:
            if len(row) >= len(headers):
                trades.append(dict(zip(headers, row)))

        log(f"✅ Retrieved {len(trades)} trades")
        return trades

    except Exception as e:
        log(f"❌ Failed to get trades: {e}")
        return []


def update_trade(symbol=None, trade_id=None, dhan_order_id=None, **updates):
    """
    Update a trade by symbol, trade_id, or dhan_order_id

    Example:
        update_trade(symbol="ONGC", status="CLOSED", current_price=150.5)
        update_trade(trade_id="abc123", exit_price=150, pnl=500)

    Returns: True/False
    """
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if len(all_values) < 2:
            return False

        headers = all_values[0]
        row_num = None

        # Find row
        for idx, row in enumerate(all_values[1:], start=2):
            if len(row) < len(headers):
                continue

            trade = dict(zip(headers, row))

            if symbol and trade.get("Symbol", "").upper() == symbol.upper():
                row_num = idx
                break

            if trade_id and trade.get("ID") == trade_id:
                row_num = idx
                break

            if dhan_order_id and trade.get("Dhan_Order_ID") == dhan_order_id:
                row_num = idx
                break

        if not row_num:
            log(f"❌ Trade not found")
            return False

        # Update cells
        for key, value in updates.items():
            if key in headers:
                col_idx = headers.index(key) + 1
                sheet.update_cell(row_num, col_idx, value)

        # Always update timestamp
        if "Updated_At" in headers:
            col_idx = headers.index("Updated_At") + 1
            ts = datetime.now(timezone.utc).isoformat()
            sheet.update_cell(row_num, col_idx, ts)

        log(f"✅ Updated trade: {symbol or trade_id or dhan_order_id}")
        return True

    except Exception as e:
        log(f"❌ Failed to update trade: {e}")
        return False


def delete_trade(symbol=None, trade_id=None, dhan_order_id=None):
    """Delete a trade by symbol, trade_id, or dhan_order_id"""
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if len(all_values) < 2:
            return False

        headers = all_values[0]
        row_num = None

        # Find row
        for idx, row in enumerate(all_values[1:], start=2):
            if len(row) < len(headers):
                continue

            trade = dict(zip(headers, row))

            if symbol and trade.get("Symbol", "").upper() == symbol.upper():
                row_num = idx
                break

            if trade_id and trade.get("ID") == trade_id:
                row_num = idx
                break

            if dhan_order_id and trade.get("Dhan_Order_ID") == dhan_order_id:
                row_num = idx
                break

        if not row_num:
            log(f"❌ Trade not found")
            return False

        sheet.delete_rows(row_num)
        log(f"✅ Deleted trade: {symbol or trade_id or dhan_order_id}")
        return True

    except Exception as e:
        log(f"❌ Failed to delete trade: {e}")
        return False


# ==========================
# FILTER & QUERY
# ==========================

def filter_by_status(status):
    """Get all trades with a specific status (OPEN, CLOSED, etc.)"""
    try:
        trades = get_all_trades()
        filtered = [t for t in trades if t.get("Status", "").upper() == status.upper()]
        log(f"✅ Found {len(filtered)} trades with status: {status}")
        return filtered
    except Exception as e:
        log(f"❌ Failed to filter: {e}")
        return []


def filter_by_symbol(symbol):
    """Get all trades for a specific symbol"""
    try:
        trades = get_all_trades()
        filtered = [t for t in trades if t.get("Symbol", "").upper() == symbol.upper()]
        log(f"✅ Found {len(filtered)} trades for {symbol}")
        return filtered
    except Exception as e:
        log(f"❌ Failed to filter: {e}")
        return []


def get_open_trades():
    """Get all OPEN trades"""
    return filter_by_status("OPEN")


def get_closed_trades():
    """Get all CLOSED trades"""
    return filter_by_status("CLOSED")


def get_today_trades():
    """Get trades created today"""
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        trades = get_all_trades()

        filtered = [
            t for t in trades
            if t.get("Entry_Time", "").startswith(today)
        ]

        log(f"✅ Found {len(filtered)} trades from today")
        return filtered

    except Exception as e:
        log(f"❌ Failed to get today's trades: {e}")
        return []


def get_summary_stats():
    """Get portfolio summary statistics"""
    try:
        trades = get_all_trades()

        if not trades:
            return {"total": 0, "open": 0, "closed": 0, "total_pnl": 0}

        open_trades = [t for t in trades if t.get("Status") == "OPEN"]
        closed_trades = [t for t in trades if t.get("Status") == "CLOSED"]

        total_pnl = sum([float(t.get("PnL", 0)) for t in trades])

        stats = {
            "total": len(trades),
            "open": len(open_trades),
            "closed": len(closed_trades),
            "total_pnl": total_pnl
        }

        log(f"✅ Summary: {stats}")
        return stats

    except Exception as e:
        log(f"❌ Failed to get stats: {e}")
        return {}


# ==========================
# BATCH OPERATIONS
# ==========================

def update_multiple(updates_list):
    """
    Update multiple trades at once

    Args:
        updates_list: List of dicts with 'symbol'/'trade_id'/'dhan_order_id' + update fields

    Example:
        update_multiple([
            {"symbol": "ONGC", "status": "CLOSED", "current_price": 150},
            {"symbol": "INFY", "current_price": 1500}
        ])
    """
    results = []
    for update in updates_list:
        lookup_key = None
        lookup_value = None

        if "symbol" in update:
            lookup_key = "symbol"
            lookup_value = update.pop("symbol")
        elif "trade_id" in update:
            lookup_key = "trade_id"
            lookup_value = update.pop("trade_id")
        elif "dhan_order_id" in update:
            lookup_key = "dhan_order_id"
            lookup_value = update.pop("dhan_order_id")

        if lookup_key:
            kwargs = {lookup_key: lookup_value}
            kwargs.update(update)
            result = update_trade(**kwargs)
            results.append(result)

    log(f"✅ Batch updated {len(results)} trades")
    return all(results)


def export_to_json():
    """Export all trades as JSON string"""
    try:
        trades = get_all_trades()
        return json.dumps(trades, indent=2)
    except Exception as e:
        log(f"❌ Failed to export: {e}")
        return None


# ==========================
# TESTING/DEMO
# ==========================

if __name__ == "__main__":
    log("=" * 80)
    log("📊 GOOGLE SHEETS DATABASE MODULE - DEMO")
    log("=" * 80)

    # Initialize
    sheet = init_sheets()

    # Get all trades
    trades = get_all_trades()
    log(f"\nTotal trades: {len(trades)}")

    # Get open trades
    open_trades = get_open_trades()
    log(f"Open trades: {len(open_trades)}")

    # Get stats
    stats = get_summary_stats()
    log(f"Stats: {stats}")

    log("\n✅ Module loaded successfully")