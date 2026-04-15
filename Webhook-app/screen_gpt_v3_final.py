# ==============================================
# SCREEN_GPT V3 FINAL
# ==============================================
# Screening : V3 (Fundamental + Macro + Technical + Pattern)
# Charts    : V1 style (mplfinance, daily+weekly, 4 EMAs, breakout/base)
# Alerts    : V3 format (quality / IFP / pattern / edge metrics)
# Cost      : $0 (no GPT)

import os
import json
import time
import requests
import pandas as pd
import yfinance as yf
import mplfinance as mpf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import sqlite3
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['figure.max_open_warning'] = 0

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CAPITAL = int(os.getenv("CAPITAL") or "200000")


# ==========================
# PRIORITY 1: FUNDAMENTAL ANALYZER
# ==========================

class FundamentalAnalyzer:
    """Fast fundamental analysis with caching"""

    CACHE = {}
    LOCK = threading.Lock()

    @staticmethod
    def fetch_fundamentals(symbol):
        with FundamentalAnalyzer.LOCK:
            if symbol in FundamentalAnalyzer.CACHE:
                return FundamentalAnalyzer.CACHE[symbol]

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            fundamentals = {
                "pe_ratio": info.get('trailingPE'),
                "revenue_growth": info.get('revenueGrowth'),
                "profit_margin": info.get('profitMargins'),
                "roe": info.get('returnOnEquity'),
            }

            with FundamentalAnalyzer.LOCK:
                FundamentalAnalyzer.CACHE[symbol] = fundamentals

            return fundamentals
        except:
            return None

    @staticmethod
    def quality_score(fundamentals):
        if not fundamentals:
            return 0

        scores = []

        # PE (25%)
        pe = fundamentals.get('pe_ratio')
        if pe and 10 < pe < 35:
            scores.append(1.0 * 0.25)
        elif pe and pe > 35:
            scores.append(0.2 * 0.25)
        else:
            scores.append(0.5 * 0.25)

        # Margin (25%)
        pm = fundamentals.get('profit_margin')
        if pm and pm > 0.15:
            scores.append(1.0 * 0.25)
        elif pm and pm > 0.08:
            scores.append(0.8 * 0.25)
        elif pm and pm > 0:
            scores.append(0.4 * 0.25)
        else:
            scores.append(0.2 * 0.25)

        # Revenue Growth (25%)
        rg = fundamentals.get('revenue_growth')
        if rg and rg > 0.15:
            scores.append(1.0 * 0.25)
        elif rg and rg > 0.08:
            scores.append(0.8 * 0.25)
        elif rg and rg > 0:
            scores.append(0.4 * 0.25)
        else:
            scores.append(0.2 * 0.25)

        # ROE (25%)
        roe = fundamentals.get('roe')
        if roe and roe > 0.20:
            scores.append(1.0 * 0.25)
        elif roe and roe > 0.15:
            scores.append(0.8 * 0.25)
        elif roe and roe > 0.10:
            scores.append(0.6 * 0.25)
        else:
            scores.append(0.2 * 0.25)

        return sum(scores) * 10


# ==========================
# PRIORITY 3: MACRO ANALYZER
# ==========================

class MacroAnalyzer:
    """Sector tailwind analysis"""

    SECTOR_CACHE = {}
    SECTOR_LOCK = threading.Lock()

    SECTOR_TAILWIND = {
        1: {"favor": ["BANKS", "FINANCE", "INSURANCE"], "avoid": ["IT", "TECH", "REALTY"]},
        2: {"favor": ["BANKS", "UTILITIES", "PHARMA"], "avoid": ["TECH"]},
        3: {"favor": ["IT", "TECH", "REALTY", "GROWTH", "AUTO"], "avoid": ["UTILITIES"]},
        4: {"favor": ["IT", "TECH", "GROWTH"], "avoid": ["BANKS"]}
    }

    CURRENT_PHASE = 3

    @staticmethod
    def get_sector(symbol):
        with MacroAnalyzer.SECTOR_LOCK:
            if symbol in MacroAnalyzer.SECTOR_CACHE:
                return MacroAnalyzer.SECTOR_CACHE[symbol]

        try:
            ticker = yf.Ticker(symbol)
            sector = ticker.info.get('sector', 'UNKNOWN')

            with MacroAnalyzer.SECTOR_LOCK:
                MacroAnalyzer.SECTOR_CACHE[symbol] = sector

            return sector
        except:
            return 'UNKNOWN'

    @staticmethod
    def has_sector_tailwind(symbol):
        sector = MacroAnalyzer.get_sector(symbol)
        tailwind = MacroAnalyzer.SECTOR_TAILWIND.get(MacroAnalyzer.CURRENT_PHASE, {})

        if sector in tailwind.get('favor', []):
            return True
        elif sector in tailwind.get('avoid', []):
            return False
        else:
            return True


