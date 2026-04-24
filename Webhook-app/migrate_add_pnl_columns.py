# ==============================================
# 🔄 MIGRATION: Add P&L tracking columns to trades table
# ==============================================

import sqlite3
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_FILE = "/root/trade-execution-webhook/trades.db"


def migrate_add_pnl_columns():
    """
    Add missing columns to trades table for P&L tracking:
    - current_price: Last traded price
    - pnl: Profit/Loss in rupees
    - pnl_percent: Profit/Loss percentage
    - updated_at: Last update timestamp
    """

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        logger.info("=" * 70)
        logger.info("🔄 MIGRATION: Adding P&L columns to trades table")
        logger.info("=" * 70)

        # Get existing columns
        cursor.execute("PRAGMA table_info(trades)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        logger.info(f"\n📋 Current columns in trades table: {existing_columns}")

        columns_to_add = {
            'current_price': 'REAL',
            'pnl': 'REAL',
            'pnl_percent': 'REAL',
            'updated_at': 'TEXT'
        }

        added_count = 0

        for col_name, col_type in columns_to_add.items():
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
                    logger.info(f"✅ Added column: {col_name} ({col_type})")
                    added_count += 1
                except Exception as e:
                    logger.error(f"❌ Failed to add {col_name}: {e}")
            else:
                logger.info(f"⏭️  Column already exists: {col_name}")

        conn.commit()

        logger.info(f"\n✅ Migration completed!")
        logger.info(f"   - Columns added: {added_count}")
        logger.info(f"   - Total columns in trades: {len(existing_columns) + added_count}")

        # Verify
        cursor.execute("PRAGMA table_info(trades)")
        all_columns = cursor.fetchall()

        logger.info(f"\n📊 Final schema for trades table:")
        logger.info("-" * 70)
        for row in all_columns:
            col_id, col_name, col_type, notnull, default_val, pk = row
            logger.info(f"  {col_name:20} {col_type:15} {'PRIMARY KEY' if pk else ''}")
        logger.info("-" * 70)

        conn.close()

        logger.info("\n✅ Migration successful! Dashboard P&L tracking enabled.")
        logger.info("=" * 70 + "\n")

        return True

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        logger.exception("Traceback:")
        return False


if __name__ == "__main__":
    success = migrate_add_pnl_columns()
    exit(0 if success else 1)