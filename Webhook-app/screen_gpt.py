# ==============================================
# 🚀 FINAL SYSTEM (GPT UPGRADED + JSON OUTPUT)
# ==============================================

import os
import json
import time
import requests
import pandas as pd
import yfinance as yf
import mplfinance as mpf
import matplotlib.pyplot as plt
from datetime import datetime
from openai import OpenAI
from matplotlib.patches import Patch

from reportlab.platypus import SimpleDocTemplate, Image, Spacer
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

import sqlite3

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

# ==========================
# CONFIG
# ==========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL = int(os.getenv("CAPITAL") or "200000")


# ==========================
# GLOBAL TOKEN CACHE
# ==========================
DHAN_TOKEN_CACHE = {"token": None, "generated_at": 0}


def get_dhan_token():
    global DHAN_TOKEN_CACHE

    # Reuse cached token if < 23 hours old (Dhan tokens valid 24h)
    if DHAN_TOKEN_CACHE["token"] and (time.time() - DHAN_TOKEN_CACHE["generated_at"]) < 23 * 3600:
        return DHAN_TOKEN_CACHE["token"]

    try:
        import pyotp

        client_id = os.getenv("DHAN_CLIENT_ID")
        pin = os.getenv("DHAN_PIN")
        secret = os.getenv("DHAN_TOTP_SECRET")

        if not all([client_id, pin, secret]):
            print("❌ Missing Dhan credentials")
            return None

        totp = pyotp.TOTP(secret).now()

        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={
                "dhanClientId": client_id,
                "pin": pin,
                "totp": totp
            },
            timeout=15
        )

        if r.status_code != 200:
            print(f"❌ Token HTTP error: {r.status_code} {r.text}")
            return DHAN_TOKEN_CACHE["token"]  # fallback to old token if exists

        data = r.json()
        token = data.get("accessToken")

        if not token:
            # Rate-limited → reuse old cached token if available
            print(f"⚠️ Token generation blocked: {data}. Reusing cached token.")
            return DHAN_TOKEN_CACHE["token"]

        DHAN_TOKEN_CACHE = {"token": token, "generated_at": time.time()}
        print("✅ New Dhan token generated and cached")
        return token

    except Exception as e:
        print(f"❌ Token generation failed: {e}")
        return DHAN_TOKEN_CACHE["token"]


# ==========================
# DB
# ==========================
def get_conn():
    return sqlite3.connect(DB_FILE)


