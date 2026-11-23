"""OHM /ohm/ API (127.0.0.1:8009, nginx /ohm/api/).

Public:  GET /api/health, /api/data (nightly ohm.json)
Key:     X-API-Key = the dashboard key (/root/trade-execution-webhook/api_key.json)
         GET /api/positions  live Dhan holdings (A5 shares excluded by dhan_client) + course sell rules
         GET /api/risk       drawdown, open risk, last-50 closed trades, buy lock state
         POST /api/review    unlocks the -5% lock
         POST /api/sell      sell qty (market in session, AMO at open otherwise)
         POST /api/safety    forever SELL safety order 12% below entry
Buys go through the existing Trade Web API POST /api/buy (forever order) — not duplicated here.
"""
import datetime as dt, json, math, os, sys, threading, time
import numpy as np, pandas as pd, psycopg2
from fastapi import FastAPI, Header, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, '/root/trade-execution-webhook')
from web_api import dhan_client  # noqa: E402  (imported, not modified)
import tick_utils  # noqa: E402

DATA = '/root/ohm_web/data/ohm.json'
REVIEWS = '/root/ohm_web/data/reviews.json'
KEY_FILE = '/root/trade-execution-webhook/api_key.json'
MD = dict(host='localhost', dbname='market_data', user='postgres', password='postgres')
TP = dict(host='localhost', dbname='trading_platform', user='postgres', password='postgres')
LOCK_REVIEW, LOCK_PAPER, LOCK_STOP = -5.0, -10.0, -15.0
SAFETY_PCT = 8.0  # same as BAU sl_engine SAFETY_SL_PCT 0.92
app = FastAPI(title='OHM /ohm/ API')
_cache = {}
_lk = threading.Lock()


def need_key(k):
    try:
        good = json.load(open(KEY_FILE)).get('api_key')
    except Exception:
        good = None
    if not good or k != good:
        raise HTTPException(401, 'Invalid API key')


def cached(name, ttl, fn):
    with _lk:
        v = _cache.get(name)
        if v and time.time() - v[0] < ttl:
            return v[1]
    out = fn()
    with _lk:
        _cache[name] = (time.time(), out)
    return out


def meta():
    try:
        return json.load(open(DATA))['meta']
    except Exception:
        return {'capital': 400000.0, 'riskPct': 0.25, 'safetyPct': 12.0}


def f(x, n=2):
    try:
        x = float(x)
        return round(x, n) if math.isfinite(x) else None
    except Exception:
        return None


def market_open(now=None):
    now = now or dt.datetime.now()
    return now.weekday() < 5 and dt.time(9, 15) <= now.time() <= dt.time(15, 25)


@app.get('/api/health')
def health():
    return {'ok': True, 'data': os.path.exists(DATA), 'generated': meta().get('generated')}


@app.get('/api/data')
def data():
    return FileResponse(DATA, media_type='application/json', headers={'Cache-Control': 'no-cache'})


# ---------------- positions ----------------
def _bars(symbols):
    if not symbols:
        return {}
    conn = psycopg2.connect(**MD)
    try:
        df = pd.read_sql("""select symbol, (time at time zone 'Asia/Kolkata')::date d, high::float8 h, low::float8 l,
            close::float8 c from ohlcv_data where symbol = any(%s) and time >= now() - interval '200 days' order by 1, 2""",
                         conn, params=(list(symbols),))
    finally:
        conn.close()
    out = {}
    for s, g in df.groupby('symbol'):
        c, h, l = g.c, g.h, g.l
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        e10 = c.ewm(span=10, adjust=False).mean()
        out[s] = dict(close=float(c.iloc[-1]), date=str(g.d.iloc[-1]), e10=float(e10.iloc[-1]),
                      e10slope=float((e10.iloc[-1] / e10.iloc[-6] - 1) * 100) if len(e10) > 6 else None,
                      e21=float(c.ewm(span=21, adjust=False).mean().iloc[-1]),
                      s50=float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else None,
                      atr=float(tr.rolling(14).mean().iloc[-1]) if len(c) >= 15 else None)
    return out


def _open_trades():
    conn = psycopg2.connect(**TP)
    try:
        cur = conn.cursor()
        cur.execute("""select symbol, security_id, actual_buy_price, buy_trigger_price, structural_sl, current_sl_price,
            current_sl_order_id, half_booked, buy_filled_at, entry_type, quantity, status
            from trades where status not in ('CLOSED','CANCELLED','REJECTED','EXPIRED') order by id desc""")
        cols = [c[0] for c in cur.description]
        res = {}
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            res.setdefault(d['symbol'].upper(), d)
        return res
    finally:
        conn.close()


