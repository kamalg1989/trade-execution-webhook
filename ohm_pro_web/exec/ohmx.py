#!/usr/bin/env python3
"""OHM Pro executor: runs the backtest-passed OHM Pro rules (ohmpro.py) on a LIVE book.

Modes (book['mode']):
  SHADOW  default. Never sends an order. Fills are simulated from the next session's bar with the live
          order model (buy: forever LIMIT trigger x1.002; exits / half-sale / buy-back: market at the open).
  LIVE    places Dhan orders (needs Kamal's go-live OK + the A5 / SL reservation changes).
Timers: plan 22:45 (shadow fills -> close checks -> buy-stops), fire 08:50, sync 09:40 / 15:50, expire 15:20.
Files: data/live_book.json (book), data/live_orders.jsonl (every intent / placement / fill).
Halt: exec/OHMP_HALT (nothing is placed while it exists). No BAU file is changed.
Usage: ohmx.py --init --capital N | --plan | --fire [--arm] | --sync [--arm] | --expire [--arm] | --verify | --status
"""
import argparse, datetime as dt, json, math, os, sys
import numpy as np, pandas as pd, psycopg2
sys.path.insert(0, '/root/ohm_pro_web'); sys.path.insert(0, '/root/trade-execution-webhook')
import ohmpro as O          # rules and constants (MAXPOS, MAXW, RISK, RISK_TOP, TOP_PCT, MAXSTOP, LIVE_LIM)
import compute as C         # tick helpers (its main() is guarded)

ROOT = '/root/ohm_pro_web'
BOOK, OLOG, PRO = ROOT + '/data/live_book.json', ROOT + '/data/live_orders.jsonl', ROOT + '/data/pro.json'
HALT = ROOT + '/exec/OHMP_HALT'
ENV = '/root/trade-execution-webhook/.env'
PREFIX = 'OHMP'
MAX_ORDERS = 60                                 # envelope per fire (backtest places every passing buy-stop; cash decides fills)
DD_LADDER, DD_NOBUY = (10, 15, 20), 25         # % below peak: Telegram alerts; at 25 new buys stop
COST = 0.0025                                   # per leg for shadow fills (realistic backtest)
CASH_YIELD = 0.04                               # idle cash in LIQUIDCASE (shadow only)
LIQ, LIQ_BUFFER, LIQ_MIN = 'LIQUIDCASE', 0.05, 10000   # LIVE sweep: idle cash minus 5% of equity, min Rs 10k
MIN_BUY = 1000


def log(*a):
    print(dt.datetime.now().strftime('[%Y-%m-%d %H:%M:%S]'), *a, flush=True)


def olog(**k):
    k['ts'] = dt.datetime.now().isoformat(timespec='seconds')
    with open(OLOG, 'a') as fh:
        fh.write(json.dumps(k, default=str) + '\n')


def env(key):
    try:
        for ln in open(ENV):
            if ln.startswith(key + '='):
                return ln.split('=', 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return None


def tg(text):
    tok, chat = env('TELEGRAM_TOKEN'), env('TELEGRAM_CHAT_ID')
    if not tok or not chat:
        return
    try:
        import requests
        requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                      json=dict(chat_id=chat, text='OHM Pro: ' + text), timeout=10)
    except Exception as e:
        log('telegram failed:', e)


def load():
    return json.load(open(BOOK))


def save(b):
    tmp = BOOK + '.tmp'
    json.dump(b, open(tmp, 'w'), indent=1, default=str)
    os.replace(tmp, BOOK)


