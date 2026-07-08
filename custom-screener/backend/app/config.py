"""Standalone config for the Custom Screener backend (:8005)."""
import os
from pathlib import Path

# Load .env if present (own file; falls back to process env)
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    pass

# DB — same PostgreSQL/TimescaleDB as ohlcv_data (read-only use here)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "market_data_user")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "market_data")

# Service
API_PORT = int(os.getenv("CUSTOM_SCREENER_PORT", "8005"))
# Backend sees /api/... ; nginx maps /custom-screener/api/ -> here.
ROOT_PATH = os.getenv("CUSTOM_SCREENER_ROOT_PATH", "")

# Charts API used by the frontend chart modal (data-only dependency)
CHARTS_API_BASE = os.getenv("CHARTS_API_BASE", "/api/v1")

# Compute completeness threshold
COMPLETE_THRESHOLD = int(os.getenv("SNAPSHOT_COMPLETE_THRESHOLD", "2600"))
