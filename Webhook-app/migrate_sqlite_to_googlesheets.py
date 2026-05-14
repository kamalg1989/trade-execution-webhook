# ==============================================
# 🔄 MIGRATION SCRIPT: SQLite → Google Sheets
# One-time script to move all trades from SQLite to Google Sheets
# Run this ONCE, then delete it
# ==============================================

import os
import sys
import sqlite3
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
import json

# ==========================
# CONFIG
# ==========================
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "trades.db")  # Path to old SQLite DB
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH")
SHEET_NAME = "Trades"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"  # Set to "true" to preview without migrating

# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


# ==========================
# STEP 1: Read from SQLite
# ==========================
def read_sqlite_trades():
    """
    Read all trades from SQLite database
    Returns: List of trade dictionaries
    """
    try:
        if not os.path.exists(SQLITE_DB_PATH):
            log(f"❌ SQLite database not found at: {SQLITE_DB_PATH}")
            return []

        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row  # Get results as dictionaries
        cursor = conn.cursor()

        # Get all trades from the trades table
        cursor.execute("SELECT * FROM trades")
        rows = cursor.fetchall()

        # Convert to list of dicts
        trades = [dict(row) for row in rows]

        conn.close()

        log(f"✅ Read {len(trades)} trades from SQLite")
        return trades

    except Exception as e:
        log(f"❌ Error reading SQLite: {e}")
        return []


# ==========================
# STEP 2: Initialize Google Sheets
# ==========================
def init_google_sheets():
    """
    Initialize Google Sheets client and get/create the worksheet
    Returns: gspread.Worksheet object
    """
    try:
        if not SPREADSHEET_ID or not SERVICE_ACCOUNT_KEY_PATH:
            log("❌ Missing SPREADSHEET_ID or SERVICE_ACCOUNT_KEY_PATH")
            return None

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

        # Get or create the worksheet
        try:
            gsheet = spreadsheet.worksheet(SHEET_NAME)
            log(f"✅ Using existing sheet: {SHEET_NAME}")
        except gspread.exceptions.WorksheetNotFound:
            log(f"⚠️ Sheet '{SHEET_NAME}' not found, creating...")
            gsheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=5000, cols=15)

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
# STEP 3: Transform & Validate Data
# ==========================
def transform_trade(sqlite_trade):
    """
    Transform SQLite trade record to Google Sheets row format
    Handles data type conversions and missing fields
    """
    try:
        # Map SQLite columns to Google Sheets columns
        # SQLite schema: id, symbol, security_id, qty, entry_price, entry_time,
        #               status, sl_price, target_price, setup_id, current_price,
        #               pnl, pnl_percent, updated_at, dhan_order_id

        row = [
            str(sqlite_trade.get('id', '')),                    # ID
            str(sqlite_trade.get('symbol', '')).upper(),        # Symbol
            str(sqlite_trade.get('security_id', '')),           # Security_ID
            int(sqlite_trade.get('qty', 0)) or 0,              # Qty
            float(sqlite_trade.get('entry_price', 0)) or 0,    # Entry_Price
            str(sqlite_trade.get('entry_time', '')),            # Entry_Time
            str(sqlite_trade.get('status', 'OPEN')).upper(),   # Status
            float(sqlite_trade.get('sl_price', 0)) or 0,       # SL_Price
            float(sqlite_trade.get('target_price', 0)) or 0,   # Target_Price
            str(sqlite_trade.get('setup_id', '')),              # Setup_ID
            float(sqlite_trade.get('current_price', 0)) or 0,  # Current_Price
            float(sqlite_trade.get('pnl', 0)) or 0,            # PnL
            float(sqlite_trade.get('pnl_percent', 0)) or 0,    # PnL_Percent
            str(sqlite_trade.get('updated_at', '')),            # Updated_At
            str(sqlite_trade.get('dhan_order_id', ''))          # Dhan_Order_ID
        ]

        return row

    except Exception as e:
        log(f"❌ Error transforming trade: {e}")
        return None


# ==========================
# STEP 4: Migrate Trades
# ==========================
def migrate_trades(trades, gsheet):
    """
    Migrate trades to Google Sheets
    Returns: (success_count, failed_count)
    """
    if not trades or not gsheet:
        return 0, 0

    success_count = 0
    failed_count = 0

    log(f"\n📤 Migrating {len(trades)} trades to Google Sheets...")

    # Transform all trades
    rows_to_insert = []

    for idx, trade in enumerate(trades):
        row = transform_trade(trade)

        if row:
            rows_to_insert.append(row)
            success_count += 1
        else:
            failed_count += 1
            log(f"⚠️ Skipped trade {idx + 1}: Transform failed")

    if not rows_to_insert:
        log("❌ No trades to migrate")
        return 0, len(trades)

    # Insert all rows at once (more efficient)
    try:
        if DRY_RUN:
            log(f"\n🔍 DRY RUN: Would insert {len(rows_to_insert)} rows")
            log(f"   First row sample: {rows_to_insert[0]}")
            return success_count, failed_count

        log(f"   Inserting {len(rows_to_insert)} rows...")
        gsheet.append_rows(rows_to_insert, value_input_option="USER_ENTERED")

        log(f"✅ Successfully migrated {success_count} trades")

    except Exception as e:
        log(f"❌ Error inserting rows: {e}")
        log(f"   Trying row-by-row insertion...")

        # Fallback: insert one by one
        for idx, row in enumerate(rows_to_insert):
            try:
                gsheet.append_row(row, value_input_option="USER_ENTERED")
            except Exception as e:
                log(f"⚠️ Failed to insert row {idx + 1}: {e}")
                failed_count += 1
                success_count -= 1

    return success_count, failed_count