# ---------------------------------------------------------------- market data
def db_bars(sym, since):
    """Daily bars from `since` (inclusive) with EMA21 from full history."""
    conn = psycopg2.connect(**O.DB)
    try:
        df = pd.read_sql("""select (time at time zone 'Asia/Kolkata')::date d, open::float8 o, high::float8 h,
            low::float8 l, close::float8 c, volume::float8 v from ohlcv_data where symbol=%s
            and time >= %s::date - interval '700 days' order by 1""", conn, params=(sym, since))
    finally:
        conn.close()
    df = df.drop_duplicates('d', keep='last')
    df = df[df.v.notna() & (df.c > 0) & (df.l > 0) & (df.h >= df.l)].copy()
    df['e21'] = df.c.ewm(span=21, adjust=False).mean()
    df['d'] = df.d.astype(str)
    return df[df.d >= str(since)][['d', 'o', 'h', 'l', 'c', 'e21']].reset_index(drop=True)


GET_BARS = db_bars          # the validation harness swaps this for in-memory bars


# ---------------------------------------------------------------- book
def new_book(capital, mode='SHADOW', start=None):
    return dict(mode=mode, start=start, cash=float(capital), start_capital=float(capital), eq=float(capital),
                peak=float(capital), asof=start, positions={}, closed=[], curve=[], plan=None, carry=[],
                dd_alerted=[], buys_blocked=False)


def mark(b, asof, get_bars=None):
    """Mark open positions at the asof close; equity, peak, drawdown ladder."""
    get_bars = get_bars or GET_BARS
    for s, p in b['positions'].items():
        df = get_bars(s, p['fill_date'])
        df = df[df.d <= asof]
        if len(df):
            p['last'], p['e21'], p['last_date'] = float(df.c.iloc[-1]), float(df.e21.iloc[-1]), df.d.iloc[-1]
    if b.get('liq_qty'):
        df = get_bars(LIQ, str((pd.Timestamp(asof) - pd.Timedelta(days=10)).date()))
        df = df[df.d <= asof]
        if len(df):
            b['liq_last'] = float(df.c.iloc[-1])
    b['eq'] = (b['cash'] + sum(p['qty'] * p['last'] for p in b['positions'].values())
               + b.get('liq_qty', 0) * b.get('liq_last', 0.0))
    b['peak'] = max(b['peak'], b['eq'])
    if not b['curve'] or b['curve'][-1][0] != asof:
        b['curve'].append([asof, round(b['eq'], 2)])
    else:
        b['curve'][-1][1] = round(b['eq'], 2)
    dd = (1 - b['eq'] / b['peak']) * 100 if b['peak'] > 0 else 0.0
    if dd < 1:
        b['dd_alerted'] = []                       # back near the peak: re-arm the ladder
    for lv in DD_LADDER:
        if dd >= lv and lv not in b['dd_alerted']:
            b['dd_alerted'].append(lv); tg(f'drawdown {dd:.1f}% (alert level {lv}%)')
    if dd >= DD_NOBUY and not b['buys_blocked']:
        b['buys_blocked'] = True; tg(f'drawdown {dd:.1f}% >= {DD_NOBUY}%: NEW BUYS STOPPED (exits continue)')
    b['asof'] = asof
    return dd


def _close(b, s, p, px, date, why):
    q = p['qty']
    p['proc'] += q * px * (1 - COST); b['cash'] += q * px * (1 - COST)
    R = (p['proc'] - p['cost']) / p['risk'] if p['risk'] > 0 else 0.0
    b['closed'].append(dict(sym=s, fill_date=p['fill_date'], exit_date=date, fill=p['fill'], stop=p['stop'], exit=px,
                            R=round(R, 3), pnl=round(p['proc'] - p['cost'], 2), why=why, half=p['half_sold'],
                            added=p['added'], typ=p['typ']))
    del b['positions'][s]
    return R


