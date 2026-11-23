"""OHM Screener (web) — small read-only API on 127.0.0.1:8008.

/api/results  -> nightly JSON from compute.py
/api/chart    -> the existing Stock Vault daily chart (create_svg_chart from
                 market_data_setup.api.main, imported, not modified) with
                 pivot / stop / breakout overlays drawn on top.
"""
import os, sys, json, time, threading
from collections import OrderedDict
import pandas as pd
import psycopg2
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import Response, FileResponse

sys.path.insert(0, '/root/trade-execution-webhook')
from market_data_setup.api.main import create_svg_chart  # noqa: E402
from market_data_setup.api.indicators import TechnicalIndicators  # noqa: E402

RESULTS = '/root/ohm_screener_web/data/latest.json'
app = FastAPI(title='OHM Screener web API', docs_url=None, redoc_url=None)
_cache = OrderedDict()
_lock = threading.Lock()
CACHE_MAX = 800


def _db():
    return psycopg2.connect(host='localhost', user='postgres', password='postgres', dbname='market_data')


@app.get('/api/health')
def health():
    ok = os.path.exists(RESULTS)
    return {'ok': ok, 'results_mtime': os.path.getmtime(RESULTS) if ok else None}


def _capital():
    try:
        for ln in open('/root/trade-execution-webhook/.env'):
            if ln.startswith('CAPITAL='):
                return int(float(ln.split('=', 1)[1].strip().strip('"').strip("'")))
    except Exception:
        pass
    return 400000


@app.get('/api/config')
def config():
    """OHM sizing rule (screen_gpt.create_trade): risk 0.25% of CAPITAL, cap 10% of CAPITAL, 2R target."""
    return {'capital': _capital(), 'riskPct': 0.25, 'capPct': 10.0, 'targetR': 2.0}


@app.get('/api/results')
def results():
    if not os.path.exists(RESULTS):
        raise HTTPException(503, 'results not computed yet')
    return FileResponse(RESULTS, media_type='application/json', headers={'Cache-Control': 'no-cache'})


def _geometry(df, width, height, has_stats):
    """Mirror of create_svg_chart's price->y mapping (market_data_setup/api/main.py)."""
    s = max(0.45, min(1.4, height / 780))
    is_narrow = width < 700
    px = lambda v: round(v * s, 1)
    prices = pd.concat([df['open'], df['high'], df['low'], df['close']])
    mn, mx = float(prices.min()), float(prices.max())
    pad = (mx - mn) * 0.06 or 1
    mn -= pad; mx += pad
    rng = mx - mn or 1
    left = px(80) if is_narrow else px(115)
    right = px(20) if is_narrow else px(30)
    top = (px(175) if is_narrow else px(100)) if has_stats else px(64)
    bottom = px(42) if is_narrow else px(62)
    legend = px(34)
    ch_w = width - left - right
    ch_h = height - top - bottom - legend
    plot_bottom = height - bottom - legend
    y = lambda p: plot_bottom - ((p - mn) / rng) * ch_h
    x = lambda i: left + (i / max(len(df) - 1, 1)) * ch_w
    return dict(px=px, y=y, x=x, mn=mn, mx=mx, left=left, right=right)


def _overlay(svg, df, width, height, has_stats, theme, pivot, stop, bo):
    g = _geometry(df, width, height, has_stats)
    px = g['px']
    piv_col = '#b45309' if theme == 'light' else '#f5b83d'
    stp_col = '#be123c' if theme == 'light' else '#fb7185'
    add = []
    fs = max(10, px(13))
    sw = max(1.2, px(1.8))
    for lvl, lbl, col in ((pivot, 'PIVOT', piv_col), (stop, 'STOP', stp_col)):
        if lvl is None or not (g['mn'] <= lvl <= g['mx']):
            continue
        yy = g['y'](lvl)
        add.append(f'<line x1="{g["left"]}" y1="{yy:.1f}" x2="{width - g["right"]}" y2="{yy:.1f}" '
                   f'stroke="{col}" stroke-width="{sw}" stroke-dasharray="7 5"/>')
        ty = yy - px(5) if lbl == 'PIVOT' else yy + fs + px(2)
        add.append(f'<text x="{g["left"] + px(6)}" y="{ty:.1f}" font-size="{fs}" font-weight="700" '
                   f'fill="{col}">{lbl} {lvl:,.2f}</text>')
    if bo and len(df):
        i = len(df) - 1
        cx, cy = g['x'](i), g['y'](float(df['close'].iloc[-1]))
        add.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{max(6, px(12))}" fill="none" '
                   f'stroke="{piv_col}" stroke-width="{max(1.5, px(2.4))}"/>')
    if not add:
        return svg
    return svg.replace('</svg>', '\n'.join(add) + '\n</svg>')


