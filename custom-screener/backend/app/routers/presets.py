"""Backtest presets - save/load custom configurations"""
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class PresetCreate(BaseModel):
    name: str
    strategy: str = "BREAKOUT"
    config: dict


class PresetUpdate(BaseModel):
    config: dict


def _pool(request: Request):
    return request.app.state.pool


@router.get("/presets")
async def list_presets(request: Request):
    """List all saved presets"""
    pool = _pool(request)
    rows = await pool.fetch(
        "SELECT id, name, strategy, created_at, updated_at FROM backtest_presets ORDER BY name"
    )
    return [dict(r) for r in rows]


@router.post("/presets")
async def create_preset(req: PresetCreate, request: Request):
    """Save a new preset"""
    pool = _pool(request)
    try:
        # JSON-serialize the config dict for asyncpg JSONB insert
        config_json = json.dumps(req.config) if isinstance(req.config, dict) else req.config
        row = await pool.fetchrow(
            """INSERT INTO backtest_presets (name, strategy, config)
               VALUES ($1, $2, $3::jsonb)
               RETURNING id, name, strategy, created_at""",
            req.name, req.strategy, config_json
        )
        return dict(row)
    except Exception as e:
        if "duplicate key" in str(e):
            raise HTTPException(status_code=409, detail=f"Preset '{req.name}' already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/presets/{preset_id}")
async def get_preset(preset_id: int, request: Request):
    """Get a preset by ID"""
    pool = _pool(request)
    row = await pool.fetchrow(
        "SELECT * FROM backtest_presets WHERE id = $1", preset_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Preset not found")
    return dict(row)


@router.get("/presets/name/{name}")
async def get_preset_by_name(name: str, request: Request):
    """Get a preset by name"""
    pool = _pool(request)
    row = await pool.fetchrow(
        "SELECT * FROM backtest_presets WHERE name = $1", name
    )
    if not row:
        raise HTTPException(status_code=404, detail="Preset not found")
    return dict(row)


@router.put("/presets/{preset_id}")
async def update_preset(preset_id: int, req: PresetUpdate, request: Request):
    """Update a preset's configuration"""
    pool = _pool(request)
    await pool.execute(
        """UPDATE backtest_presets
           SET config = $1, updated_at = NOW()
           WHERE id = $2""",
        req.config, preset_id
    )
    return await get_preset(preset_id, request)


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, request: Request):
    """Delete a preset"""
    pool = _pool(request)
    result = await pool.execute(
        "DELETE FROM backtest_presets WHERE id = $1", preset_id
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"deleted": True, "id": preset_id}