def shadow_execute(b, asof, get_bars=None):
    """SHADOW: play yesterday's plan on the next session's bar with the live order model."""
    get_bars = get_bars or GET_BARS
    plan = b.get('plan')
    if not plan or plan.get('executed'):
        return []
    items = list(b.get('carry', [])) + plan['items']
    b['carry'], done = [], []
    nxt_bar = {}
    for it in items:
        df = get_bars(it['sym'], plan['date'])
        df = df[(df.d > plan['date']) & (df.d <= asof)]
        nxt_bar[id(it)] = df.iloc[0] if len(df) else None
    order = {'sell_all': 0, 'sell_half': 0, 'buy_half': 1, 'buy': 2}
    items = [it for it in items if it['act'] in order]
    items.sort(key=lambda it: (order[it['act']], -(it.get('r252') or -9)))
    P = b['positions']
    for it in items:
        bar, s = nxt_bar[id(it)], it['sym']
        if bar is None:                               # no session yet / suspended: exits carry, buys lapse
            if it['act'] != 'buy':
                b['carry'].append(it)
            continue
        d, o, h, l = bar.d, float(bar.o), float(bar.h), float(bar.l)
        p = P.get(s)
        if it['act'] == 'sell_all' and p:
            R = _close(b, s, p, o, d, it['why']); done.append((d, s, 'SELL ALL', p['qty'], o, f"R {R:.2f}"))
        elif it['act'] == 'sell_half' and p and not p['half_sold']:
            q = min(it['qty'], p['qty'])
            p['qty'] -= q; p['proc'] += q * o * (1 - COST); b['cash'] += q * o * (1 - COST); p['half_sold'] = True
            done.append((d, s, 'SELL HALF', q, o, it['why']))
        elif it['act'] == 'buy_half' and p and p['half_sold'] and not p['added']:
            q = min(it['qty'], math.floor(b['cash'] / (o * (1 + COST))))
            p['add_tried'] = True
            if q * o >= 1000:
                b['cash'] -= q * o * (1 + COST); p['cost'] += q * o * (1 + COST); p['qty'] += q; p['added'] = True
                done.append((d, s, 'BUY HALF BACK', q, o, it['why']))
        elif it['act'] == 'buy' and s not in P and len(P) < O.MAXPOS:
            trg, lim = it['trigger'], it['limit']
            if h < trg:
                continue                              # never triggered: one-day order lapses
            if o <= lim:
                px = max(o, trg)
            elif l <= lim:
                px = lim
            else:
                continue                              # gapped above the limit and never came back
            if px <= it['stop'] or (px - it['stop']) / px > O.MAXSTOP:
                continue
            q = min(it['qty'], math.floor(b['cash'] / (px * (1 + COST))))
            if q * px < 1000:
                continue
            b['cash'] -= q * px * (1 + COST)
            P[s] = dict(fill=px, stop=it['stop'], fill_date=d, sig_date=plan['date'], typ=it.get('typ'), risk_pct=it['risk_pct'],
                        qty=q, sh0=q, cost=q * px * (1 + COST), proc=0.0, risk=q * (px - it['stop']), half_sold=False,
                        added=False, add_tried=False, last=px, last_date=d)
            done.append((d, s, 'BUY', q, px, f"trigger {trg}"))
    # idle cash yield between the plan date and asof (LIQUIDCASE)
    days = (pd.Timestamp(asof) - pd.Timestamp(plan['date'])).days
    if b['cash'] > 0 and days > 0:
        b['cash'] += b['cash'] * CASH_YIELD * days / 365.0
    plan['executed'] = True
    for x in done:
        olog(kind='shadow_fill', date=x[0], sym=x[1], act=x[2], qty=x[3], px=x[4], note=x[5])
    return done


# ---------------------------------------------------------------- plan (evening, after compute.py)
def _lim(entry, sym, tick_of):
    t = tick_of(sym)
    return round(math.ceil(entry * O.LIVE_LIM / t - 1e-9) * t, 2)


