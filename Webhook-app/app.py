# ==============================================
# 🚀 FINAL SYSTEM (GPT = FULL DECISION ENGINE)
# ==============================================

import os
import json
import requests
import pandas as pd
import yfinance as yf
import mplfinance as mpf
import matplotlib.pyplot as plt
from datetime import datetime
from openai import OpenAI

from reportlab.platypus import SimpleDocTemplate, Image, Spacer, Paragraph
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL = 1000000
RISK_PER_TRADE = 0.01

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

    requests.post(url, data=payload)

def send_document(file_path, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"

    with open(file_path, "rb") as f:
        requests.post(url, files={"document": f},
                      data={"chat_id": CHAT_ID, "caption": caption or ""})

# ==========================
# STOCKS
# ==========================
def get_stocks():
    url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        data = requests.get(url, headers=headers).json()
        return [x['symbol'] + ".NS" for x in data['data'] if x['symbol'].isalpha()]
    except:
        return []

# ==========================
# DATA
# ==========================
def fetch(stock):
    df = yf.download(stock, period="6mo", auto_adjust=True, progress=False)

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df[['Open','High','Low','Close','Volume']].dropna()

# ==========================
# STRICT FILTER ONLY
# ==========================
def basic_filter(df):

    if len(df) < 50:
        return False

    if df['Close'].iloc[-1] < 50:
        return False

    df['EMA50'] = df['Close'].ewm(span=50).mean()
    df['EMA200'] = df['Close'].ewm(span=200).mean()

    if not (df.iloc[-1]['Close'] > df.iloc[-1]['EMA50'] > df.iloc[-1]['EMA200']):
        return False

    return True

# ==========================
# TRADE
# ==========================
def create_trade(df):

    entry = df.iloc[-1]['High']
    sl = entry * 0.92

    risk = entry - sl
    qty = int((CAPITAL * RISK_PER_TRADE) / risk)

    return entry, sl, qty

# ==========================
# CHART
# ==========================
def plot_chart(df, path):

    df['EMA10'] = df['Close'].ewm(span=10).mean()
    df['EMA21'] = df['Close'].ewm(span=21).mean()
    df['EMA50'] = df['Close'].ewm(span=50).mean()
    df['EMA200'] = df['Close'].ewm(span=200).mean()

    apds = [
        mpf.make_addplot(df['EMA10']),
        mpf.make_addplot(df['EMA21']),
        mpf.make_addplot(df['EMA50']),
        mpf.make_addplot(df['EMA200'])
    ]

    fig, _ = mpf.plot(df, type='candle', volume=True,
                      addplot=apds, returnfig=True)

    fig.savefig(path)
    plt.close(fig)

# ==========================
# GPT VISION (CORE LOGIC)
# ==========================
def gpt_decision(pdf_path):

    file = client.files.create(file=open(pdf_path, "rb"), purpose="assistants")

    PROMPT = """
You are a professional breakout trader.

Follow STRICTLY:

WHAT TO BUY:
- Stocks where institutional buying is visible (price + volume)
- Strong demand zones
- Avoid weak or manipulated structures

WHEN TO BUY:
- Clear base formation (tight consolidation)
- Breakout readiness
- Logical stop loss exists
- HH-HL structure
- Accumulation phase preferred

TASK:

1. For EACH stock:
   - Score (0–10)
   - Identify base quality
   - Mention if institutional activity is visible

2. Reject weak setups

3. Give FINAL PICKS:
   - Only stocks with score ≥ 7
   - Max 2–3 stocks

OUTPUT FORMAT:

Stock | Score | Verdict

Final Picks:
- Stock1
- Stock2
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_file", "file_id": file.id}
            ]
        }]
    )

    return response.output_text

# ==========================
# PARSE
# ==========================
def extract_picks(text):

    picks = []

    for line in text.split("\n"):
        if line.startswith("-"):
            picks.append(line.replace("-", "").strip())

    return picks

# ==========================
# MAIN
# ==========================
def run():

    stocks = get_stocks()
    shortlisted = []

    for s in stocks:
        try:
            df = fetch(s)

            if basic_filter(df):
                shortlisted.append(s)

        except:
            continue

    shortlisted = shortlisted[:10]  # TOP 10 to GPT

    folder = f"run_{datetime.now().strftime('%H%M%S')}"
    os.makedirs(folder, exist_ok=True)

    styles = getSampleStyleSheet()
    elements = []

    trade_map = {}

    for s in shortlisted:

        df = fetch(s)

        img = f"{folder}/{s}.png"
        plot_chart(df, img)

        entry, sl, qty = create_trade(df)

        trade_map[s] = (entry, sl, qty)

        elements.append(Paragraph(f"<b>{s}</b>", styles['Heading2']))
        elements.append(Spacer(1,10))
        elements.append(Image(img, width=500, height=300))
        elements.append(Spacer(1,20))

    pdf_path = f"{folder}/report.pdf"
    doc = SimpleDocTemplate(pdf_path, pagesize=letter)
    doc.build(elements)

    # SEND PDF (AUDIT)
    send_document(pdf_path, "📄 Sent to GPT")

    # GPT DECISION
    gpt_output = gpt_decision(pdf_path)

    send_message(f"📊 GPT ANALYSIS\n\n{gpt_output[:3500]}")

    picks = extract_picks(gpt_output)

    # SEND TRADES
    for s in picks:

        if s not in trade_map:
            continue

        entry, sl, qty = trade_map[s]

        msg = f"""
📈 *FINAL TRADE*

*{s}*

Entry: `{entry}`
SL: `{sl}`
Qty: `{qty}`
"""

        callback = f"BUY|{s}|{qty}"
        buttons = [[{"text":"✅ Confirm Buy","callback_data":callback}]]

        send_message(msg, buttons)

# ==========================
# RUN
# ==========================
if __name__ == "__main__":
    run()
