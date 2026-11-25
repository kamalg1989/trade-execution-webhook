#!/usr/bin/env python3
"""2026-11-25 ohmx.py: cash guard (item 3) + LIQUIDCASE sweep (item 4)."""
import shutil, datetime as dt
P = '/root/ohm_pro_web/exec/ohmx.py'
shutil.copy(P, P + '.bak-items34-' + dt.datetime.now().strftime('%Y%m%d-%H%M'))
s = open(P).read()


def rep(old, new):
    global s
    assert s.count(old) == 1, (old[:80], s.count(old))
    s = s.replace(old, new)


rep("CASH_YIELD = 0.04                               # idle cash in LIQUIDCASE (shadow only)\n",
    "CASH_YIELD = 0.04                               # idle cash in LIQUIDCASE (shadow only)\n"
    "LIQ, LIQ_BUFFER, LIQ_MIN = 'LIQUIDCASE', 0.05, 10000   # LIVE sweep: idle cash minus 5% of equity, min Rs 10k\n"
    "MIN_BUY = 1000\n")

# equity includes the LIQUIDCASE sweep (LIVE)
rep("    b['eq'] = b['cash'] + sum(p['qty'] * p['last'] for p in b['positions'].values())\n",
    "    if b.get('liq_qty'):\n"
    "        df = get_bars(LIQ, asof)\n"
    "        df = df[df.d <= asof]\n"
    "        if len(df):\n"
    "            b['liq_last'] = float(df.c.iloc[-1])\n"
    "    b['eq'] = (b['cash'] + sum(p['qty'] * p['last'] for p in b['positions'].values())\n"
    "               + b.get('liq_qty', 0) * b.get('liq_last', 0.0))\n")

# plan: LIQUIDCASE sweep after the buy-stops
rep("    buy_val = sum(it['qty'] * it['trigger'] for it in items if it['act'] == 'buy')\n",
    "    _liq_items(b, asof, items, est_cash, get_bars)\n"
    "    buy_val = sum(it['qty'] * it['trigger'] for it in items if it['act'] == 'buy')\n")

rep("def cands_from_page():",
    '''def _liq_items(b, asof, items, est_cash, get_bars):
    """Item 4 (LIVE): buys OFF / no buy-stops -> park idle cash (minus 5% of equity) in LIQUIDCASE at the open;
    buy-stops in the plan -> sell the whole LIQUIDCASE holding at the open so the cash is there.
    SHADOW keeps the 4% cash-yield model and only logs what it would do."""
    try:
        df = get_bars(LIQ, asof)
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


def cands_from_page():''')

# shadow_execute must skip liq items if a LIVE->SHADOW switch ever leaves one behind
rep("    order = {'sell_all': 0, 'sell_half': 0, 'buy_half': 1, 'buy': 2}\n",
    "    order = {'sell_all': 0, 'sell_half': 0, 'buy_half': 1, 'buy': 2}\n"
    "    items = [it for it in items if it['act'] in order]\n")

# cash guard helpers before fire()
rep("def fire(b, arm):",
    '''def _a5_resting_buys():
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


def fire(b, arm):''')

rep('''    dhan = _dhan()
    amo = dt.datetime.now().time() < dt.time(9, 0)
    bad = 0
    for it in items:
        sec = dhan.get_security_id(it['sym'])''',
    '''    dhan = _dhan()
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
        sec = dhan.get_security_id(it['sym'])''')

rep("            side = 'BUY' if it['act'] == 'buy_half' else 'SELL'\n",
    "            side = 'BUY' if it['act'] in ('buy_half', 'liq_buy') else 'SELL'\n")

# sync: LIQUIDCASE fills, then the intraday cash guard
rep('''        s = it['sym']
        if it['act'] == 'buy':
            if s not in P:''',
    '''        s = it['sym']
        if it['act'] in ('liq_buy', 'liq_sell'):
            sg = 1 if it['act'] == 'liq_buy' else -1
            b['liq_qty'] = int(b.get('liq_qty') or 0) + sg * new
            b['cash'] -= sg * new * avg; b['liq_last'] = avg
        elif it['act'] == 'buy':
            if s not in P:''')

rep('''        olog(kind='fill', date=today, sym=s, act=it['act'], qty=new, px=round(avg, 2))
    return 0
''',
    '''        olog(kind='fill', date=today, sym=s, act=it['act'], qty=new, px=round(avg, 2))
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
''')

# verify: A5-only reservation (ours is in _a5_reserved now) + LIQUIDCASE holding check
rep("        a5 = dhan._a5_reserved()\n",
    "        a5 = dhan._a5_reserved(include_ohmp=False)\n")
rep("    if b['mode'] == 'LIVE' and b['positions']:\n",
    "    if b['mode'] == 'LIVE' and (b['positions'] or b.get('liq_qty')):\n")
rep('''                exc.append(f'{s}: broker has {have} (after A5) but book has {p["qty"]}')
''',
    '''                exc.append(f'{s}: broker has {have} (after A5) but book has {p["qty"]}')
        lq = int(b.get('liq_qty') or 0)
        if lq and held.get(LIQ, 0) - a5.get(LIQ, 0) < lq:
            exc.append(f'{LIQ}: broker has {held.get(LIQ, 0) - a5.get(LIQ, 0)} (after A5) but book has {lq}')
''')

# status line shows the sweep
rep('''f"open {len(b['positions'])} | closed {len(b['closed'])} | DD {dd:.1f}% | buys_blocked {b['buys_blocked']}")''',
    '''f"open {len(b['positions'])} | closed {len(b['closed'])} | DD {dd:.1f}% | buys_blocked {b['buys_blocked']} | "
        f"liquidcase {b.get('liq_qty', 0)}")''')

open(P, 'w').write(s)
print('patched', P)