# ==========================
# STEP 5: VERIFY MIGRATION
# ==========================
def verify_migration(original_count, gsheet):
    """
    Verify that migration was successful
    """
    try:
        all_values = gsheet.get_all_values()

        # Subtract 1 for header row
        migrated_count = len(all_values) - 1

        log(f"\n📊 Verification:")
        log(f"   Original trades (SQLite): {original_count}")
        log(f"   Migrated trades (Google Sheets): {migrated_count}")

        if migrated_count == original_count:
            log(f"✅ Migration successful! All {original_count} trades migrated")
            return True
        else:
            log(f"⚠️ Count mismatch. Expected {original_count}, got {migrated_count}")
            return False

    except Exception as e:
        log(f"❌ Verification failed: {e}")
        return False


# ==========================
# STEP 6: BACKUP ORIGINAL
# ==========================
def backup_sqlite_db():
    """
    Create a backup of the original SQLite database
    """
    try:
        if not os.path.exists(SQLITE_DB_PATH):
            return

        backup_path = f"{SQLITE_DB_PATH}.backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

        import shutil
        shutil.copy2(SQLITE_DB_PATH, backup_path)

        log(f"✅ Backup created: {backup_path}")
        return backup_path

    except Exception as e:
        log(f"⚠️ Backup failed: {e}")
        return None


# ==========================
# MAIN
# ==========================
def main():
    log("=" * 80)
    log("🔄 MIGRATION: SQLite → Google Sheets")
    log("=" * 80)

    # Show configuration
    log(f"\n⚙️ Configuration:")
    log(f"   SQLite DB: {SQLITE_DB_PATH}")
    log(f"   Spreadsheet ID: {SPREADSHEET_ID[:30]}..." if SPREADSHEET_ID else "   Spreadsheet ID: NOT SET")
    log(f"   Dry run: {DRY_RUN}")

    if DRY_RUN:
        log(f"\n🔍 DRY RUN MODE - No data will be written")

    # Check environment variables
    if not SPREADSHEET_ID or not SERVICE_ACCOUNT_KEY_PATH:
        log("\n❌ Missing required environment variables:")
        log("   SPREADSHEET_ID - Get from Google Sheet URL")
        log("   SERVICE_ACCOUNT_KEY_PATH - Path to service account JSON key")
        log("\n   Set them with:")
        log("   export SPREADSHEET_ID='your-id'")
        log("   export SERVICE_ACCOUNT_KEY_PATH='/path/to/key.json'")
        return

    # Step 1: Read from SQLite
    log(f"\n📥 Reading from SQLite...")
    trades = read_sqlite_trades()

    if not trades:
        log("❌ No trades found in SQLite database")
        return

    original_count = len(trades)

    # Show sample
    log(f"\n   Sample trade:")
    log(f"   {trades[0]}")

    # Step 2: Backup original
    log(f"\n💾 Creating backup of original database...")
    backup_sqlite_db()

    # Step 3: Initialize Google Sheets
    log(f"\n☁️ Initializing Google Sheets...")
    gsheet = init_google_sheets()

    if not gsheet:
        log("❌ Failed to initialize Google Sheets")
        return

    # Step 4: Migrate
    log(f"\n🔄 Starting migration...")
    success, failed = migrate_trades(trades, gsheet)

    if DRY_RUN:
        log(f"\n🔍 DRY RUN COMPLETE - No data was actually written")
        log(f"   Would have migrated: {success} trades")
        return

    # Step 5: Verify
    log(f"\n✅ Verifying migration...")
    verified = verify_migration(original_count, gsheet)

    # Summary
    log(f"\n" + "=" * 80)
    log(f"📊 MIGRATION SUMMARY")
    log(f"=" * 80)
    log(f"   Original trades: {original_count}")
    log(f"   Successfully migrated: {success}")
    log(f"   Failed: {failed}")
    log(f"   Verification: {'✅ PASSED' if verified else '⚠️ FAILED'}")

    if verified:
        log(f"\n🎉 Migration complete! All trades are now in Google Sheets")
        log(f"\n📋 Next steps:")
        log(f"   1. Open your Google Sheet to verify")
        log(f"   2. Delete the old trades.db file (backup is saved)")
        log(f"   3. Run entry_engine_google_sheets.py for new trades")
    else:
        log(f"\n⚠️ Migration may have issues. Please verify manually")

    log(f"=" * 80)


if __name__ == "__main__":
    main()