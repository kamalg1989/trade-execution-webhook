"""
Custom Screener standalone backend (FastAPI, port :8005).

Public URL: https://ohmstockvault.duckdns.org/custom-screener/api/...
nginx maps /custom-screener/api/ -> this service's /api/... (prefix stripped).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .routers import backtest, screener, presets, paper

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("custom-screener")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .db import PgRepo, create_pool
    pool = None
    try:
        pool = await create_pool()
        app.state.pool = pool  # Make pool available to all routers
        app.state.repo = PgRepo(pool)
        log.info("✅ DB pool ready (%s:%s/%s)", config.DB_HOST, config.DB_PORT, config.DB_NAME)
    except Exception as e:  # keep the service up so /health reports the problem
        app.state.repo = None
        log.error("❌ DB pool init failed: %s", e)
    yield
    if pool is not None:
        await pool.close()


app = FastAPI(
    title="Custom Screener API",
    version="1.0.0",
    root_path=config.ROOT_PATH,
    lifespan=lifespan,
)

from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(screener.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(paper.router, prefix="/api")
app.include_router(presets.router, prefix="/api")

# AI visual analysis (optional module — requires ANTHROPIC_API_KEY for POST)
try:
    from ai_analysis.api.router import router as ai_router
    app.include_router(ai_router, prefix="/api")
    log.info("✅ AI analysis router mounted")
except Exception as e:  # missing deps (anthropic/mplfinance) shouldn't kill the service
    log.warning("⚠️ AI analysis router not mounted: %s", e)


@app.get("/api/health")
async def health():
    return {"status": "ok", "dbReady": getattr(app.state, "repo", None) is not None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.API_PORT, reload=False)
