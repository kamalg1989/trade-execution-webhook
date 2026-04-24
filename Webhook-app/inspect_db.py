#!/usr/bin/env python3
import sqlite3

DB = "/root/trade-execution-webhook/trades.db"

print("=" * 70)
print("DATABASE SCHEMA INSPECTION")
print("=" * 70)

conn = sqlite3.connect(DB)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print(f"\n📋 All tables in database:")
for table in tables:
    print(f"  - {table[0]}")

# Get schema for each table
print("\n" + "=" * 70)
print("DETAILED SCHEMA:")
print("=" * 70)

for table in tables:
    table_name = table[0]
    print(f"\n📊 TABLE: {table_name}")
    print("-" * 70)

    # Get column info
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()

    if columns:
        for col in columns:
            col_id, col_name, col_type, notnull, default, pk = col
            print(f"  {col_name:30} {col_type:15} PK={pk} NOT_NULL={notnull}")
    else:
        print("  (No columns)")

    # Get row count
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"\n  Row count: {count}")

    # Sample data
    if count > 0:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 2")
        sample = cursor.fetchall()
        print(f"\n  Sample data:")
        for row in sample:
            print(f"    {row}")

conn.close()

print("\n" + "=" * 70)
print("CHECKING FOR EXPECTED TABLES:")
print("=" * 70)

conn = sqlite3.connect(DB)
cursor = conn.cursor()

expected_tables = ['pending_orders', 'executed_orders']
for table_name in expected_tables:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    result = cursor.fetchone()
    if result:
        print(f"✅ {table_name} - EXISTS")
    else:
        print(f"❌ {table_name} - MISSING!")

conn.close()