def build_plan(b, asof, cands, buys_on, get_bars=None, tick_of=None):
    """Exits / half-sales / buy-backs for open positions (same rules as the backtest: ohmpro.replay),
    then buy-stops for today's passing candidates sized on the live book."""
    get_bars, tick_of = get_bars or GET_BARS, tick_of or C.tick_of
    items, P = [], b['positions']
    est_cash = b['cash']
    exiting = set()
    for s, p in P.items():
        df = get_bars(s, p['fill_date'])
        df = df[df.d <= asof].reset_index(drop=True)
        if not len(df):
            continue
        st, nxt = O.replay(p['fill'], p['stop'], df)
        last = float(df.c.iloc[-1])
        if st['exit'] is not None:                     # the rules exited earlier but we still hold it: sell now
            items.append(dict(act='sell_all', sym=s, qty=p['qty'], why='missed exit: ' + st['exit'][2]))
        elif nxt is None:
            continue
        elif nxt['act'] == 'sell_all':
            items.append(dict(act='sell_all', sym=s, qty=p['qty'], why=nxt['why']))
        elif nxt['act'] == 'sell_half' and not p['half_sold']:
            q = min(math.floor(0.5 * p['sh0']), p['qty'])
            if q > 0:
                items.append(dict(act='sell_half', sym=s, qty=q, why=nxt['why'])); est_cash += q * last
        elif nxt['act'] == 'buy_half' and p['half_sold'] and not p['added'] and not p.get('add_tried'):
            cur = p['qty'] * last
            notional = min(0.5 * p['sh0'] * last, max(0.0, O.MAXW * b['eq'] - cur), est_cash / (1 + COST))
            q = math.floor(notional / last)
            if q * last >= 1000:
                items.append(dict(act='buy_half', sym=s, qty=q, why=nxt['why'])); est_cash -= q * last
        if items and items[-1]['sym'] == s and items[-1]['act'] == 'sell_all':
            exiting.add(s); est_cash += p['qty'] * last
    if buys_on and not b['buys_blocked']:
        # As in the backtest: a buy-stop for EVERY passing candidate; whichever trigger first take the cash
        # and the free slots (fills beyond cash / 20 positions are refused at execution).
        ok = [c for c in cands if not c.get('block') and (c['sym'] not in P or c['sym'] in exiting)]
        ok.sort(key=lambda c: -(c['r252'] if c.get('r252') is not None and np.isfinite(c['r252']) else -9))
        for c in ok:
            entry = C.up_tick(c['buy'], tick_of(c['sym']))
            rps = entry - c['stop']
            if rps <= 0 or rps / entry > O.MAXSTOP:
                continue
            top = c.get('lpct') is not None and np.isfinite(c['lpct']) and c['lpct'] >= O.TOP_PCT
            rk = O.RISK_TOP if top else O.RISK
            notional = min(rk * b['eq'] / rps * entry, O.MAXW * b['eq'])
            q = math.floor(notional / entry)
            if q * entry < 1000:
                continue
            items.append(dict(act='buy', sym=c['sym'], qty=q, trigger=entry, limit=_lim(entry, c['sym'], tick_of),
                              stop=c['stop'], risk_pct=rk, lpct=c.get('lpct'), r252=c.get('r252'), typ=c.get('typ'),
                              why=f"buy-stop above {entry}, stop {c['stop']}, risk {rk * 100:.2f}%"))
    _liq_items(b, asof, items, est_cash, get_bars)
    buy_val = sum(it['qty'] * it['trigger'] for it in items if it['act'] == 'buy')
    env_ok = len(items) <= MAX_ORDERS and all(it['qty'] * it['trigger'] <= O.MAXW * b['eq'] + 1
                                              for it in items if it['act'] == 'buy')
    b['plan'] = dict(date=asof, items=items, executed=False, fired=False, envelope_ok=env_ok,
                     buy_value=round(buy_val, 0), made=dt.datetime.now().isoformat(timespec='seconds'))
    for it in items:
        olog(kind='plan', date=asof, **it)
    return b['plan']


