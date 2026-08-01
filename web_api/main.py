"""
Trade Web API - FastAPI Backend
Port: 8004
Purpose: Serve web platform APIs for recommendations, orders, portfolio, and stop loss tracking
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
import logging
import sys
import os
import json
import secrets

# Add parent directory to path for imports
sys.path.insert(0, '/root/trade-execution-webhook')
sys.path.insert(0, os.path.dirname(__file__))

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================
# API KEY MANAGEMENT
# =============================================
API_KEY_FILE = '/root/trade-execution-webhook/api_key.json'

def load_or_create_api_key():
    """Load existing API key or create new one"""
    try:
        if os.path.exists(API_KEY_FILE):
            with open(API_KEY_FILE, 'r') as f:
                data = json.load(f)
                key = data.get('api_key')
                if key:
                    logger.info("✅ Loaded existing API key")
                    return key
    except Exception as e:
        logger.warning(f"Could not load API key: {e}")

    # Generate new key
    new_key = f"sk_{secrets.token_urlsafe(32)}"
    try:
        os.makedirs(os.path.dirname(API_KEY_FILE), exist_ok=True)
        with open(API_KEY_FILE, 'w') as f:
            json.dump({'api_key': new_key}, f)
        logger.info(f"🔑 Generated new API key: {new_key[:10]}...")
    except Exception as e:
        logger.error(f"Could not save API key: {e}")
    return new_key

CURRENT_API_KEY = load_or_create_api_key()

# Setup PIN for accessing API key (password protection). Set via SETUP_PIN env var.
SETUP_PIN = os.getenv('SETUP_PIN', '1234')  # fallback only used if SETUP_PIN is unset
logger.info("🔐 Setup PIN configured")

# =============================================
# API KEY VALIDATION MIDDLEWARE
# =============================================
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Check API key on protected trading endpoints"""

    PROTECTED_PATHS = [
        '/api/buy',
        '/api/close-position',
        '/api/sl',          # covers /api/sl/* actions and /api/sl-alerts writes
    ]

    async def dispatch(self, request: Request, call_next):
        # Check if path is protected
        if any(request.url.path.startswith(p) for p in self.PROTECTED_PATHS):
            if request.method in ('POST', 'PUT', 'DELETE'):
                # Extract API key from header
                api_key = request.headers.get('X-API-Key', '').strip()

                if not api_key:
                    logger.warning(f"❌ Missing API key for {request.url.path}")
                    return JSONResponse(status_code=401, content={"detail": "Missing API key. Set X-API-Key header."})

                if api_key != CURRENT_API_KEY:
                    logger.warning(f"❌ Invalid API key attempt for {request.url.path}")
                    return JSONResponse(status_code=403, content={"detail": "Invalid API key"})

                logger.info(f"✅ Valid API key for {request.method} {request.url.path}")

        response = await call_next(request)
        return response

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

# API Key middleware (must be before CORS)
app.add_middleware(APIKeyMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],  # Explicitly allow X-API-Key header
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
            "charts": "/api/charts/daily",
            "api-key": "/api/security/api-key"
        }
    }


class SetupPINRequest(BaseModel):
    pin: str = None

@app.post("/api/security/api-key")
async def get_api_key(request: SetupPINRequest):
    """Get the current API key (password protected - only you know the PIN)"""
    if not request.pin:
        raise HTTPException(status_code=400, detail="PIN required")

    if request.pin != SETUP_PIN:
        logger.warning(f"❌ Invalid PIN attempt for API key")
        raise HTTPException(status_code=403, detail="Invalid PIN")

    logger.info("✅ API key retrieved with correct PIN")
    return {
        "api_key": CURRENT_API_KEY,
        "message": "API key loaded. Store it securely in your browser.",
        "usage": "X-API-Key header will be auto-sent with all trading requests"
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
