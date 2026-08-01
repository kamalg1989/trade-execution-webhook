"""
Screener Settings Router
Reads/writes screener_settings.json consumed by screen_gpt.py at scan start.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import json
import os
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

SETTINGS_FILE = '/root/trade-execution-webhook/screener_settings.json'

DEFAULTS = {
    "values": {
        "capital": 400000,
        "maxAlertsPerRun": 3,
        "minTurnoverCr": 10,
        "targetStrategy": "FIXED_R",
        "targetRMultiple": 2.0,
        "techMaxBaseRangePct": 20,
        "trendAlignmentMode": "medium",
        "baseMinPriorUpmovePct": 15,
        "baseMaxGivebackPct": 30,
        "maxBaseStage": 4,
        "ifpMinScore": 0.25,
        "fundMaxPE": 80,
        "fundMinROEPct": 15,
    },
    "features": {
        "liquidityGate": True,
        "technicalGate": True,
        "baseQualityGate": True,
        "fundamentalGate": True,
        "ifpGate": True,
        "gptConfirmation": True,
        "telegramAlerts": True,
        "hardStopOnDecline": True,
        "pullbackTrigger": False,
        "breakoutRetestTrigger": False,
    },
}

FEATURE_LABELS = {
    "liquidityGate": "Liquidity Gate (min daily turnover)",
    "technicalGate": "Technical Gate (trend + base range + volume)",
    "baseQualityGate": "Base Quality Gate (upmove/giveback/vol dry-up)",
    "fundamentalGate": "Fundamental Gate (growth/ROE/PE/promoter)",
    "ifpGate": "Institutional Footprint (IFP) Gate",
    "gptConfirmation": "GPT Chart Confirmation (veto layer)",
    "telegramAlerts": "Telegram Alerts",
    "hardStopOnDecline": "Hard Stop in DECLINE Regime",
    "pullbackTrigger": "Pullback-to-EMA21 Entry Trigger",
    "breakoutRetestTrigger": "Breakout-Retest Entry Trigger",
}


def _load():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                stored = json.load(f)
            # merge with defaults so new keys appear automatically
            merged = {
                "values": {**DEFAULTS["values"], **stored.get("values", {})},
                "features": {**DEFAULTS["features"], **stored.get("features", {})},
            }
            return merged
        except Exception as e:
            logger.error(f"Failed to read settings: {e}")
    return DEFAULTS


@router.get("/settings")
async def get_settings():
    cfg = _load()
    return {**cfg, "featureLabels": FEATURE_LABELS}


@router.post("/settings")
async def save_settings(payload: dict):
    values = payload.get("values", {})
    features = payload.get("features", {})

    # Basic validation
    try:
        cap = float(values.get("capital", 400000))
        if cap <= 0:
            raise ValueError("capital must be > 0")
        maxAlerts = int(values.get("maxAlertsPerRun", 3))
        if not (1 <= maxAlerts <= 20):
            raise ValueError("maxAlertsPerRun must be 1-20")
        rMult = float(values.get("targetRMultiple", 2.0))
        if not (0.5 <= rMult <= 10):
            raise ValueError("targetRMultiple must be 0.5-10")
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    cfg = {
        "values": {**DEFAULTS["values"], **values},
        "features": {**DEFAULTS["features"], **{k: bool(v) for k, v in features.items()}},
    }

    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"success": True, "message": "Settings saved. Applied on next scan.", **cfg}


@router.post("/settings/reset")
async def reset_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"success": True, "message": "Settings reset to defaults", **DEFAULTS}