def _liq_items(b, asof, items, est_cash, get_bars):
    """Item 4 (LIVE): buys OFF / no buy-stops -> park idle cash (minus 5% of equity) in LIQUIDCASE at the open;
    buy-stops in the plan -> sell the whole LIQUIDCASE holding at the open so the cash is there.
    SHADOW keeps the 4% cash-yield model and only logs what it would do."""
    try:
        df = get_bars(LIQ, str((pd.Timestamp(asof) - pd.Timedelta(days=10)).date()))
        df = df[df.d <= asof]
    except Exception:
        return
    if not len(df):
        return
    px = float(df.c.iloc[-1])
    lq = int(b.get('liq_qty') or 0)
    buys = any(it['act'] == 'buy' for it in items)
    it = None
    if buys and lq > 0:
        it = dict(act='liq_sell', sym=LIQ, qty=lq, why='buy-stops in plan: free the parked cash')
    elif not buys:
        idle = est_cash - LIQ_BUFFER * b['eq']
        q = math.floor(idle / (px * 1.001))
        if q * px >= LIQ_MIN:
            it = dict(act='liq_buy', sym=LIQ, qty=q, why=f'no buy-stops: park Rs {q * px:,.0f} idle cash')
    if it is None:
        return
    if b['mode'] == 'LIVE':
        items.append(it)
    else:
        log('shadow (not placed):', it['act'], LIQ, it['qty'], it['why'])


def cands_from_page():
    """Today's candidates from data/pro.json (written by compute.py with the same gates)."""
    d = json.load(open(PRO))
    m = d['meta']
    cands = [dict(sym=r['s'], buy=r['buy'], stop=r['stop'], block=r['block'], typ=r.get('entT'),
                  r252=(r['r1y'] / 100.0) if r.get('r1y') is not None else None,
                  lpct=(r['lpct'] / 100.0) if r.get('lpct') is not None else None)
             for r in d['candidates']]
    return m['asof'], cands, bool(m['buysOn'])


# ---------------------------------------------------------------- live order path (LIVE mode only)
def _dhan():
    from web_api import dhan_client as dhan
    return dhan


def _a5_resting_buys():
    """Rs value A5 still has resting as BUY forever orders (not yet filled). Those funds are A5's."""
    conn = psycopg2.connect('postgresql://postgres:postgres@localhost:5432/trading_platform')
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT coalesce(sum((quantity - coalesce(filled_qty,0)) * coalesce(limit_price, trigger_price, ref_price)),0)"
                        " FROM a5l_order WHERE side='BUY' AND status IN ('PLACED','PARTIAL','TRIGGERED','PENDING')")
            return float(cur.fetchone()[0])
    finally:
        conn.close()


def budget(b, dhan, extra=0.0):
    """Item 3: OHM Pro may spend min(its own book cash, broker free funds - A5's resting buys).
    `extra` = LIQUIDCASE proceeds expected at the open (fire only)."""
    avail = float((dhan._get('/fundlimit') or {}).get('availabelBalance') or 0)
    a5 = _a5_resting_buys()
    bud = min(b['cash'] + extra, avail + extra - a5)
    log(f'budget: book cash {b["cash"]:,.0f} + liq {extra:,.0f} | broker free {avail:,.0f} - A5 resting {a5:,.0f} -> {bud:,.0f}')
    return bud, avail, a5