def _bau_sl():
    """BAU SL view (Trade Web API /api/sl-alerts): forever SL orders, structural SL, R multiple."""
    import requests
    try:
        k = json.load(open(KEY_FILE)).get('api_key')
        r = requests.get('http://127.0.0.1:8004/api/sl-alerts', headers={'X-API-Key': k}, timeout=40)
        r.raise_for_status()
        return {str(x.get('securityId')): x for x in r.json().get('positions', [])}
    except Exception:
        return {}


def _rules(p, b, trade):
    """Course sell rules (V9/V10) -> (state, message, action)."""
    stop = p.get('structural') or p.get('stop')   # BAU: close below structural SL = exit
    if b is None:
        return 'watch', ('BSE only: no chart data' if p.get('exchange', 'NSE').startswith('BSE') else 'No price data'), None
    close = b['close']
    if stop and close < stop:
        return 'alert', 'Closed below structural stop' if p.get('structural') else 'Closed below stop', 'exit'
    if p.get('r') is not None and p['r'] >= 2 and not p.get('halfBooked'):
        return 'alert', '2R reached: book half', 'half'
    if b.get('atr') and (close - b['e10']) / b['atr'] >= 3:
        return 'alert', 'Stretched 3+ ATR above 10 EMA', 'half'
    if close < b['e21'] and p.get('r') is not None and p['r'] > 0:
        return 'alert', 'Closed below 21 EMA', 'exit'
    if stop and (close - stop) / close * 100 <= 1.5:
        return 'watch', f"{(close - stop) / close * 100:.1f}% above stop", None
    return 'ok', 'Holding', None


def positions_payload():
    hold = dhan_client.get_holdings()  # A5 shares already excluded (dhan_client._clamp_rows)
    today = [p for p in dhan_client.get_positions() if str(p.get('positionType', '')).upper() == 'LONG'
             and int(p.get('netQty') or 0) > 0 and str(p.get('productType', '')).upper() == 'CNC']
    rows = {}
    for h in hold:
        q = int(h.get('totalQty') or h.get('availableQty') or 0)
        if q > 0:
            rows[h['tradingSymbol'].upper()] = dict(symbol=h['tradingSymbol'].upper(), securityId=str(h.get('securityId')),
                                                   qty=q, avail=int(h.get('availableQty') or 0),
                                                   avg=float(h.get('avgCostPrice') or 0),
                                                   ltp=float(h.get('lastTradedPrice') or 0), exchange=str(h.get('exchange') or 'NSE').upper())
    for p in today:
        s = p['tradingSymbol'].upper()
        r = rows.setdefault(s, dict(symbol=s, securityId=str(p.get('securityId')), qty=0, avail=0,
                                    avg=float(p.get('buyAvg') or p.get('costPrice') or 0), ltp=0))
        r['qty'] += int(p.get('netQty') or 0)
        r['today'] = True
    trades = _open_trades()
    bars = _bars(rows.keys())
    bau = _bau_sl()
    out = []
    for s, p in rows.items():
        t = trades.get(s) or {}
        a = bau.get(p['securityId']) or {}
        entry = f(a.get('entry_price')) or f(t.get('actual_buy_price')) or f(p['avg'])
        # stop = live Dhan forever SL trigger (BAU /api/sl-alerts); journal only as fallback
        stop = f(a.get('stop_loss')) or f(t.get('current_sl_price')) or None
        b = bars.get(s)
        close = b['close'] if b else (p['ltp'] or entry)
        # R exactly as BAU sl_engine: R unit = buy - (structural SL if an SL rests, else -8% safety)
        avg = f(a.get('avgCost')) or f(p['avg'])
        has_sl = bool(a.get('slOrders'))
        r_stop = (a.get('structuralSL') or a.get('safetySL')) if has_sl else (a.get('safetySL') or a.get('structuralSL'))
        rps = (avg - r_stop) if (avg and r_stop and avg > r_stop) else None
        p.update(entry=entry, stop=stop, close=f(close), entryType=t.get('entry_type'),
                 filled=str(t.get('buy_filled_at'))[:10] if t.get('buy_filled_at') else None,
                 slOrder=has_sl or bool(t.get('current_sl_order_id')),
                 halfBooked=bool(a.get('halfBooked')) or bool(t.get('half_booked')),
                 slBasis=a.get('slBasis'), structural=f(a.get('structuralSL')), rStop=f(r_stop),
                 gainPct=f((close / entry - 1) * 100, 1) if entry else None,
                 pnl=f((close - avg) * p['qty'], 0) if avg else None,
                 r=f(a['rMultiple'], 2) if a.get('rMultiple') is not None else (f((close - avg) / rps, 2) if rps else None),
                 riskAtEntry=f(rps * p['qty'], 0) if rps else None,
                 atrAbove10=f((close - b['e10']) / b['atr'], 1) if b and b.get('atr') else None,
                 e21=f(b['e21']) if b else None, safety=f(entry * (1 - SAFETY_PCT / 100)) if entry else None, exchange=p.get('exchange', 'NSE'))
        p['state'], p['msg'], p['action'] = _rules(p, b, t)
        out.append(p)
    order = {'alert': 0, 'watch': 1, 'ok': 2}
    out.sort(key=lambda x: (order[x['state']], -(x['pnl'] or 0)))
    return dict(asof=dt.datetime.now().isoformat(timespec='seconds'), marketOpen=market_open(), positions=out,
                summary=dict(n=len(out), alerts=sum(1 for x in out if x['state'] == 'alert'),
                             pnl=f(sum(x['pnl'] or 0 for x in out), 0),
                             riskAtEntry=f(sum(x['riskAtEntry'] or 0 for x in out), 0)))


