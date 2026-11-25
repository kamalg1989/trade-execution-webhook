"""/blend/ API (127.0.0.1:8012, nginx /blend/api/). Read-only. GET /api/data needs X-API-Key (dashboard key)."""
import json
from fastapi import FastAPI, Header, HTTPException

DATA = '/root/blend_web/data/data.json'
KEY_FILE = '/root/trade-execution-webhook/api_key.json'
app = FastAPI(title='blend API')


@app.get('/api/health')
def health():
    try:
        return {'ok': True, 'generated': json.load(open(DATA)).get('generated')}
    except Exception:
        return {'ok': True, 'generated': None}


@app.get('/api/data')
def data(x_api_key: str = Header(default='')):
    try:
        good = json.load(open(KEY_FILE)).get('api_key')
    except Exception:
        good = None
    if not good or x_api_key != good:
        raise HTTPException(401, 'Invalid API key')
    try:
        return json.load(open(DATA))
    except Exception:
        raise HTTPException(503, 'data not built yet')