# ==========================
# PRIORITY 2: PATTERN DETECTOR
# ==========================

class PatternDetector:
    """Ultra-fast pattern detection (no external API)"""

    @staticmethod
    def detect_pattern(df):
        if len(df) < 2:
            return "UNKNOWN", 0.5

        c = df.iloc[-1]
        body = abs(c['Close'] - c['Open'])

        if body == 0:
            return "UNKNOWN", 0.3

        upper_wick = c['High'] - max(c['Close'], c['Open'])
        lower_wick = min(c['Close'], c['Open']) - c['Low']

        if upper_wick < body * 0.15:
            return "TREND_BAR", 0.85

        if lower_wick > body * 2 or upper_wick > body * 2:
            return "PIN_BAR", 0.90

        if len(df) > 1:
            prev = df.iloc[-2]
            if c['High'] > prev['High'] and c['Low'] > prev['Low']:
                return "HH_HL", 0.80
            if c['High'] < prev['High'] and c['Low'] > prev['Low']:
                return "INSIDE_BAR", 0.85

        return "POSSIBLE_BASE", 0.5


# ==========================
# IFP & EDGE CALCULATORS
# ==========================

class IFPChecker:
    """Volume analysis"""

    @staticmethod
    def calculate_ifp_score(df):
        if len(df) < 5:
            return 5.0

        current_vol = df.iloc[-1]['Volume']
        avg_prev_5 = df.iloc[-5:-1]['Volume'].mean()
        vol_ratio = current_vol / avg_prev_5 if avg_prev_5 > 0 else 0

        expansion = min(1.0, vol_ratio / 1.3) if vol_ratio > 1.0 else vol_ratio / 2

        avg_30d = df['Volume'].tail(30).mean() if len(df) > 30 else df['Volume'].mean()
        climax_ratio = current_vol / avg_30d if avg_30d > 0 else 0
        climax = min(1.0, climax_ratio / 2.0)

        return (expansion * 0.6 + climax * 0.4) * 10


class EdgeCalculator:
    """Edge calculation"""

    @staticmethod
    def calculate_edge(entry, sl, target):
        risk = entry - sl
        if risk <= 0:
            return -100

        reward = target - entry
        edge = (0.5 * (reward / entry)) - (0.5 * (risk / entry))
        return edge * 100


# ==========================
# CHART ENGINE  (V1 style)
# ==========================

def to_weekly(df):
    """Resample daily OHLCV to weekly"""
    return df.resample('W').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min',
        'Close': 'last', 'Volume': 'sum'
    }).dropna()