@app.get('/api/positions')
def positions(x_api_key: str = Header(None)):
    need_key(x_api_key)
    try:
        return cached('pos', 60, positions_payload)
    except Exception as e:
        raise HTTPException(502, f'Dhan: {str(e)[:150]}')


# ---------------- risk ----------------
def _reviews():
    try:
        return json.load(open(REVIEWS))
    except Exception:
        return []


def risk_payload():
    m = meta()
    cap = float(m.get('capital') or 400000)
    conn = psycopg2.connect(**TP)
    try:
        cur = conn.cursor()
        cur.execute("""select symbol, quantity, actual_buy_price, sell_price, sell_date, risk_per_share, structural_sl
            from trades where status = 'CLOSED' and sell_price is not null and actual_buy_price is not null
            order by sell_date""")
        closed = cur.fetchall()
    finally:
        conn.close()
    eq, peak, curve = cap, cap, []
    rs = []
    for sym, q, buy, sell, sd, rps, sl in closed:
        pnl = (float(sell) - float(buy)) * int(q)
        eq += pnl
        peak = max(peak, eq)
        curve.append([str(sd)[:10], round(eq)])
        risk = float(rps) if rps else (float(buy) - float(sl) if sl and float(buy) > float(sl) else None)
        if risk and risk > 0:
            rs.append((float(sell) - float(buy)) / risk)
    try:
        pos = cached('pos', 60, positions_payload)
        unreal = pos['summary']['pnl'] or 0
        open_n, open_risk = pos['summary']['n'], pos['summary']['riskAtEntry'] or 0
    except Exception:
        unreal, open_n, open_risk = 0, None, None
    now_eq = eq + unreal
    peak = max(peak, now_eq)
    dd = round((now_eq / peak - 1) * 100, 2)
    last = rs[-50:]
    wins, losses = [x for x in last if x > 0], [x for x in last if x <= 0]
    reviewed = any(r.get('dd', 0) <= LOCK_REVIEW and r.get('peak') == round(peak) for r in _reviews())
    if dd <= LOCK_STOP:
        lock = 'stop'
    elif dd <= LOCK_PAPER:
        lock = 'paper'
    elif dd <= LOCK_REVIEW and not reviewed:
        lock = 'review'
    else:
        lock = None
    return dict(capital=cap, equity=round(now_eq), peak=round(peak), drawdown=dd, lock=lock,
                levels=dict(review=LOCK_REVIEW, paper=LOCK_PAPER, stop=LOCK_STOP),
                open=dict(n=open_n, slots=20, riskAtEntry=open_risk,
                          riskPct=f(open_risk / cap * 100, 2) if open_risk is not None else None),
                journal=dict(n=len(last), winRate=f(len(wins) / len(last) * 100, 0) if last else None,
                             avgWin=f(np.mean(wins), 2) if wins else None, avgLoss=f(np.mean(losses), 2) if losses else None,
                             edge=f(np.mean(last), 2) if last else None, bigLosses=sum(1 for x in last if x < -1.2)),
                curve=curve[-120:])


@app.get('/api/risk')
def risk(x_api_key: str = Header(None)):
    need_key(x_api_key)
    return cached('risk', 60, risk_payload)


@app.post('/api/review')
def review(body: dict = Body(...), x_api_key: str = Header(None)):
    need_key(x_api_key)
    text = str(body.get('text', '')).strip()
    if len(text) < 30:
        raise HTTPException(400, 'Write at least a few lines: what went wrong and what you will change.')
    r = risk_payload()
    rv = _reviews()
    rv.append(dict(at=dt.datetime.now().isoformat(timespec='seconds'), dd=r['drawdown'], peak=r['peak'], text=text[:4000]))
    json.dump(rv, open(REVIEWS, 'w'), indent=1)
    with _lk:
        _cache.pop('risk', None)
    return {'ok': True}


# ---------------- actions ----------------
def _holding(sec_id):
    for h in dhan_client.get_holdings():
        if str(h.get('securityId')) == str(sec_id):
            return h
    return None


