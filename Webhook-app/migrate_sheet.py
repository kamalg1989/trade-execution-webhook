#!/usr/bin/env python3
# ==============================================
# SHEET MIGRATION v1.0
# One-shot: rename col 8 SL_Price → Structural_SL
#           then ensure full 24-col schema
# ==============================================

import os
import sys

# Add the Webhook-app to path so we can import the data layer
sys.path.insert(0, '/root/trade-execution-webhook/Webhook-app')

import google_sheets_db as db

def migrate():
    print("=" * 80)
    print("🔄 SHEET MIGRATION v1.0")
    print("=" * 80)

    # Initialize connection
    try:
        sheet = db.init_sheets()
        print("✅ Connected to Google Sheets")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        return False

    # Read current header
    try:
        current_header = db._read_headers()
        print(f"\n📋 Current header ({len(current_header)} cols):")
        for i, col in enumerate(current_header, 1):
            print(f"   {i:2d}. {col}")
    except Exception as e:
        print(f"❌ Failed to read header: {e}")
        return False

    # Check if col 8 is "SL_Price"
    if len(current_header) < 8:
        print(f"\n⚠️  Header has only {len(current_header)} cols; expected >= 8")
        print("   Proceeding with ensure_schema only (no rename needed)")
    elif current_header[7] == "SL_Price":
        print(f"\n✅ Found col 8 = 'SL_Price' — renaming to 'Structural_SL'")
        try:
            # gspread uses 1-indexed rows and columns
            # Row 1, Column 8
            db._with_retry(sheet.update_cell, 1, 8, "Structural_SL")
            print(f"✅ Renamed col 8: SL_Price → Structural_SL")
        except Exception as e:
            print(f"❌ Rename failed: {e}")
            return False
    elif current_header[7] == "Structural_SL":
        print(f"\n✅ Col 8 is already 'Structural_SL' — no rename needed")
    else:
        print(f"\n⚠️  Col 8 is '{current_header[7]}' (expected 'SL_Price' or 'Structural_SL')")
        print("   This is unusual. Proceeding with ensure_schema.")

    # Ensure full 24-col schema (appends only missing new columns)
    print(f"\n🔍 Checking for missing columns...")
    try:
        final_header = db.ensure_schema()
        print(f"\n✅ Schema finalized ({len(final_header)} cols):")
        for i, col in enumerate(final_header, 1):
            is_new = col not in current_header if len(current_header) >= 8 else False
            marker = " ← NEW" if is_new else ""
            print(f"   {i:2d}. {col}{marker}")
    except Exception as e:
        print(f"❌ ensure_schema failed: {e}")
        return False

    # Verify the canonical 24 columns are present
    if len(final_header) < 24:
        print(f"\n⚠️  Final header has {len(final_header)} cols (expected 24)")
        print("   Some columns may be missing. Check manually.")
        return False

    expected = db.COLUMNS
    for i, exp_col in enumerate(expected, 1):
        if i > len(final_header):
            print(f"\n❌ Missing column {i}: {exp_col}")
            return False
        actual_col = final_header[i-1]
        if actual_col != exp_col:
            print(f"\n⚠️  Column {i} mismatch: expected '{exp_col}', got '{actual_col}'")
            print("   (This is OK if you renamed or reordered columns intentionally.)")

    print("\n" + "=" * 80)
    print("✅ MIGRATION COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("  1. Verify the Google Sheet manually (check row 1)")
    print("  2. Ensure any existing open trades have their SL values visible in col 8")
    print("  3. Deploy the new entry_engine.py")
    print("  4. Test with a paper trade")
    return True

if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)