def _outline(svg, df, width, height, has_stats, theme, ol):
    """Pattern outline polylines. ol = 'ago:price ago:price|ago:price ...' (bars ago from the last bar)."""
    g = _geometry(df, width, height, has_stats)
    col = '#6d28d9' if theme == 'light' else '#c4b5fd'
    n = len(df); add = []
    for line in ol.split('|'):
        pts = []
        for tok in line.split():
            try:
                a, p = tok.split(':'); a = int(a); p = float(p)
            except ValueError:
                continue
            if 0 <= a < n:
                pts.append(f"{g['x'](n - 1 - a):.1f},{g['y'](min(max(p, g['mn']), g['mx'])):.1f}")
        if len(pts) >= 2:
            add.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" '
                       f'stroke-width="{max(1.5, g["px"](2.6))}" stroke-linejoin="round" opacity="0.9"/>')
    return svg.replace('</svg>', '\n'.join(add) + '\n</svg>') if add else svg


@app.get('/api/chart')
def chart(symbol: str = Query(..., max_length=30),
          days: int = Query(120, ge=30, le=500),
          theme: str = Query('light', pattern='^(light|dark)$'),
          w: int = Query(640, ge=300, le=2400),
          h: int = Query(380, ge=300, le=2000),
          pivot: float = Query(None), stop: float = Query(None),
          bo: int = Query(0, ge=0, le=1), stats: int = Query(0, ge=0, le=1),
          ol: str = Query('', max_length=2000),
          v: str = Query('')):
    symbol = symbol.upper().strip()
    key = (symbol, days, theme, w, h, pivot, stop, bo, stats, v, ol)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return Response(_cache[key], media_type='image/svg+xml', headers={'Cache-Control': 'public, max-age=1800'})
    conn = _db()
    try:
        cur = conn.cursor()
        cur.execute("""select (time at time zone 'Asia/Kolkata')::date, open, high, low, close, volume
                       from ohlcv_data where symbol=%s order by time desc limit %s""", (symbol, days + 260))
        rows = cur.fetchall()[::-1]
        cur.execute("select security_name from symbols_meta where symbol=%s", (symbol,))
        r = cur.fetchone(); name = r[0] if r else None
    finally:
        conn.close()
    if not rows:
        raise HTTPException(404, 'no data')
    df = pd.DataFrame(rows, columns=['trading_date', 'open', 'high', 'low', 'close', 'volume'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df['trading_date'] = pd.to_datetime(df['trading_date'])
    df.set_index('trading_date', inplace=True)
    dates = df.index
    tech = TechnicalIndicators(df)
    tech.calculate_ema([10, 21, 50, 200])
    df = tech.df
    df.index = dates
    st = None
    if stats:
        yr = df.tail(252)
        st = {'ltp': float(yr['close'].iloc[-1]),
              'chg1y': float((yr['close'].iloc[-1] / yr['close'].iloc[0] - 1) * 100),
              'wk52_high': float(yr['high'].max()), 'wk52_low': float(yr['low'].min())}
    df = df.tail(days)
    ind = {c: df[c] for c in df.columns if c.startswith('ema_')}
    svg = create_svg_chart(symbol, df, ind, width=w, height=h, title_suffix='Daily', theme=theme,
                           stock_name=name, stats=st)
    svg = _overlay(svg, df, w, h, bool(st), theme, pivot, stop, bo)
    if ol:
        svg = _outline(svg, df, w, h, bool(st), theme, ol)
    # add a viewBox so the chart scales cleanly inside <img> on any screen width
    svg = svg.replace(f'<svg width="{w}" height="{h}"', f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}"', 1)
    with _lock:
        _cache[key] = svg
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return Response(svg, media_type='image/svg+xml', headers={'Cache-Control': 'public, max-age=1800'})
