#!/usr/bin/env python3
"""2026-11-25 OHM Pro go-live items 1-4 (approved by Kamal). Idempotent-ish: asserts each anchor once."""
import re, shutil, datetime as dt
TS = dt.datetime.now().strftime('%Y%m%d-%H%M')
W = '/root/trade-execution-webhook'


def patch(path, pairs):
    shutil.copy(path, f'{path}.bak-ohmp-{TS}')
    s = open(path).read()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:70], s.count(old))
        s = s.replace(old, new)
    open(path, 'w').write(s)
    print('patched', path)


OHMP_FN = '''OHMP_BOOK = "/root/ohm_pro_web/data/live_book.json"


def _ohmp_reserved():
    """{normalised symbol: quantity} owned by the OHM Pro executor (LIVE book only;
    a SHADOW book owns nothing). Includes its LIQUIDCASE sweep (liq_qty). 2026-11-25."""
    out = {}
    try:
        import json
        with open(OHMP_BOOK) as fh:
            b = json.load(fh)
        if b.get("mode") != "LIVE":
            return out
        for s, p in (b.get("positions") or {}).items():
            q = int(p.get("qty") or 0)
            if q > 0:
                out[_norm_sym(s)] = out.get(_norm_sym(s), 0) + q
        lq = int(b.get("liq_qty") or 0)
        if lq > 0:
            out["LIQUIDCASE"] = out.get("LIQUIDCASE", 0) + lq
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error("OHMP clamp: could NOT read %s (%s)" % (OHMP_BOOK, e))
    return out


def _a5_reserved(include_ohmp=True):
    """{normalised symbol: quantity} owned by A5 plus (default) the OHM Pro LIVE book.
    include_ohmp=False = A5 only (the OHM Pro executor's own verify uses that)."""
    out = dict(_a5_db_reserved())
    if include_ohmp:
        for k, q in _ohmp_reserved().items():
            out[k] = out.get(k, 0) + q
    return out


def _a5_db_reserved():'''

# 1. web_api/dhan_client.py
patch(W + '/web_api/dhan_client.py', [(
    'def _a5_reserved():\n    """{normalised symbol: quantity} currently owned by A5."""',
    OHMP_FN + '\n    """{normalised symbol: quantity} currently owned by A5."""')])

# 2. sl_engine.py (BAU SL engine, approved)
patch(W + '/sl_engine.py', [(
    'def _a5_reserved():\n    """{normalised symbol: quantity} currently owned by A5. Cached per run."""',
    OHMP_FN + '\n    """{normalised symbol: quantity} currently owned by A5. Cached per run."""')])

# 3. live_trading/a5_live.py verify orphan check
patch(W + '/live_trading/a5_live.py', [(
    '        ever = {r["symbol"] for r in await tr.fetch(\n            "SELECT DISTINCT symbol FROM a5l_position")}',
    '''        # 2026-11-25: the OHM Pro executor shares this account; shares its LIVE
        # book owns are not A5 orphans.
        try:
            for k, q in dhan_client._ohmp_reserved().items():
                for s in list(broker):
                    if q > 0 and dhan_client._norm_sym(s) == k:
                        take = min(broker[s], q); broker[s] -= take; q -= take
        except Exception as e:
            log.warning("verify: could not read the OHM Pro book (%s) - its shares "
                        "may show as orphans", e)
        ever = {r["symbol"] for r in await tr.fetch(
            "SELECT DISTINCT symbol FROM a5l_position")}''')])
