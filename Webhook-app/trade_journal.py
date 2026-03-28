# ==============================================
# 🚀 ADVANCED TRADE JOURNAL (FIFO + ANALYTICS)
# ==============================================

import os
import requests
import sqlite3
from datetime import datetime

DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
TOKEN = None


def log(*args):
    print(*args, flush=True)


# ==========================
# DB
# ==========================
def init_db():
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        qty INTEGER,
        entry_price REAL,
        exit_price REAL,
        entry_time TEXT,
        exit_time TEXT,
        pnl REAL,
        pnl_pct REAL
    )
    """)

    conn.commit()
    conn.close()


# ==========================
# FETCH ORDERS
# ==========================
def fetch_orders():
    r = requests.get(
        "https://api.dhan.co/v2/orders",
        headers={"access-token": TOKEN},
        timeout=10
    )

    data = r.json()
    return data if isinstance(data, list) else []


# ==========================
# FILTER TRADED
# ==========================
def get_traded_orders(orders):
    return [
        o for o in orders
        if o.get("orderStatus") == "TRADED"
    ]


# ==========================
# FIFO ENGINE
# ==========================
def process_orders_fifo(traded_orders):

    books = {}
    completed_trades = []

    # sort by time
    traded_orders.sort(key=lambda x: x.get("createTime"))

    for o in traded_orders:

        symbol = o.get("tradingSymbol")
        side = o.get("transactionType")
        qty = int(o.get("quantity", 0))
        price = float(o.get("price", 0))
        time = o.get("createTime")

        if symbol not in books:
            books[symbol] = []

        # ==========================
        # BUY → add to book
        # ==========================
        if side == "BUY":
            books[symbol].append({
                "qty": qty,
                "price": price,
                "time": time
            })

        # ==========================
        # SELL → match FIFO
        # ==========================
        elif side == "SELL":

            remaining = qty

            while remaining > 0 and books[symbol]:

                buy = books[symbol][0]

                match_qty = min(remaining, buy["qty"])

                pnl = (price - buy["price"]) * match_qty
                pnl_pct = ((price - buy["price"]) / buy["price"]) * 100

                completed_trades.append({
                    "symbol": symbol,
                    "qty": match_qty,
                    "entry_price": buy["price"],
                    "exit_price": price,
                    "entry_time": buy["time"],
                    "exit_time": time,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2)
                })

                buy["qty"] -= match_qty
                remaining -= match_qty

                if buy["qty"] == 0:
                    books[symbol].pop(0)

    return completed_trades


# ==========================
# SAVE TRADES
# ==========================
def save_trades(trades):

    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    inserted = 0

    for t in trades:

        c.execute("""
        SELECT 1 FROM trades
        WHERE symbol=? AND entry_time=? AND exit_time=? AND qty=?
        """, (t["symbol"], t["entry_time"], t["exit_time"], t["qty"]))

        if c.fetchone():
            continue

        c.execute("""
        INSERT INTO trades (
            symbol, qty, entry_price, exit_price,
            entry_time, exit_time, pnl, pnl_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["symbol"],
            t["qty"],
            t["entry_price"],
            t["exit_price"],
            t["entry_time"],
            t["exit_time"],
            t["pnl"],
            t["pnl_pct"]
        ))

        inserted += 1

    conn.commit()
    conn.close()

    log(f"✅ Trades saved: {inserted}")


# ==========================
# ANALYTICS
# ==========================
def analytics():

    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    c.execute("SELECT pnl FROM trades")
    pnls = [r[0] for r in c.fetchall()]

    if not pnls:
        log("No trades")
        return

    total = sum(pnls)
    wins = len([p for p in pnls if p > 0])
    losses = len([p for p in pnls if p <= 0])

    avg_win = sum([p for p in pnls if p > 0]) / max(wins, 1)
    avg_loss = sum([p for p in pnls if p <= 0]) / max(losses, 1)

    win_rate = (wins / len(pnls)) * 100

    log("\n📊 ANALYTICS")
    log(f"Total Trades: {len(pnls)}")
    log(f"Total PnL: {round(total,2)}")
    log(f"Win Rate: {round(win_rate,2)}%")
    log(f"Avg Win: {round(avg_win,2)}")
    log(f"Avg Loss: {round(avg_loss,2)}")

    conn.close()


# ==========================
# EQUITY CURVE
# ==========================
def equity_curve():

    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    c.execute("SELECT pnl FROM trades ORDER BY id")
    pnls = [r[0] for r in c.fetchall()]

    equity = 0
    curve = []

    for p in pnls:
        equity += p
        curve.append(equity)

    log("\n📈 EQUITY CURVE:", curve)

    conn.close()


# ==========================
# MAIN
# ==========================
def run():

    log("\n🚀 TRADE JOURNAL (ADVANCED)\n")

    init_db()

    orders = fetch_orders()
    traded = get_traded_orders(orders)

    log(f"📦 TRADED ORDERS: {len(traded)}")

    trades = process_orders_fifo(traded)

    log(f"🔄 MATCHED TRADES: {len(trades)}")

    save_trades(trades)

    analytics()
    equity_curve()


if __name__ == "__main__":
    from sl_engine import get_token
    TOKEN = get_token()

    run()