def plot_chart_v1(stock, df, save_path):
    """
    V1-style chart: mplfinance dual panel (daily + weekly)
    EMA 10/21/50/200, breakout line, base zone, volume, 200 DPI
    Uses stock-symbol-prefixed temp files to avoid race conditions.
    """
    if len(df) < 50:
        return False

    # Safe symbol for temp file naming (remove .NS suffix)
    safe = stock.replace('.', '_')
    tmp_d = f"_tmp_{safe}_d.png"
    tmp_w = f"_tmp_{safe}_w.png"

    try:
        # ---- EMAs on full history for accuracy, then slice 50 bars ----
        for ema in [10, 21, 50, 200]:
            df[f'EMA{ema}'] = df['Close'].ewm(span=ema, adjust=False).mean()

        plot_df = df.tail(50).copy()
        df_weekly = to_weekly(df.copy())

        for ema in [10, 21, 50, 200]:
            df_weekly[f'EMA{ema}'] = df_weekly['Close'].ewm(span=ema, adjust=False).mean()

        recent = plot_df.tail(20)
        breakout = recent['High'].max()
        base_low = recent['Low'].min()
        base_high = recent['High'].max()

        mc = mpf.make_marketcolors(
            up='green', down='red',
            volume={'up': 'green', 'down': 'red'}
        )
        style = mpf.make_mpf_style(base_mpf_style='yahoo', marketcolors=mc)

        legend_patches = [
            Patch(facecolor='black',  label='EMA10'),
            Patch(facecolor='red',    label='EMA21'),
            Patch(facecolor='blue',   label='EMA50'),
            Patch(facecolor='purple', label='EMA200'),
        ]

        # ---- Daily chart ----
        apds_d = [
            mpf.make_addplot(plot_df['EMA10'],  color='black'),
            mpf.make_addplot(plot_df['EMA21'],  color='red'),
            mpf.make_addplot(plot_df['EMA50'],  color='blue'),
            mpf.make_addplot(plot_df['EMA200'], color='purple'),
        ]

        fig1, ax1 = mpf.plot(
            plot_df, type='candle', style=style, addplot=apds_d,
            volume=True, returnfig=True,
            figsize=(12, 6), datetime_format='%b-%y'
        )
        ax1[0].axhline(breakout, linestyle='--', color='green', linewidth=1)
        ax1[0].axhspan(base_low, base_high, alpha=0.1)
        ax1[0].legend(handles=legend_patches, loc='upper left', fontsize=8)
        ax1[0].set_title(f"{stock} (Daily)", fontsize=14)
        fig1.savefig(tmp_d, dpi=200, bbox_inches='tight', pad_inches=0)
        plt.close(fig1)

        # ---- Weekly chart ----
        apds_w = [
            mpf.make_addplot(df_weekly['EMA10'],  color='black'),
            mpf.make_addplot(df_weekly['EMA21'],  color='red'),
            mpf.make_addplot(df_weekly['EMA50'],  color='blue'),
            mpf.make_addplot(df_weekly['EMA200'], color='purple'),
        ]

        fig2, ax2 = mpf.plot(
            df_weekly, type='candle', style=style, addplot=apds_w,
            volume=True, returnfig=True,
            figsize=(12, 6), datetime_format='%b-%y'
        )
        ax2[0].legend(handles=legend_patches, loc='upper left', fontsize=8)
        ax2[0].set_title(f"{stock} (Weekly)", fontsize=14)
        fig2.savefig(tmp_w, dpi=200, bbox_inches='tight', pad_inches=0)
        plt.close(fig2)

        # ---- Merge daily + weekly ----
        fig = plt.figure(figsize=(12, 9))
        a1 = fig.add_subplot(2, 1, 1)
        a1.imshow(plt.imread(tmp_d))
        a1.axis('off')
        a2 = fig.add_subplot(2, 1, 2)
        a2.imshow(plt.imread(tmp_w))
        a2.axis('off')
        plt.subplots_adjust(hspace=0.05)
        plt.savefig(save_path, dpi=200, bbox_inches='tight', pad_inches=0)
        plt.close('all')

        return True

    except Exception as e:
        print(f"Chart error for {stock}: {e}")
        plt.close('all')
        return False

    finally:
        for f in [tmp_d, tmp_w]:
            if os.path.exists(f):
                os.remove(f)


# ==========================
# DATA FETCHING
# ==========================