def fire(b, arm):
    """Morning: send the plan. Exits / half-sales / buy-backs = market at the open (AMO before 09:00);
    buys = forever LIMIT (trigger, trigger x1.002), cancelled at 15:20 if not triggered."""
    plan = b.get('plan')
    if os.path.exists(HALT):
        log('REFUSED: halt file present'); return 2
    if not plan or plan.get('fired'):
        log('nothing to fire'); return 0
    if not plan.get('envelope_ok'):
        log('REFUSED: plan outside envelope'); tg('plan outside envelope, not fired'); return 2
    items = plan['items'] + b.get('carry', [])
    if b['mode'] != 'LIVE' or not arm:
        for it in items:
            log('would place:', it['act'], it['sym'], it['qty'], it.get('trigger', 'MKT'), it.get('limit', ''))
        if b['mode'] != 'LIVE':
            plan['fired'] = True
        return 0
    dhan = _dhan()
    amo = dt.datetime.now().time() < dt.time(9, 0)
    bad = 0
    liq_px = b.get('liq_last') or 0.0
    extra = sum(it['qty'] * liq_px for it in items if it['act'] == 'liq_sell')
    bud, avail, a5 = budget(b, dhan, extra)
    plan['budget'] = round(bud, 0)
    if bud < MIN_BUY:
        for it in items:
            if it['act'] == 'buy':
                it['status'] = 'SKIPPED_NO_CASH'
        tg(f'no buy cash: book {b["cash"]:,.0f}, broker free {avail:,.0f}, A5 resting {a5:,.0f} - buy-stops not placed')
    for it in items:
        if it.get('status') == 'SKIPPED_NO_CASH':
            continue
        sec = dhan.get_security_id(it['sym'])
        if not sec:
            it['status'] = 'NO_SECURITY_ID'; bad += 1; continue
        if it['act'] == 'buy':
            r = dhan.place_forever(sec, it['qty'], 'BUY', it['trigger'], it['limit'], it['sym'], correlation_prefix=PREFIX)
        else:
            side = 'BUY' if it['act'] in ('buy_half', 'liq_buy') else 'SELL'
            r = dhan.place_order(sec, it['qty'], side, 'MARKET', after_market=amo, amo_time='OPEN')
        it['status'] = 'PLACED' if r.get('success') else 'ERROR'
        it['oid'] = r.get('orderId'); it['err'] = r.get('error')
        bad += it['status'] != 'PLACED'
        olog(kind='placed', date=plan['date'], sym=it['sym'], act=it['act'], qty=it['qty'], oid=it['oid'], err=it['err'])
    plan['fired'] = True
    tg(f"fired plan {plan['date']}: {len(items)} order(s), {bad} error(s)")
    return 1 if bad else 0


def sync(b, arm):
    """Apply broker fills to the book (forever orders fill under a child order whose algoId = forever id)."""
    plan = b.get('plan')
    if b['mode'] != 'LIVE' or not plan:
        return 0
    rest = [it for it in plan['items'] if it.get('status') in ('PLACED', 'PARTIAL')]
    if not rest:
        return 0
    dhan = _dhan()
    trades = {}
    for t in dhan.get_trades() or []:
        trades.setdefault(str(t.get('orderId') or ''), []).append(t)
    child = {}
    for o in dhan.get_orders() or []:
        a, i = str(o.get('algoId') or ''), str(o.get('orderId') or '')
        if a and i and a != i:
            child.setdefault(a, []).append(i)
    P, today = b['positions'], str(dt.date.today())
    for it in rest:
        ids = [str(it['oid'])] + child.get(str(it['oid']), [])
        fills = [f for i in ids for f in trades.get(i, [])]
        q = sum(int(f.get('tradedQuantity') or f.get('filledQty') or 0) for f in fills)
        if q <= 0 or not arm:
            continue
        avg = sum(float(f.get('tradedPrice') or 0) * int(f.get('tradedQuantity') or 0) for f in fills) / q
        new = q - it.get('filled', 0)
        if new <= 0:
            continue
        s = it['sym']
        if it['act'] in ('liq_buy', 'liq_sell'):
            sg = 1 if it['act'] == 'liq_buy' else -1
            b['liq_qty'] = int(b.get('liq_qty') or 0) + sg * new
            b['cash'] -= sg * new * avg; b['liq_last'] = avg
        elif it['act'] == 'buy':
            if s not in P:
                P[s] = dict(fill=avg, stop=it['stop'], fill_date=today, sig_date=plan['date'], typ=it.get('typ'),
                            risk_pct=it['risk_pct'], qty=0, sh0=0, cost=0.0, proc=0.0, risk=0.0, half_sold=False,
                            added=False, add_tried=False, last=avg, last_date=today)
            p = P[s]; p['qty'] += new; p['sh0'] += new; p['cost'] += new * avg; b['cash'] -= new * avg
            p['fill'] = p['cost'] / p['sh0']; p['risk'] = p['sh0'] * (p['fill'] - p['stop'])
        elif s in P:
            p = P[s]
            if it['act'] == 'buy_half':
                p['qty'] += new; p['cost'] += new * avg; b['cash'] -= new * avg; p['added'] = True
            else:
                p['qty'] -= new; p['proc'] += new * avg; b['cash'] += new * avg
                if it['act'] == 'sell_half':
                    p['half_sold'] = True
                if p['qty'] <= 0:
                    p['qty'] = 0; _close(b, s, p, avg, today, it['why'])
        it['filled'] = q; it['status'] = 'FILLED' if q >= it['qty'] else 'PARTIAL'
        olog(kind='fill', date=today, sym=s, act=it['act'], qty=new, px=round(avg, 2))
    guard(b, arm, dhan)
    return 0


