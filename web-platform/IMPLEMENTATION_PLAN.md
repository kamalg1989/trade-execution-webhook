# Web Platform Implementation Plan

## Overview
Complete web-based replacement for Telegram webhook system. Users can view daily recommendations, place orders, track portfolio P&L, and manage stop losses—all from a modern web dashboard.

---

## Phase 1: Backend Setup (2-3 days)

### Create FastAPI Server (Port 8004)

**File**: `web_api/main.py`

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import recommendations, orders, portfolio, sl_engine, charts

app = FastAPI(title="Trade Web API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(recommendations.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(sl_engine.router, prefix="/api")
app.include_router(charts.router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
```

### Router 1: Recommendations (`web_api/routers/recommendations.py`)

**Purpose**: Daily stock screening from screen_gpt.py

```python
from fastapi import APIRouter
from datetime import datetime
import subprocess
import json

router = APIRouter()

@router.get("/recommendations")
async def get_recommendations():
    """Get daily stock recommendations from screen_gpt"""
    try:
        # Call screen_gpt.py
        result = subprocess.run(
            ['python', 'screen_gpt.py', '--json'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return {"error": "Failed to generate recommendations"}
        
        recommendations = json.loads(result.stdout)
        
        # Format for frontend
        return {
            "stocks": [
                {
                    "symbol": r['symbol'],
                    "company": r['company_name'],
                    "currentPrice": r['current_price'],
                    "change": r['change_percent'],
                    "target": r['target_price'],
                    "stopLoss": r['stop_loss'],
                    "confidence": r['confidence_score'],
                    "reason": r['analysis_reason'],
                    "recommendedQty": 1
                }
                for r in recommendations
            ],
            "generatedAt": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e)}
```

### Router 2: Orders (`web_api/routers/orders.py`)

**Purpose**: Buy order placement via Dhan API

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dhan_api import DhanClient

router = APIRouter()

class BuyOrder(BaseModel):
    symbol: str
    quantity: int
    price: float
    stopLoss: float

@router.post("/buy")
async def place_buy_order(order: BuyOrder):
    """Place a buy order through Dhan API"""
    try:
        dhan = DhanClient()
        
        # Place order
        response = dhan.place_order(
            symbol=order.symbol,
            quantity=order.quantity,
            price=order.price,
            side='BUY'
        )
        
        if not response.get('success'):
            raise HTTPException(status_code=400, detail=response.get('error'))
        
        order_id = response['orderId']
        
        # Register stop loss in SL Engine
        from sl_engine import SLEngine
        sl = SLEngine()
        sl.register_position(
            order_id=order_id,
            symbol=order.symbol,
            entry_price=order.price,
            stop_loss=order.stopLoss,
            quantity=order.quantity
        )
        
        return {
            "success": True,
            "orderId": order_id,
            "message": f"Order placed for {order.symbol}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### Router 3: Portfolio (`web_api/routers/portfolio.py`)

**Purpose**: P&L tracking and portfolio analytics

```python
from fastapi import APIRouter
from sqlalchemy import select
from database import get_session, Trade

router = APIRouter()

@router.get("/portfolio")
async def get_portfolio(timeframe: str = "1m"):
    """Get portfolio P&L and performance"""
    session = get_session()
    
    # Query trades from DB
    trades = session.query(Trade).filter(Trade.status == 'OPEN').all()
    
    # Get current prices from Dhan API
    from dhan_api import DhanClient
    dhan = DhanClient()
    
    total_invested = 0
    total_value = 0
    positions = []
    
    for trade in trades:
        current_price = dhan.get_price(trade.symbol)
        
        invested = trade.entry_price * trade.quantity
        current_val = current_price * trade.quantity
        pnl = current_val - invested
        pnl_percent = (pnl / invested) * 100
        
        total_invested += invested
        total_value += current_val
        
        positions.append({
            "symbol": trade.symbol,
            "quantity": trade.quantity,
            "avgCost": trade.entry_price,
            "currentPrice": current_price,
            "value": current_val,
            "pnl": pnl,
            "pnlPercent": pnl_percent
        })
    
    # Calculate performance history
    performance_history = []  # Query from DB based on timeframe
    
    return {
        "totalInvested": total_invested,
        "totalValue": total_value,
        "unrealizedPnL": total_value - total_invested,
        "realizedPnL": 0,  # Query closed trades
        "positions": positions,
        "performanceHistory": performance_history
    }
```

### Router 4: Stop Loss (`web_api/routers/sl_engine.py`)

**Purpose**: SL tracking and alerts

```python
from fastapi import APIRouter
from sl_engine import SLEngine

router = APIRouter()

@router.get("/sl-alerts")
async def get_sl_alerts():
    """Get all positions and active SL alerts"""
    sl = SLEngine()
    
    positions = []
    alerts = []
    
    for pos in sl.get_all_positions():
        # Calculate distance to SL
        distance = pos['current_price'] - pos['stop_loss']
        distance_pct = (distance / pos['current_price']) * 100
        
        # Determine status
        if distance_pct < 5:
            status = 'critical'
            alerts.append({
                "symbol": pos['symbol'],
                "currentPrice": pos['current_price'],
                "stopLoss": pos['stop_loss'],
                "message": f"🚨 CRITICAL: {pos['symbol']} within 5% of SL",
                "timestamp": datetime.now().isoformat()
            })
        elif distance_pct < 10:
            status = 'warning'
            alerts.append({
                "symbol": pos['symbol'],
                "currentPrice": pos['current_price'],
                "stopLoss": pos['stop_loss'],
                "message": f"⚠️ WARNING: {pos['symbol']} in warning zone",
                "timestamp": datetime.now().isoformat()
            })
        else:
            status = 'safe'
        
        positions.append({
            "id": pos['id'],
            "symbol": pos['symbol'],
            "currentPrice": pos['current_price'],
            "stopLoss": pos['stop_loss'],
            "status": status,
            "riskPercent": abs(distance_pct),
            "quantity": pos['quantity']
        })
    
    return {
        "positions": positions,
        "alerts": alerts
    }

@router.post("/update-sl")
async def update_stop_loss(positionId: str, stopLoss: float):
    """Update stop loss for a position"""
    sl = SLEngine()
    success = sl.update_stop_loss(positionId, stopLoss)
    
    return {
        "success": success,
        "message": f"Stop loss updated to {stopLoss}"
    }
```

### Router 5: Charts (`web_api/routers/charts.py`)

**Purpose**: Proxy to existing Market Data API

```python
from fastapi import APIRouter
import httpx

router = APIRouter()

@router.get("/charts/daily")
async def get_daily_chart(symbol: str, from_date: str, to_date: str, theme: str = "light"):
    """Get daily chart from Market Data API"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://ohmstockvault.duckdns.org/api/v1/charts/daily",
            params={
                "symbol": symbol,
                "from_date": from_date,
                "to_date": to_date,
                "theme": theme
            }
        )
        return response.content
```

---

## Phase 2: Frontend Development (2-3 days)

All React components already created in `web-platform/pages/`:
- ✅ Dashboard.jsx
- ✅ ProfitLossTracker.jsx
- ✅ StopLossTracker.jsx
- ✅ Portfolio.jsx
- ✅ App.jsx (main layout)

### Build & Run

```bash
cd web-platform

# Install dependencies
npm install

# Development
npm run dev

# Production build
npm run build
```

---

## Phase 3: Integration & Testing (1-2 days)

### Test Checklist

- [ ] Recommendations endpoint returns valid data
- [ ] Dashboard loads and displays stocks
- [ ] Charts render correctly
- [ ] Buy button sends order to Dhan API
- [ ] Portfolio calculates P&L correctly
- [ ] Stop loss tracker shows correct status zones
- [ ] SL alerts trigger at correct thresholds
- [ ] Real-time price updates work

### API Testing Commands

```bash
# Test recommendations
curl http://localhost:8004/api/recommendations | jq

# Test portfolio
curl http://localhost:8004/api/portfolio?timeframe=1m | jq

# Test SL alerts
curl http://localhost:8004/api/sl-alerts | jq

# Test chart
curl http://localhost:8004/api/charts/daily?symbol=TCS&from_date=2026-03-15&to_date=2026-07-03 > chart.svg
```

---

## Phase 4: Deployment (1 day)

### 1. Build Frontend

```bash
cd web-platform
npm run build
```

### 2. Deploy to VPS

```bash
# Copy to VPS
scp -r dist root@165.232.187.97:/root/web-app/

# Copy backend
scp -r web_api root@165.232.187.97:/root/trade-execution-webhook/
```

### 3. Configure Nginx

Update `/etc/nginx/nginx.conf`:

```nginx
server {
    listen 80;
    server_name ohmstockvault.duckdns.org;

    # Frontend
    location / {
        root /root/web-app/dist;
        try_files $uri /index.html;
    }

    # Backend API
    location /api/ {
        proxy_pass http://localhost:8004;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://localhost:8004;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### 4. Create Systemd Service

`/etc/systemd/system/trade-web-api.service`:

```ini
[Unit]
Description=Trade Web API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/trade-execution-webhook
Environment="PYTHONUNBUFFERED=1"
ExecStart=/root/trade-execution-webhook/venv/bin/python -m uvicorn web_api.main:app --host 0.0.0.0 --port 8004
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 5. Start Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable trade-web-api
sudo systemctl start trade-web-api
sudo systemctl restart nginx
```

---

## Expected Result

### Accessible URLs

- **Frontend**: `https://ohmstockvault.duckdns.org/`
- **API Base**: `https://ohmstockvault.duckdns.org/api/`

### User Experience

1. Visit website → See today's stock recommendations
2. Click stock → View detailed chart with indicators
3. Click "Buy" → Place order instantly via Dhan API
4. Check P&L → Portfolio performance dashboard
5. Monitor SL → Stop loss tracker with risk zones

---

## Database Schema (New Tables)

```sql
-- User trades
CREATE TABLE user_trades (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price DECIMAL(10, 2) NOT NULL,
    entry_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    exit_price DECIMAL(10, 2),
    exit_date TIMESTAMP,
    status VARCHAR(20) DEFAULT 'OPEN',
    pnl DECIMAL(12, 2),
    pnl_percent DECIMAL(6, 2)
);

-- Stop loss positions
CREATE TABLE sl_positions (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price DECIMAL(10, 2) NOT NULL,
    stop_loss DECIMAL(10, 2) NOT NULL,
    current_price DECIMAL(10, 2),
    status VARCHAR(20) DEFAULT 'OPEN',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Portfolio history
CREATE TABLE portfolio_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    portfolio_value DECIMAL(15, 2) NOT NULL,
    invested_value DECIMAL(15, 2) NOT NULL,
    total_pnl DECIMAL(15, 2) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Summary

| Phase | Component | Status | Duration |
|-------|-----------|--------|----------|
| 1 | Backend API (5 routers) | TODO | 2-3 days |
| 2 | Frontend (4 pages) | ✅ DONE | 0 days (ready) |
| 3 | Integration & Testing | TODO | 1-2 days |
| 4 | Deployment | TODO | 1 day |

**Total Time to Production**: ~5-7 days

---

## Next Steps

1. ✅ Design & mockups complete
2. ✅ Frontend components ready
3. [ ] **Implement backend routers**
4. [ ] Set up database tables
5. [ ] Integration testing
6. [ ] Deploy to VPS
7. [ ] Monitor and optimize