def save_trade(payload):
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_setups (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT,
            qty INTEGER,
            entry REAL,
            sl REAL,
            target REAL,
            score REAL,

            status TEXT DEFAULT 'PENDING',
            executed_at TIMESTAMP,
            order_id TEXT,
            broker_response TEXT,

            risk REAL,
            reward REAL,
            rr_ratio REAL,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ensure new columns exist (schema migration)
    try:
        conn.execute("ALTER TABLE trade_setups ADD COLUMN risk REAL")
    except:
        pass

    try:
        conn.execute("ALTER TABLE trade_setups ADD COLUMN reward REAL")
    except:
        pass

    try:
        conn.execute("ALTER TABLE trade_setups ADD COLUMN rr_ratio REAL")
    except:
        pass

    conn.execute("""
        INSERT OR REPLACE INTO trade_setups
        (setup_id, symbol, qty, entry, sl, target, score,
         status, risk, reward, rr_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload["setup_id"],
        payload["symbol"],
        payload["qty"],
        payload["entry"],
        payload["sl"],
        payload["target"],
        payload["score"],
        "PENDING",
        payload.get("risk", 0),
        payload.get("reward", 0),
        payload.get("rr_ratio", 0)
    ))

    conn.commit()
    conn.close()


# ==========================
# TELEGRAM
# ==========================
def send_message(text, buttons=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    print("📡 Telegram sendMessage payload:")
    print(payload)

    try:
        res = requests.post(url, data=payload, timeout=10)
        print(f"📡 Telegram status: {res.status_code}")
        print(f"📡 Telegram response: {res.text}")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


def send_document(path, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"

    print(f"📡 Sending document: {path}")

    try:
        with open(path, "rb") as f:
            res = requests.post(
                url,
                files={"document": f},
                data={"chat_id": CHAT_ID, "caption": caption or ""},
                timeout=20
            )

        print(f"📡 Document status: {res.status_code}")
        print(f"📡 Document response: {res.text}")

    except Exception as e:
        print(f"❌ Document send failed: {e}")


# ==========================
# NSE STOCK FETCH
# ==========================
def get_stocks():
    headers = {"User-Agent": "Mozilla/5.0"}

    indices = [
        "NIFTY 500",
        "NIFTY MIDCAP 150",
        "NIFTY SMALLCAP 250"
    ]

    stocks = set()

    for index in indices:
        try:
            url = f"https://www.nseindia.com/api/equity-stockIndices?index={index.replace(' ', '%20')}"
            res = requests.get(url, headers=headers, timeout=10)
            data = res.json()

            for item in data.get("data", []):
                symbol = item.get("symbol")
                if symbol and symbol.isalpha():
                    stocks.add(symbol + ".NS")

            time.sleep(0.5)

        except:
            continue

    return list(stocks)


# ==========================
# DATA
# ==========================
def fetch(stock):
    try:
        # ==========================
        # LOAD SECURITY MAP (AUTO)
        # ==========================
        global SECURITY_MAP_CACHE

        if "SECURITY_MAP_CACHE" not in globals():
            try:
                url = "https://images.dhan.co/api-data/api-scrip-master.csv"
                df_map = pd.read_csv(url, low_memory=False)

                # Filter NSE EQ only
                df_map = df_map[df_map["SEM_EXM_EXCH_ID"] == "NSE"]

                SECURITY_MAP_CACHE = {
                    f"{row['SEM_TRADING_SYMBOL']}.NS": str(row["SEM_SMST_SECURITY_ID"])
                    for _, row in df_map.iterrows()
                }

                print(f"✅ Loaded {len(SECURITY_MAP_CACHE)} instruments from Dhan")

            except Exception as e:
                print(f"❌ Failed to load instrument list: {e}")
                return pd.DataFrame()

        security_id = SECURITY_MAP_CACHE.get(stock)

        if not security_id:
            print(f"❌ securityId not found for {stock}")
            return pd.DataFrame()

        from datetime import datetime, timedelta

        to_date = datetime.now()
        from_date = to_date - timedelta(days=200)

        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d")
        }

        token = get_dhan_token()
        if not token:
            print(f"❌ Skipping {stock} due to token failure")
            return pd.DataFrame()

        headers = {
            "Content-Type": "application/json",
            "access-token": token
        }

        response = None
        for attempt in range(3):
            try:
                response = requests.post(
                    "https://api.dhan.co/v2/charts/historical",
                    json=payload,
                    headers=headers,
                    timeout=15
                )

                if response.status_code == 200:
                    break

                print(f"⚠️ Retry {attempt+1} failed for {stock}: {response.text}")
                time.sleep(2 ** attempt)

            except Exception as e:
                print(f"⚠️ Retry {attempt+1} exception for {stock}: {e}")
                time.sleep(2 ** attempt)

        if not response or response.status_code != 200:
            print(f"❌ Dhan API failed for {stock}")
            return pd.DataFrame()

        data = response.json()

        if not data.get("close"):
            print(f"❌ No data returned for {stock}")
            return pd.DataFrame()

        df = pd.DataFrame({
            "Open": data["open"],
            "High": data["high"],
            "Low": data["low"],
            "Close": data["close"],
            "Volume": data["volume"],
            "Timestamp": data["timestamp"]
        })

        df["Date"] = pd.to_datetime(df["Timestamp"], unit="s")
        df.set_index("Date", inplace=True)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()

        print(f"📊 {stock} | candles={len(df)} | last={df.index[-1].date()}")

        return df

    except Exception as e:
        print(f"❌ Dhan fetch error for {stock}: {e}")
        return pd.DataFrame()


def to_weekly(df):
    return df.resample('W').agg({
        'Open':'first','High':'max','Low':'min',
        'Close':'last','Volume':'sum'
    }).dropna()


# ==========================
# FILTER
# ==========================
def filter_stock(df):
    if df is None or df.empty:
        return False

    if len(df) < 50:
        return False

    df['EMA50'] = df['Close'].ewm(span=50).mean()
    df['EMA200'] = df['Close'].ewm(span=200).mean()

    cond1 = df.iloc[-1]['Close'] > df.iloc[-1]['EMA50'] > df.iloc[-1]['EMA200']

    recent = df.tail(20)
    base_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
    cond2 = base_range < 0.15

    vol_avg = df['Volume'].rolling(20).mean()
    cond3 = df.iloc[-1]['Volume'] > 0.8 * vol_avg.iloc[-1]

    return cond1 and cond2 and cond3


# ==========================
# TRADE LOGIC
# ==========================
def create_trade(df):

    last = df.iloc[-1]

    entry = float(last['High'])
    exit_price = float(last['Low'])

    risk_per_share = entry - exit_price

    if risk_per_share <= 0:
        return None

    # 0.25% risk
    risk_amt = CAPITAL * 0.0025
    qty_risk = int(risk_amt / risk_per_share)

    # 10% cap
    max_capital_per_trade = CAPITAL * 0.10
    qty_cap = int(max_capital_per_trade / entry)

    qty = min(qty_risk, qty_cap)

    if qty <= 0:
        return None

    return round(entry, 2), round(exit_price, 2), qty


# ==========================
# CHART ENGINE
# ==========================
def plot_chart(stock, save_path):

    df = fetch(stock)
    if df is None or df.empty:
        print(f"❌ Skipping chart for {stock} due to no data")
        return
    df_weekly = to_weekly(df.copy())

    for ema in [10,21,50,200]:
        df[f'EMA{ema}'] = df['Close'].ewm(span=ema).mean()
        df_weekly[f'EMA{ema}'] = df_weekly['Close'].ewm(span=ema).mean()

    recent = df.tail(20)
    breakout = recent['High'].max()
    base_low = recent['Low'].min()
    base_high = recent['High'].max()

    mc = mpf.make_marketcolors(
        up='green', down='red',
        volume={'up':'green','down':'red'}
    )

    style = mpf.make_mpf_style(base_mpf_style='yahoo', marketcolors=mc)

    apds = [
        mpf.make_addplot(df['EMA10'], color='black'),
        mpf.make_addplot(df['EMA21'], color='red'),
        mpf.make_addplot(df['EMA50'], color='blue'),
        mpf.make_addplot(df['EMA200'], color='purple'),
    ]

    apds_w = [
        mpf.make_addplot(df_weekly['EMA10'], color='black'),
        mpf.make_addplot(df_weekly['EMA21'], color='red'),
        mpf.make_addplot(df_weekly['EMA50'], color='blue'),
        mpf.make_addplot(df_weekly['EMA200'], color='purple'),
    ]

    legend = [
        Patch(facecolor='black', label='EMA10'),
        Patch(facecolor='red', label='EMA21'),
        Patch(facecolor='blue', label='EMA50'),
        Patch(facecolor='purple', label='EMA200')
    ]

    # DAILY
    fig1, ax1 = mpf.plot(
        df, type='candle', style=style, addplot=apds,
        volume=True, returnfig=True,
        figsize=(12,6), datetime_format='%b-%y'
    )

    ax1[0].axhline(breakout, linestyle='--', color='green')
    ax1[0].axhspan(base_low, base_high, alpha=0.1)
    ax1[0].legend(handles=legend)
    ax1[0].set_title(f"{stock} (Daily)", fontsize=14)

    fig1.savefig("d.png", dpi=200, bbox_inches='tight', pad_inches=0)
    plt.close(fig1)

    # WEEKLY
    fig2, ax2 = mpf.plot(
        df_weekly, type='candle', style=style, addplot=apds_w,
        volume=True, returnfig=True,
        figsize=(12,6), datetime_format='%b-%y'
    )

    ax2[0].legend(handles=legend)
    ax2[0].set_title(f"{stock} (Weekly)", fontsize=14)

    fig2.savefig("w.png", dpi=200, bbox_inches='tight', pad_inches=0)
    plt.close(fig2)

    # MERGE
    fig = plt.figure(figsize=(12,9))

    a1 = fig.add_subplot(2,1,1)
    a1.imshow(plt.imread("d.png"))
    a1.axis('off')

    a2 = fig.add_subplot(2,1,2)
    a2.imshow(plt.imread("w.png"))
    a2.axis('off')

    plt.subplots_adjust(hspace=0.05)
    plt.savefig(save_path, dpi=200, bbox_inches='tight', pad_inches=0)
    plt.close()
    for f in ["d.png", "w.png"]:
        if os.path.exists(f):
            os.remove(f)


# ==========================
# PDF
# ==========================
def build_pdf(images, path):

    doc = SimpleDocTemplate(path, pagesize=letter)

    elements = []

    for img_path in images:
        img = ImageReader(img_path)
        w, h = img.getSize()

        scale = min(doc.width / w, doc.height * 0.9 / h)

        elements.append(Image(img_path, width=w*scale, height=h*scale))
        elements.append(Spacer(1, 10))

    doc.build(elements)


# ==========================
# GPT (UPGRADED)
# ==========================
def gpt_decision(pdf_path):
    if not client:
        print("⚠️ OPENAI_API_KEY missing → Skipping GPT")
        return json.dumps({"picks": []})

    file = client.files.create(file=open(pdf_path,"rb"), purpose="user_data")

    PROMPT = """
    You are an institutional breakout trader following strict rules.
    
    Analyze charts and return ONLY valid JSON. No explanation. No text outside JSON.
    
    RULES:
    - Strong trend (EMA50 > EMA200)
    - Tight base (<15%)
    - Near breakout (price close to recent high)
    - Volume expansion
    - Strong entry candle
    
    Reject weak setups strictly.
    
    SCORING:
    9-10 perfect
    8 strong
    7 acceptable
    <7 reject
    
    SELECTION RULES (STRICT):
    - Return MAX 3 stocks ONLY
    - If unsure, return fewer (0 or 1 allowed)
    - NEVER return more than 3
    - Only include stocks with score >= 7
    
    RANKING LOGIC (DETERMINISTIC):
    If multiple stocks qualify, rank strictly by:
    1. Tightest base (lowest range %)
    2. Closest to breakout (price near resistance)
    3. Strongest volume expansion
    
    Always pick top ranked stocks only.
    
    OUTPUT FORMAT (STRICT JSON):
    
    {
      "picks": [
        {
          "stock": "ABC.NS",
          "score": 8.5,
          "quality": "STRONG",
          "reason": "tight base + volume breakout",
          "entry_type": "Trend Bar"
        }
      ]
    }
    
    If no valid setups, return:
    {
      "picks": []
    }
    """

    res = client.responses.create(
        model="gpt-4.1-mini",
        temperature=0,
        input=[{
            "role":"user",
            "content":[
                {"type":"input_text","text":PROMPT},
                {"type":"input_file","file_id":file.id}
            ]
        }]
    )

    return res.output_text


# ==========================
# SAFE PARSER
# ==========================
def parse_gpt_output(output):
    try:
        data = json.loads(output)
        return data.get("picks", [])
    except:
        return []


# ==========================
# MAIN
# ==========================
def run():

    stocks = get_stocks()

    shortlist = []
    scored = []

    for s in stocks:
        try:
            df = fetch(s)

            if not filter_stock(df):
                continue

            # ensure EMA exists
            df['EMA50'] = df['Close'].ewm(span=50).mean()

            recent = df.tail(20)

            base_high = recent['High'].max()
            base_low = recent['Low'].min()
            current = df['Close'].iloc[-1]

            if base_low == 0:
                continue

            tightness = (base_high - base_low) / base_low

            score = (
                    (current / base_high) * 0.5 +   # breakout proximity
                    (current / df['EMA50'].iloc[-1]) * 0.3 +  # trend strength
                    (1 - tightness) * 0.2           # tighter base
            )

            scored.append((s, score))

        except:
            continue


    # sort best first
    scored.sort(key=lambda x: x[1], reverse=True)

    shortlist = [s for s, _ in scored[:10]]

    print(f"📊 Shortlist 10: {shortlist}")

    print(f"🔍 TELEGRAM CONFIG → CHAT_ID={CHAT_ID}, TOKEN_SET={bool(TELEGRAM_TOKEN)}")

    folder = f"run_{datetime.now().strftime('%H%M%S')}"
    os.makedirs(folder, exist_ok=True)

    images = []
    trade_map = {}

    for s in shortlist:

        img = f"{folder}/{s}.png"
        plot_chart(s, img)

        if not os.path.exists(img):
            continue

        images.append(img)

        df = fetch(s)
        if df is None or df.empty:
            continue
        trade_map[s] = create_trade(df)

    pdf_path = f"{folder}/charts.pdf"
    build_pdf(images, pdf_path)

    send_document(pdf_path, "📄 Charts sent to GPT")

    print("🧠 GPT RAW OUTPUT:")
    print(output := gpt_decision(pdf_path))

    picks = parse_gpt_output(output)
    print(f"🧠 Parsed Picks: {picks}")
    print(f"🧠 Picks Count: {len(picks)}")

    if not picks:
        print("⚠️ No picks returned from GPT. No Telegram messages will be sent.")

    for p in picks:
        print(f"🔍 Processing pick: {p}")

        s = p["stock"]

        print(f"📌 Checking trade_map for {s}")
        print(f"📌 Available keys: {list(trade_map.keys())}")

        if s not in trade_map:
            continue

        print(f"✅ Found trade setup for {s}: {trade_map[s]}")

        entry, exit_price, qty = trade_map[s]

        if not entry or not exit_price or not qty:
            print(f"⚠️ Invalid trade values for {s}: {trade_map[s]}")
            continue

        # ===== derive additional fields =====
        risk = round(entry - exit_price, 2)
        reward = round((entry + (risk * 2)) - entry, 2)
        target = round(entry + (risk * 2), 2)

        rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        score = p.get("score", 0)

        # Ensure uniqueness with timestamp seconds
        setup_id = f"{datetime.now().strftime('%H%M%S%f')}{s[:4]}"

        payload = {
            "setup_id": setup_id,
            "symbol": s,
            "qty": qty,
            "entry": entry,
            "sl": exit_price,
            "target": target,
            "score": score,
            "risk": risk,
            "reward": reward,
            "rr_ratio": rr_ratio
        }

        save_trade(payload)

        print("🧾 FULL TRADE PAYLOAD (SAVED):")
        print(payload)

        msg = f"""
📈 *FINAL TRADE*

{s}
Score: {p['score']} ({p['quality']})

Entry: `{entry}`
SL: `{exit_price}`
Target: `{target}`
Qty: `{qty}`

Reason: {p['reason']}
Type: {p['entry_type']}
"""

        short_cb = f"BUY|{setup_id}|{s}|{qty}|{entry}|{exit_price}|{target}|{score}"

        payload_size = len(short_cb.encode("utf-8"))
        print(f"📏 Callback Payload Size: {payload_size} bytes")
        print("📦 Callback RAW STRING:")
        print(short_cb)
        print("📦 Callback REPR:")
        print(repr(short_cb))

        buttons = [[{
            "text": "✅ Confirm Buy",
            "callback_data": short_cb
        }]]

        print("🧪 FINAL TELEGRAM MESSAGE:")
        print(msg)
        print("🧪 BUTTONS:")
        print(buttons)

        print(f"📤 Sending Telegram alert for {s}")
        print(f"📤 Callback (FULL): {short_cb}")

        send_message(msg, buttons)


# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    run()