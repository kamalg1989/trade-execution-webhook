"""Render the v3 few-shot example charts (run once at deploy, from backend/):

  /root/trade-execution-webhook/venv/bin/python -m ai_analysis.scripts.render_v3_examples
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

from ai_analysis import config
from ai_analysis.charting import render_chart

EXAMPLES = [("COHANCE", "2026-01-01", "2026-06-30"),
            ("TNPETRO", "2026-01-01", "2026-06-30")]


async def main():
    from app.db import create_pool
    pool = await create_pool()
    config.EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    for sym, frm, to in EXAMPLES:
        rows = await pool.fetch(
            "SELECT time, open::float, high::float, low::float, close::float, volume::float "
            "FROM ohlcv_data WHERE symbol=$1 AND time::date BETWEEN $2::date AND $3::date "
            "ORDER BY time", sym, pd.Timestamp(frm).date(), pd.Timestamp(to).date())
        df = pd.DataFrame([dict(r) for r in rows])
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df = df.set_index("time").sort_index()
        png = render_chart(df, sym, "daily")
        out = config.EXAMPLES_DIR / f"example_{sym}_2026H1_daily.png"
        out.write_bytes(png)
        print(f"rendered {out} ({len(png)} bytes, {len(df)} bars)")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
