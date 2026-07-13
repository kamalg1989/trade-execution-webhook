"""Config for the AI analysis module. Reuses the screener .env discovery."""
from __future__ import annotations

import os
from pathlib import Path

from app import config as _screener_config  # noqa: F401  (triggers .env load)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Gate
AI_GATE_MODE = os.getenv("AI_GATE_MODE", "hard")            # hard | soft
IFP_GATE_THRESHOLD = float(os.getenv("IFP_GATE_THRESHOLD", "0.30"))

# AI
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-5")
MAX_CONCURRENT_AI = int(os.getenv("MAX_CONCURRENT_AI", "5"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "1500"))
PROMPT_VERSION = os.getenv("AI_PROMPT_VERSION", "v1")
AI_DAILY_CALL_CAP = int(os.getenv("AI_DAILY_CALL_CAP", "500"))

# Charts
CHART_DIR = Path(os.getenv("AI_CHART_DIR", "/tmp/ai_analysis_charts"))
CHART_DIR.mkdir(parents=True, exist_ok=True)

# Verification tolerance (fraction)
LEVEL_TOLERANCE = float(os.getenv("AI_LEVEL_TOLERANCE", "0.02"))

# Data windows
DAILY_BARS = int(os.getenv("AI_DAILY_BARS", "300"))
WEEKLY_BARS = int(os.getenv("AI_WEEKLY_BARS", "150"))
