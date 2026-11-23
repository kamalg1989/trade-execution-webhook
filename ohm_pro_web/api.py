"""OHM-PRO API (127.0.0.1:8011, nginx /ohm-pro/api/). Backtest-passed setup only.

Public: GET /api/health, /api/data (data/pro.json: market, candidates, paper book)
Key (X-API-Key = dashboard key):
  GET  /api/live            live trades bought from this page: Dhan holding check + rule replay -> next action
  POST /api/live/record     {s, entry, stop, qty, riskPct, sigDate}  (page calls it after /api/buy succeeds)
  POST /api/live/remove     {s}
Buys use the Trade Web API POST /api/buy; sells use the /ohm/ API POST /ohm/api/sell. Nothing duplicated here.
"""
import datetime as dt, json, math, os, sys, threading
import pandas as pd, psycopg2
from fastapi import FastAPI, Header, HTTPException, Body
from fastapi.responses import FileResponse

sys.path.insert(0, '/root/ohm_pro_web')
sys.path.insert(0, '/root/trade-execution-webhook')
import ohmpro as O  # noqa: E402

ROOT = '/root/ohm_pro_web'
DATA, LIVE = ROOT + '/data/pro.json', ROOT + '/data/live.json'
KEY_FILE = '/root/trade-execution-webhook/api_key.json'
app = FastAPI(title='OHM-PRO API')
_lk = threading.Lock()


def need_key(k):
    try:
        good = json.load(open(KEY_FILE)).get('api_key')
    except Exception:
        good = None
    if not good or k != good:
        raise HTTPException(401, 'Invalid API key')


def f(x, n=2):
    try:
        x = float(x)
        return round(x, n) if math.isfinite(x) else None
    except Exception:
        return None


def _load_live():
    try:
        return json.load(open(LIVE))
    except Exception:
        return {}


def _save_live(d):
    tmp = LIVE + '.tmp'
    json.dump(d, open(tmp, 'w'), indent=1)
    os.replace(tmp, LIVE)


@app.get('/api/health')
def health():
    ok = os.path.exists(DATA)
    asof = json.load(open(DATA))['meta']['asof'] if ok else None
    return {'ok': True, 'data': ok, 'asof': asof}


@app.get('/api/data')
def data():
    if not os.path.exists(DATA):
        raise HTTPException(503, 'No data yet: the first daily run has not finished')
    return FileResponse(DATA, media_type='application/json', headers={'Cache-Control': 'no-cache'})


def _bars(sym, since):
    conn = psycopg2.connect(**O.DB)
    try:
        df = pd.read_sql("""select (time at time zone 'Asia/Kolkata')::date d, open::float8 o, high::float8 h, low::float8 l,
            close::float8 c, volume::float8 v from ohlcv_data where symbol=%s and time >= %s::date - interval '700 days' order by 1""",
                         conn, params=(sym, since))
    finally:
        conn.close()
    df = df.drop_duplicates('d', keep='last')
    df = df[df.v.notna() & (df.c > 0) & (df.l > 0) & (df.h >= df.l)].copy()
    df['e21'] = df.c.ewm(span=21, adjust=False).mean()
    return df[df.d >= pd.Timestamp(since).date()][['d', 'o', 'h', 'l', 'c', 'e21']].reset_index(drop=True)


@app.get('/api/live')
def live(x_api_key: str = Header(None)):
    need_key(x_api_key)
    from web_api import dhan_client  # imported, not modified
    try:
        hold = {str(h.get('tradingSymbol', '')).upper(): h for h in dhan_client.get_holdings()}
    except Exception:
        hold = {}
    out = []
    for s, j in sorted(_load_live().items(), key=lambda kv: kv[1].get('recorded', '')):
        h = hold.get(s)
        fill = float(h['avgCostPrice']) if h and h.get('avgCostPrice') else None
        row = dict(s=s, entry=j['entry'], stop=j['stop'], qty=j['qty'], riskPct=j.get('riskPct'), sigDate=j.get('sigDate'),
                   recorded=j.get('recorded'), held=bool(h), heldQty=int(h.get('totalQty') or 0) if h else 0, fill=f(fill),
                   fillDate=j.get('fillDate'), state=None, next=None, last=None,
                   secId=str(h.get('securityId')) if h else None, avail=int(h.get('availableQty') or 0) if h else 0)
        if fill and not row['fillDate'] and j.get('sigDate'):
            try:                                            # first day after the signal day that traded above the buy level
                b = _bars(s, j['sigDate'])
                hit = b[(b.d > pd.Timestamp(j['sigDate']).date()) & (b.h >= j['entry'])]
                if len(hit):
                    row['fillDate'] = str(hit.d.iloc[0]); row['fillDateAuto'] = True
            except Exception:
                pass
        if fill and row['fillDate']:
            try:
                df = _bars(s, row['fillDate'])
                if len(df):
                    st, nxt = O.replay(fill, j['stop'], df)
                    rps = fill - j['stop']
                    row.update(state={k: st[k] for k in ('touched', 'kept', 'half_sold', 'added')}, events=st['events'],
                               exit=st['exit'], next=nxt, last=f(df.c.iloc[-1]), e21=f(df.e21.iloc[-1]), t2r=f(fill + 2 * rps),
                               rNow=f((df.c.iloc[-1] - fill) / rps, 2) if rps > 0 else None, asof=str(df.d.iloc[-1]))
            except Exception as e:
                row['error'] = str(e)
        out.append(row)
    return {'positions': out}


@app.post('/api/live/record')
def record(body: dict = Body(...), x_api_key: str = Header(None)):
    need_key(x_api_key)
    s = str(body.get('s', '')).upper().strip()
    if not s:
        raise HTTPException(400, 'symbol missing')
    with _lk:
        d = _load_live()
        d[s] = dict(entry=float(body['entry']), stop=float(body['stop']), qty=int(body['qty']), riskPct=body.get('riskPct'),
                    sigDate=body.get('sigDate'), fillDate=body.get('fillDate'), recorded=dt.datetime.now().isoformat(timespec='seconds'))
        _save_live(d)
    return {'ok': True}


@app.post('/api/live/fill')
def set_fill(body: dict = Body(...), x_api_key: str = Header(None)):
    """Fill date of a live trade (the day the buy-stop triggered) - needed for the 21 EMA / 2R replay."""
    need_key(x_api_key)
    s = str(body.get('s', '')).upper()
    with _lk:
        d = _load_live()
        if s not in d:
            raise HTTPException(404, 'not in the live list')
        d[s]['fillDate'] = str(pd.Timestamp(body['fillDate']).date())
        _save_live(d)
    return {'ok': True}


@app.post('/api/live/remove')
def remove(body: dict = Body(...), x_api_key: str = Header(None)):
    need_key(x_api_key)
    with _lk:
        d = _load_live()
        d.pop(str(body.get('s', '')).upper(), None)
        _save_live(d)
    return {'ok': True}