def get_stocks():
    """Fetch stocks from NSE"""
    headers = {"User-Agent": "Mozilla/5.0"}
    indices = ["NIFTY 500", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250"]
    stocks = set()

    for index in indices:
        try:
            url = f"https://www.nseindia.com/api/equity-stockIndices?index={index.replace(' ', '%20')}"
            res = requests.get(url, headers=headers, timeout=8)
            data = res.json()

            for item in data.get("data", []):
                symbol = item.get("symbol")
                if symbol and symbol.isalpha():
                    stocks.add(symbol + ".NS")

            time.sleep(0.2)
        except:
            continue

    return list(stocks)


def fetch(stock):
    """Fetch 6-month daily data"""
    try:
        df = yf.download(stock, period="6mo", auto_adjust=True, progress=False)
        df.index = pd.to_datetime(df.index)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

        if not df.empty and len(df) > 1:
            last = df.iloc[-1]
            if (pd.isna(last['Close']) or last['Volume'] == 0 or
                    (last['High'] == last['Low'] == last['Open'] == last['Close'])):
                df = df.iloc[:-1]

        return df if len(df) >= 50 else None
    except:
        return None


# ==========================
# FILTERING
# ==========================

def filter_stock(df):
    """Technical filter: trend + tight base + volume"""
    if len(df) < 50:
        return False

    try:
        import numpy as np
        ema50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = df['Close'].ewm(span=200, adjust=False).mean().iloc[-1]

        if not (df.iloc[-1]['Close'] > ema50 > ema200):
            return False

        recent = df.tail(20)
        base_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
        if base_range >= 0.15:
            return False

        vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
        if df.iloc[-1]['Volume'] < 0.8 * vol_avg:
            return False

        return True
    except:
        return False


def create_trade(df):
    """Position sizing: 0.25% risk, 10% capital cap"""
    last = df.iloc[-1]
    entry = float(last['High'])
    exit_price = float(last['Low'])
    risk_per_share = entry - exit_price

    if risk_per_share <= 0:
        return None

    risk_amt = CAPITAL * 0.0025
    qty_risk = int(risk_amt / risk_per_share)

    max_capital = CAPITAL * 0.10
    qty_cap = int(max_capital / entry)

    qty = min(qty_risk, qty_cap)
    return (round(entry, 2), round(exit_price, 2), qty) if qty > 0 else None


# ==========================
# DATABASE
# ==========================

def save_trade(payload):
    """Save trade with V3 extended schema"""
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_setups (
                setup_id       TEXT PRIMARY KEY,
                symbol         TEXT,
                qty            INTEGER,
                entry          REAL,
                sl             REAL,
                target         REAL,
                score          REAL,
                pattern        TEXT,
                quality_score  REAL,
                ifp_score      REAL,
                edge_score     REAL,
                status         TEXT DEFAULT 'PENDING',
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            INSERT OR REPLACE INTO trade_setups
            (setup_id, symbol, qty, entry, sl, target, score, pattern,
             quality_score, ifp_score, edge_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            payload["setup_id"], payload["symbol"], payload["qty"],
            payload["entry"], payload["sl"], payload["target"],
            payload["score"], payload.get("pattern"),
            payload.get("quality_score", 0), payload.get("ifp_score", 0),
            payload.get("edge_score", 0), "PENDING"
        ))

        conn.commit()
        conn.close()
    except:
        pass


# ==========================
# TELEGRAM
# ==========================

def send_message(text, buttons=None):
    """Send text alert"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}

        if buttons:
            payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

        requests.post(url, data=payload, timeout=8)
    except:
        pass


def send_document(path, caption=None):
    """Send chart image as Telegram document"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
        with open(path, "rb") as f:
            requests.post(
                url,
                files={"document": f},
                data={"chat_id": CHAT_ID, "caption": caption or ""},
                timeout=20
            )
    except:
        pass


# ==========================
# PARALLEL SCREENING
# ==========================

def screen_stock_fast(stock):
    """Screen single stock for parallel execution"""
    try:
        df = fetch(stock)
        if df is None:
            return None

        # Priority 1: Fundamental filter
        fund = FundamentalAnalyzer.fetch_fundamentals(stock)
        quality = FundamentalAnalyzer.quality_score(fund)
        if quality < 6.5:
            return None

        # Priority 3: Macro filter
        if not MacroAnalyzer.has_sector_tailwind(stock):
            return None

        # Technical filter
        if not filter_stock(df):
            return None

        result = create_trade(df)
        if not result:
            return None

        entry, sl, qty = result
        target = entry + (entry - sl) * 2

        # Priority 2: Pattern detection (instant)
        pattern, pattern_conf = PatternDetector.detect_pattern(df)

        ifp = IFPChecker.calculate_ifp_score(df)
        edge = EdgeCalculator.calculate_edge(entry, sl, target)

        composite = (quality * 0.3 + ifp * 0.3 +
                     pattern_conf * 10 * 0.2 + max(0, edge) * 0.2)

        return {
            "symbol": stock,
            "score": composite,
            "quality": quality,
            "ifp": ifp,
            "pattern": pattern,
            "pattern_conf": pattern_conf,
            "edge": edge,
            "df": df,
            "entry": entry,
            "sl": sl,
            "qty": qty
        }
    except:
        return None


# ==========================
# MAIN
# ==========================

def run():
    print("SCREEN_GPT V3 FINAL\n")
    print("Fetching stocks...")
    stocks = get_stocks()
    print(f"{len(stocks)} stocks to screen\n")

    print("Parallel screening (8 workers)...")
    results = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(screen_stock_fast, s): s for s in stocks}

        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                results.append(result)

            if i % 100 == 0:
                print(f"   Progress: {i}/{len(stocks)}")

    print(f"\nScreening complete: {len(results)} stocks qualified\n")

    if not results:
        print("No stocks passed filters")
        send_message("No stocks passed all filters today.")
        return

    results.sort(key=lambda x: x['score'], reverse=True)
    top_results = results[:5]

    print(f"Top {len(top_results)} picks — generating charts...\n")

    folder = f"run_{datetime.now().strftime('%H%M%S')}"
    os.makedirs(folder, exist_ok=True)

    chart_start = time.time()

    # Charts run sequentially to avoid mplfinance thread conflicts
    for r in top_results:
        chart_path = os.path.join(folder, f"{r['symbol']}.png")
        ok = plot_chart_v1(r['symbol'], r['df'], chart_path)
        r['chart_path'] = chart_path if ok else None

    chart_time = time.time() - chart_start
    print(f"Charts done in {chart_time:.1f}s\n")

    print("Sending alerts...\n")

    for r in top_results:
        symbol = r['symbol']
        entry  = r['entry']
        sl     = r['sl']
        qty    = r['qty']
        target = round(entry + (entry - sl) * 2, 2)

        setup_id = f"{datetime.now().strftime('%H%M%S%f')}{symbol[:4]}"

        save_trade({
            "setup_id":     setup_id,
            "symbol":       symbol,
            "qty":          qty,
            "entry":        entry,
            "sl":           sl,
            "target":       target,
            "score":        r['score'],
            "pattern":      r['pattern'],
            "quality_score": r['quality'],
            "ifp_score":    r['ifp'],
            "edge_score":   r['edge']
        })

        # Send chart image first
        if r.get('chart_path') and os.path.exists(r['chart_path']):
            send_document(r['chart_path'], caption=f"{symbol} — Daily + Weekly")

        # Then send trade alert with Buy button
        msg = f"""
*TRADE SIGNAL*

*{symbol}*
Score: {r['score']:.1f}/10

Entry:   `{entry}`
SL:      `{sl}`
Target:  `{target}`
Qty:     `{qty}`

Pattern: {r['pattern']} ({r['pattern_conf']:.0%})
Quality: {r['quality']:.1f}/10
IFP:     {r['ifp']:.1f}/10
Edge:    {r['edge']:.1f}%
"""

        cb = f"BUY|{setup_id}|{symbol}|{qty}|{entry}|{sl}|{target}|{r['score']:.1f}"
        buttons = [[{"text": "Buy", "callback_data": cb}]]

        send_message(msg, buttons)
        print(f"{symbol} — Score: {r['score']:.1f}  Pattern: {r['pattern']}  Quality: {r['quality']:.1f}")

    print(f"\nDone. Run folder: {folder}")


if __name__ == "__main__":
    import numpy as np
    run()
