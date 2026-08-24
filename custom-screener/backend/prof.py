import asyncio, sys, time
sys.path.insert(0, '/root/trade-execution-webhook')
from datetime import date
from app.db import create_pool
from backtest import funnel

async def go():
    pool = await create_pool()
    for d in (date(2019,6,14), date(2019,6,17), date(2023,6,14)):
        t = time.time(); s = await funnel.funnel_survivors(pool, d); t1 = time.time()-t
        t = time.time(); c = await funnel.build_candidates(pool, d, 400000); t2 = time.time()-t
        print(f'{d}  survivors {len(s):>3} in {t1:.3f}s | build_candidates {len(c):>3} in {t2:.3f}s')
    await pool.close()
asyncio.run(go())
