# URGENT: READ THIS FIRST!
#
# The V15 code had a critical bug that deleted all data when Dhan token failed.
#
# ROOT CAUSE:
# - TOTP token generation failed (likely system time out of sync)
# - get_positions() returned empty (no token)
# - Cleanup logic saw: Sheet has data, Dhan has 0 active → deleted all rows
#
# FIX:
# - Added validate_dhan_connection() function that checks before deleting
# - Added 3 safety checks to prevent catastrophic data loss
# - If Dhan API fails, the code now ABORTS instead of deleting everything
#
# BEFORE RUNNING:
# 1. Restore Google Sheet from version history
# 2. Fix system time and TOTP issue
# 3. Use this V16 code which is SAFE
#
# ===========================

# Copy everything from sl_engine_v15_complete.py
# THEN find the line that says: "def cleanup_stale_trades"
#
# REPLACE the cleanup_stale_trades() function with this complete section:

"""
# ===== PASTE THIS FUNCTION =====

def validate_dhan_connection():
    \"\"\"Validate Dhan connection BEFORE any destructive operations - CRITICAL SAFETY\"\"\"
    try:
        logger.info("🔐 Validating Dhan connection...")

        token = get_token()
        if not token:
            logger.error("❌ FATAL: No token from Dhan - aborting")
            logger.error("   Likely: TOTP failed, system time wrong, or API down")
            return False

        # Sanity check - try positions API
        r = session.get(
            "https://api.dhan.co/v2/positions",
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=10
        )

        if r.status_code != 200:
            logger.error(f"❌ FATAL: Dhan API error - Status {r.status_code}")
            logger.error(f"   Response: {r.text}")
            return False

        data = r.json()
        if not isinstance(data, list):
            logger.error(f"❌ FATAL: Invalid response type")
            return False

        logger.info(f"✅ Dhan connection valid")
        return True

    except Exception as e:
        logger.error(f"❌ FATAL: Connection validation failed: {e}")
        return False


def cleanup_stale_trades(trades_ws, trades_sheet):
    \"\"\"Remove rows not in active Dhan orders - WITH SAFETY CHECKS\"\"\"
    try:
        logger.info("\\n" + "=" * 80)
        logger.info("🧹 CLEANING UP STALE TRADES")
        logger.info("=" * 80)

        # ===== SAFETY CHECK #1: Validate Dhan connection =====
        if not validate_dhan_connection():
            logger.error("❌ ABORTING cleanup - Dhan connection invalid!")
            logger.error("   This prevents accidental data deletion")
            send_telegram_alert("❌ CLEANUP ABORTED", {
                "Reason": "Dhan connection failed",
                "Status": "No data deleted - safe exit"
            })
            return [], []

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
        logger.info(f"📊 Stocks in Sheet: {len(trades_sheet)}")

        # ===== SAFETY CHECK #2: Ensure we got valid data =====
        if len(active_sec_ids) == 0 and len(trades_sheet) > 0:
            logger.error("❌ SAFETY CHECK FAILED: No stocks in Dhan!")
            logger.error("   Sheet has {0} rows, Dhan is empty".format(len(trades_sheet)))
            logger.error("   Likely: Token failed, Dhan API error, or system time wrong")
            logger.error("   ABORTING cleanup to prevent data loss")
            send_telegram_alert("❌ CLEANUP ABORTED - SAFETY", {
                "Reason": "Dhan empty (likely token/API error)",
                "Sheet rows": len(trades_sheet),
                "Status": "Preventing accidental deletion"
            })
            return [], []

        # ===== SAFETY CHECK #3: Prevent massive deletions =====
        sheet_count = len(trades_sheet)
        if sheet_count > 0:
            potential_deletes = sheet_count - len(active_sec_ids)
            delete_percent = (potential_deletes / sheet_count) * 100 if sheet_count > 0 else 0

            logger.info(f"   Would delete: {potential_deletes}/{sheet_count} ({delete_percent:.1f}%)")

            if delete_percent > 80:
                logger.error("❌ SAFETY CHECK FAILED: Would delete > 80% of data!")
                logger.error("   Total: {0}, To delete: {1}".format(sheet_count, potential_deletes))
                logger.error("   This seems like a major error - aborting")
                send_telegram_alert("❌ CLEANUP ABORTED - SAFETY", {
                    "Reason": "Would delete > 80%",
                    "Total rows": sheet_count,
                    "To delete": potential_deletes,
                    "Status": "Likely API error"
                })
                return [], []

        # ===== NOW it's safe to delete =====
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
                logger.info(f"✅ Keeping: {symbol}")

        logger.info(f"\\n🗑️ Deleting {len(rows_to_delete)} stale rows...")
        for row_num, symbol, sec_id in sorted(rows_to_delete, reverse=True):
            try:
                if not DRY_RUN:
                    trades_ws.delete_rows(row_num)
                logger.info(f"✅ Deleted: {symbol} (row {row_num})")
            except Exception as e:
                logger.error(f"❌ Failed to delete row {row_num}: {e}")

        logger.info(f"\\n✅ Cleanup complete: Deleted={len(rows_to_delete)}, Kept={len(rows_to_keep)}")

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
        import traceback
        logger.error(traceback.format_exc())
        return [], []

# ===== END OF FUNCTION =====
\"\"\"