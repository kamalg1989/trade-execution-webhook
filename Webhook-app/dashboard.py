# ==============================================
# 🚀 TRADE JOURNAL DASHBOARD (STREAMLIT)
# ==============================================

import streamlit as st
import sqlite3
import pandas as pd

DB = "trades.db"


# ==========================
# LOAD DATA
# ==========================
def load_data():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # ✅ Ensure table exists
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

    try:
        df = pd.read_sql("SELECT * FROM trades", conn)
    except Exception as e:
        print("DB READ ERROR:", e)
        df = pd.DataFrame()

    conn.close()

    if not df.empty:
        df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")

    return df


# ==========================
# METRICS
# ==========================
def show_metrics(df):

    total_pnl = df["pnl"].sum()
    trades = len(df)
    wins = len(df[df["pnl"] > 0])
    losses = len(df[df["pnl"] <= 0])

    win_rate = (wins / trades * 100) if trades else 0

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total PnL", round(total_pnl, 2))
    col2.metric("Trades", trades)
    col3.metric("Win Rate", f"{round(win_rate,2)}%")
    col4.metric("Wins / Losses", f"{wins} / {losses}")


# ==========================
# EQUITY CURVE
# ==========================
def equity_curve(df):

    df = df.sort_values("exit_time")
    df["equity"] = df["pnl"].cumsum()

    st.subheader("📈 Equity Curve")
    st.line_chart(df.set_index("exit_time")["equity"])


# ==========================
# SYMBOL ANALYSIS
# ==========================
def symbol_analysis(df):

    st.subheader("📊 Symbol Performance")

    grouped = df.groupby("symbol")["pnl"].sum().sort_values(ascending=False)

    st.bar_chart(grouped)


# ==========================
# TRADE TABLE
# ==========================
def trade_table(df):

    st.subheader("🧾 Trades")

    st.dataframe(
        df.sort_values("exit_time", ascending=False),
        use_container_width=True
    )


# ==========================
# FILTERS
# ==========================
def apply_filters(df):

    symbols = st.multiselect(
        "Filter by Symbol",
        options=df["symbol"].unique(),
        default=list(df["symbol"].unique())
    )

    if symbols:
        df = df[df["symbol"].isin(symbols)]

    return df


# ==========================
# MAIN
# ==========================
def main():

    st.set_page_config(layout="wide")
    st.title("🚀 Trade Journal Dashboard")

    df = load_data()

    if df.empty:
        st.warning("No trades available")
        return

    df = apply_filters(df)

    show_metrics(df)

    col1, col2 = st.columns(2)

    with col1:
        equity_curve(df)

    with col2:
        symbol_analysis(df)

    trade_table(df)


if __name__ == "__main__":
    main()
