"""Subprocess entry point — one run per process, started by the API and left
to run unattended. Run with:  python -m backtest.runner --run-id N
(from custom-screener/backend/, same working directory as the API service).
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from app import config
from app.db import create_pool

from .engine import run_backtest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def main(run_id: int) -> None:
    pool = await create_pool()
    try:
        await run_backtest(run_id, pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    args = ap.parse_args()
    asyncio.run(main(args.run_id))
