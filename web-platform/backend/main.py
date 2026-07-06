"""
Trade Web API - FastAPI Backend
Port: 8004
Purpose: Serve web platform APIs for recommendations, orders, portfolio, and stop loss tracking
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, '/root/trade-execution-webhook')
sys.path.insert(0, os.path.dirname(__file__))

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import database
from database.db import init_db

# Initialize database (optional - may fail if DB connection unavailable)
try:
    init_db()
    logger.info("✅ Database initialized")
except Exception as e:
    logger.warning(f"⚠️ Database initialization skipped (not critical): {str(e)[:100]}")

# Create FastAPI app
app = FastAPI(
    title="Trade Web API",
    description="Web platform APIs for stock trading recommendations, orders, portfolio tracking, and SL management",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routers
try:
    # Only import health router initially (no entry_engine dependency)
    from routers import health
    app.include_router(health.router, tags=["Health"])
    logger.info("✅ Health router loaded")

    # Try to load each router individually so one failure doesn't block others
    router_modules = [
        ('recommendations', 'Recommendations'),
        ('orders', 'Orders'),
        ('portfolio', 'Portfolio'),
        ('sl_engine', 'Stop Loss'),
        ('charts', 'Charts'),
        ('settings', 'Settings'),
    ]

    for module_name, label in router_modules:
        try:
            module = __import__(f'routers.{module_name}', fromlist=[module_name])
            app.include_router(module.router, prefix="/api", tags=[label])
            logger.info(f"✅ {label} router loaded")
        except Exception as e:
            logger.warning(f"⚠️ {label} router failed to load: {str(e)[:100]}")

    logger.info("✅ Router loading complete")

except Exception as e:
    logger.error(f"❌ Failed to load health router: {e}")
    sys.exit(1)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Trade Web API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "recommendations": "/api/recommendations",
            "orders": "/api/buy",
            "portfolio": "/api/portfolio",
            "sl-alerts": "/api/sl-alerts",
            "charts": "/api/charts/daily"
        }
    }


@app.on_event("startup")
async def startup_event():
    """Startup event handler"""
    logger.info("🚀 Trade Web API starting...")
    logger.info(f"Environment: {os.getenv('ENV', 'development')}")
    logger.info(f"Database: {os.getenv('DATABASE_URL', 'postgresql://localhost')}")


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler"""
    logger.info("🛑 Trade Web API shutting down...")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8004))
    reload = os.getenv("ENV", "development") == "development"

    logger.info(f"Starting server on {host}:{port}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )
