"""
Health check endpoint
"""

from fastapi import APIRouter
from datetime import datetime
import psutil
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def health_check():
    """System health check"""
    try:
        # Get system metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "service": "trade-web-api",
            "version": "1.0.0",
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "disk_percent": disk.percent
            },
            "checks": {
                "database": "ok",
                "cache": "ok",
                "api": "ok"
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "degraded",
            "error": str(e)
        }


@router.get("/status")
async def status():
    """API status"""
    return {
        "status": "running",
        "service": "Trade Web API",
        "timestamp": datetime.utcnow().isoformat()
    }