def guard(b, arm, dhan):
    """Item 3 intraday: once OHM Pro's own cash (or the 20 slots) is used up, cancel the remaining
    untriggered buy-stops so a later trigger cannot spend A5's / the SL strategy's money."""
    plan = b.get('plan') or {}
    rest = [it for it in plan.get('items', []) if it['act'] == 'buy' and it.get('status') == 'PLACED' and not it.get('filled')]
    if not rest:
        return
    now = dt.datetime.now().time()
    if not (dt.time(9, 15) <= now <= dt.time(15, 25)):
        return
    try:
        bud = budget(b, dhan)[0]
    except Exception as e:
        log('guard: budget unknown (%s) - cancelling every resting buy-stop to be safe' % e); bud = -1
    full = len(b['positions']) >= O.MAXPOS
    for it in rest:
        if full or it['qty'] * it['limit'] > bud:
            if arm:
                r = dhan.cancel_forever_order(it['oid'])
                it['status'] = 'CANCELLED_CASH' if r.get('success') else 'CANCEL_FAILED'
            log('guard: cancel', it['sym'], it['qty'], 'x', it['limit'], '| budget', round(bud), '| slots full' if full else '', it.get('status'))
            olog(kind='guard_cancel', sym=it['sym'], qty=it['qty'], budget=round(bud), full=full, status=it.get('status'))


def expire(b, arm):
    """15:20: cancel today's untriggered buy-stops (one-day orders, as in the backtest)."""
    plan = b.get('plan')
    if b['mode'] != 'LIVE' or not plan:
        return 0
    dhan = _dhan()
    for it in plan['items']:
        if it['act'] == 'buy' and it.get('status') == 'PLACED' and not it.get('filled'):
            if arm:
                r = dhan.cancel_forever_order(it['oid'])
                it['status'] = 'CANCELLED' if r.get('success') else 'CANCEL_FAILED'
            log('expire', it['sym'], it.get('status'))
    return 0


# ---------------------------------------------------------------- safety check
def verify(b):
    exc, warn = [], []
    if os.path.exists(HALT):
        warn.append('halt file present')
    asof = b.get('asof')
    for s, p in b['positions'].items():
        if asof and (pd.Timestamp(asof) - pd.Timestamp(p.get('last_date') or p['fill_date'])).days > 7:
            warn.append(f'{s}: no price since {p.get("last_date")}')
    if b['mode'] == 'LIVE' and (b['positions'] or b.get('liq_qty')):
        dhan = _dhan()
        held = {}
        for h in dhan.get_holdings_raw() or []:
            k = dhan._norm_sym(h.get('tradingSymbol') or '')
            held[k] = held.get(k, 0) + int(h.get('totalQty') or 0)
        a5 = dhan._a5_reserved(include_ohmp=False)
        for s, p in b['positions'].items():
            k = dhan._norm_sym(s)
            have = held.get(k, 0) - a5.get(k, 0)
            if have < p['qty']:
                exc.append(f'{s}: broker has {have} (after A5) but book has {p["qty"]}')
        lq = int(b.get('liq_qty') or 0)
        if lq and held.get(LIQ, 0) - a5.get(LIQ, 0) < lq:
            exc.append(f'{LIQ}: broker has {held.get(LIQ, 0) - a5.get(LIQ, 0)} (after A5) but book has {lq}')
    if b['peak'] > 0 and (1 - b['eq'] / b['peak']) * 100 >= DD_NOBUY:
        warn.append('drawdown past the no-buy level')
    for x in warn:
        log('WARN', x)
    for x in exc:
        log('EXCEPTION', x)
    if exc and b['mode'] == 'LIVE':
        open(HALT, 'w').write(dt.datetime.now().isoformat() + '\n' + '\n'.join(exc))
        tg('HALTED: ' + '; '.join(exc[:5]))
    log(f"verify: {len(exc)} exception(s), {len(warn)} warning(s)")
    return exc