@app.post('/api/sell')
def sell(body: dict = Body(...), x_api_key: str = Header(None)):
    need_key(x_api_key)
    sec, qty = str(body.get('securityId', '')), int(body.get('qty') or 0)
    h = _holding(sec)
    if not h:
        raise HTTPException(404, 'Holding not found (A5 shares are never sold from here)')
    avail = int(h.get('availableQty') or 0)
    if qty <= 0 or qty > avail:
        raise HTTPException(400, f'Can sell up to {avail} now. If a stop-loss order holds the shares, adjust it from the SL tab first.')
    amo = not market_open()
    if _seg(h) == 'NSE_EQ':
        r = dhan_client.place_order(security_id=sec, quantity=qty, transaction_type='SELL', order_type='MARKET',
                                    product_type='CNC', after_market=amo, amo_time='OPEN')
    else:  # BSE-only holding (e.g. NSE Ltd shares): dhan_client hardcodes NSE_EQ, so send our own payload
        payload = {'dhanClientId': dhan_client.get_client_id(), 'transactionType': 'SELL', 'exchangeSegment': _seg(h),
                   'productType': 'CNC', 'orderType': 'MARKET', 'validity': 'DAY', 'securityId': sec, 'quantity': qty,
                   'price': 0, 'triggerPrice': 0, 'disclosedQuantity': 0, 'afterMarketOrder': amo}
        if amo:
            payload['amoTime'] = 'OPEN'
        r = _post(f'{dhan_client.BASE}/orders', payload)
    if not r.get('success'):
        raise HTTPException(400, f"Sell failed: {r.get('error')}")
    with _lk:
        _cache.pop('pos', None)
    when = 'at tomorrow\'s open' if amo else 'now at market'
    return {'success': True, 'orderId': r.get('orderId'),
            'message': f"Sell {qty} {h.get('tradingSymbol')} placed {when}."}


@app.post('/api/safety')
def safety(body: dict = Body(...), x_api_key: str = Header(None)):
    """−8% safety SL, same as the BAU dashboard (sl_engine.place_safety_sl via /api/sl/place-safety).
    BSE-only holdings get the same order sent with exchangeSegment BSE_EQ (BAU hardcodes NSE_EQ)."""
    need_key(x_api_key)
    import requests
    sec = str(body.get('securityId', ''))
    h = _holding(sec)
    if not h:
        raise HTTPException(404, 'Holding not found')
    sym = h.get('tradingSymbol')
    qty = int(h.get('availableQty') or h.get('totalQty') or 0)
    entry = float(body.get('entry') or h.get('avgCostPrice') or 0)
    if qty <= 0 or entry <= 0:
        raise HTTPException(400, 'No free quantity or entry price')
    if _seg(h) == 'NSE_EQ':
        k = json.load(open(KEY_FILE)).get('api_key')
        r = requests.post('http://127.0.0.1:8004/api/sl/place-safety', headers={'X-API-Key': k},
                          json={'securityId': sec, 'quantity': qty, 'symbol': sym, 'entry': entry}, timeout=40)
        out = r.json() if r.text else {}
        if r.status_code != 200 or not out.get('success'):
            raise HTTPException(400, out.get('detail') or 'Safety SL failed')
    else:
        tick = 0.05
        trig = math.floor(entry * (1 - SAFETY_PCT / 100) / tick) * tick
        price = math.floor(trig * 0.995 / tick) * tick
        payload = {'dhanClientId': dhan_client.get_client_id(), 'correlationId': 'OHMSAFE' + str(int(time.time()))[-9:],
                   'orderFlag': 'SINGLE', 'transactionType': 'SELL', 'exchangeSegment': _seg(h), 'productType': 'CNC',
                   'orderType': 'LIMIT', 'validity': 'DAY', 'securityId': sec, 'quantity': qty,
                   'price': round(price, 2), 'triggerPrice': round(trig, 2)}
        out = _post(f'{dhan_client.BASE}/forever/orders', payload)
        if not out.get('success'):
            raise HTTPException(400, f"Safety SL failed: {out.get('error')}")
        out['message'] = f'−8% safety SL placed for {sym} on BSE @ ₹{round(trig, 2)}'
    with _lk:
        _cache.pop('pos', None)
    return {'success': True, 'orderId': out.get('orderId'), 'message': out.get('message') or f'Safety SL placed for {sym}'}


def _seg(h):
    return 'BSE_EQ' if str(h.get('exchange', 'NSE')).upper().startswith('BSE') else 'NSE_EQ'


def _post(url, payload):
    r = dhan_client._request('POST', url, json=payload)
    b = r.json() if r.text else {}
    if r.status_code not in (200, 201):
        return {'success': False, 'error': b.get('errorMessage') or b.get('message') or r.text[:200]}
    return {'success': True, 'orderId': b.get('orderId')}