def status(b):
    dd = (1 - b['eq'] / b['peak']) * 100 if b['peak'] else 0
    log(f"mode {b['mode']} | asof {b['asof']} | equity {b['eq']:,.0f} | cash {b['cash']:,.0f} | "
        f"open {len(b['positions'])} | closed {len(b['closed'])} | DD {dd:.1f}% | buys_blocked {b['buys_blocked']} | "
        f"liquidcase {b.get('liq_qty', 0)}")
    pl = b.get('plan')
    if pl:
        log(f"plan {pl['date']}: {len(pl['items'])} item(s), buys ₹{pl['buy_value']:,.0f}, envelope "
            f"{'OK' if pl['envelope_ok'] else 'BREACH'}, fired {pl['fired']}, executed {pl.get('executed')}")
        for it in pl['items']:
            log('  ', it['act'], it['sym'], it['qty'], it.get('trigger', ''), it.get('why', ''), it.get('status', ''))
    for s, p in b['positions'].items():
        log(f"   {s}: {p['qty']} @ {p['fill']:.2f} stop {p['stop']} last {p['last']} since {p['fill_date']}")


def main():
    ap = argparse.ArgumentParser()
    for a in ('init', 'plan', 'fire', 'sync', 'expire', 'verify', 'status', 'arm', 'force'):
        ap.add_argument('--' + a, action='store_true')
    ap.add_argument('--capital', type=float)
    a = ap.parse_args()
    if a.init:
        if os.path.exists(BOOK):
            log('book exists: refusing to overwrite', BOOK); return 2
        if not a.capital:
            log('--capital is required'); return 2
        asof = json.load(open(PRO))['meta']['asof']
        save(new_book(a.capital, 'SHADOW', asof)); log('new SHADOW book', a.capital, 'asof', asof); return 0
    b = load()
    rc = 0
    if a.plan:
        asof, cands, buys_on = cands_from_page()
        if b.get('plan') and b['plan']['date'] == asof and not a.force:
            log('plan for', asof, 'already built'); status(b); return 0
        if b['mode'] == 'SHADOW':
            for x in shadow_execute(b, asof):
                log('shadow fill:', *x)
        dd = mark(b, asof)
        pl = build_plan(b, asof, cands, buys_on)
        save(b)
        log(f"plan {asof}: {len(pl['items'])} item(s), buys {'ON' if buys_on else 'OFF'}, DD {dd:.1f}%")
        if pl['items']:
            tg(f"{b['mode']} plan {asof}: " + ', '.join(f"{i['act']} {i['sym']} {i['qty']}" for i in pl['items'][:12]))
        verify(b)
    if a.fire:
        rc = fire(b, a.arm); save(b)
    if a.sync:
        rc = sync(b, a.arm); save(b); verify(b)
    if a.expire:
        rc = expire(b, a.arm); save(b)
    if a.verify:
        rc = 1 if verify(b) else 0
    if a.status:
        status(b)
    return rc


if __name__ == '__main__':
    sys.exit(main())
