# Stock Trading Platform - Design & Architecture

## Overview
Replace Telegram webhook dependency with a complete web-based trading platform featuring real-time recommendations, portfolio tracking, and intelligent risk management.

---

## Platform Structure

### Frontend (React SPA)
- **Framework**: React 18 + Vite
- **Styling**: Tailwind CSS
- **Charts**: Recharts for data visualization
- **State Management**: React hooks (useState, useEffect, Context API)
- **Deployment**: Vercel / S3 + CloudFront

**Pages:**
1. **Dashboard** - Daily recommendations with charts and buy buttons
2. **Profit/Loss Tracker** - Portfolio performance analytics
3. **Stop Loss Tracker** - Risk management with SL engine logic
4. **Portfolio** - Holdings, history, trades

---

## Backend Architecture

### API Endpoints (FastAPI)

#### 1. Recommendations Engine
```
GET /api/recommendations
Response:
{
  "stocks": [
    {
      "symbol": "TCS",
      "company": "Tata Consultancy Services",
      "currentPrice": 3850.50,
      "change": 2.5,
      "target": 4200,
      "stopLoss": 3700,
      "confidence": 85,
      "reason": "Bullish breakout above 20-day EMA with strong volume",
      "recommendedQty": 1
    }
  ],
  "generatedAt": "2026-07-03T18:00:00Z"
}
```
**Source**: Calls `screen_gpt.py` to generate daily stock screening

---

#### 2. Buy Order Placement
```
POST /api/buy
{
  "symbol": "TCS",
  "quantity": 1,
  "price": 3850.50,
  "stopLoss": 3700
}
Response:
{
  "success": true,
  "orderId": "ORD20260703001",
  "message": "Order placed successfully"
}
```
**Integration**: Dhan API v2 for actual order placement
**Stop Loss**: Automatically set in SL Engine

---

#### 3. Charts API (from existing Market Data API)
```
GET /api/charts/daily?symbol=TCS&from_date=2026-03-15&to_date=2026-07-03&theme=light
Returns: SVG chart with OHLCV + EMA/RSI/MACD indicators
```

---

#### 4. Portfolio P&L
```
GET /api/portfolio?timeframe=1m
Response:
{
  "totalInvested": 500000,
  "totalValue": 543250,
  "unrealizedPnL": 43250,
  "realizedPnL": 12500,
  "positions": [
    {
      "symbol": "TCS",
      "quantity": 5,
      "avgCost": 3800,
      "currentPrice": 3850,
      "value": 19250,
      "pnl": 250,
      "pnlPercent": 1.32
    }
  ],
  "performanceHistory": [
    {"date": "2026-07-03", "value": 543250, "pnl": 43250}
  ]
}
```
**Source**: TimescaleDB trades table + real-time Dhan API prices

---

#### 5. Stop Loss Tracker & SL Engine
```
GET /api/sl-alerts
Response:
{
  "positions": [
    {
      "id": "POS001",
      "symbol": "TCS",
      "currentPrice": 3850,
      "stopLoss": 3700,
      "status": "safe",  // safe | warning | critical
      "riskPercent": 3.9,
      "quantity": 5
    }
  ],
  "alerts": [
    {
      "symbol": "INFY",
      "currentPrice": 2950,
      "stopLoss": 2900,
      "message": "⚠️ WARNING: Price within 5% of stop loss",
      "timestamp": "2026-07-03T16:45:00Z"
    }
  ]
}
```

**SL Engine Logic:**
- **Safe Zone (Green)**: Price > SL + 10% → Normal monitoring
- **Warning Zone (Yellow)**: SL + 5% < Price ≤ SL + 10% → Alert notifications
- **Critical Zone (Red)**: Price ≤ SL + 5% → Could trigger auto-exit or immediate notification

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend (React)                         │
│  Dashboard | P&L | Stop Loss | Portfolio                    │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Backend (Port 8004)                │
│  /api/recommendations  /api/buy  /api/portfolio /api/sl-alerts│
└─┬────────────┬─────────────────┬──────────────────┬─────────┘
  │            │                 │                  │
  ▼            ▼                 ▼                  ▼
screen_gpt   Dhan API      Market Data API      SL Engine
engine       (Orders)      (Charts & Prices)    (Risk Logic)
  │            │                 │                  │
  └────────────┴─────────────────┴──────────────────┘
                       │
                       ▼
            ┌──────────────────────────┐
            │   TimescaleDB            │
            │ - OHLCV Data             │
            │ - Trades & Positions     │
            │ - Historical P&L         │
            └──────────────────────────┘
```

---

## Key Features by Page

### 1. Dashboard
- [ ] Display top 5-10 daily recommendations from screen_gpt
- [ ] Show stock details: price, change%, target, SL, confidence
- [ ] Render interactive daily chart with indicators
- [ ] Buy button → order placement via Dhan API
- [ ] Live price updates every 1-2 seconds

### 2. Profit/Loss Tracker
- [ ] Summary cards: Total Invested, Current Value, Unrealized P&L, Realized P&L
- [ ] Performance chart over time (line chart)
- [ ] Portfolio distribution pie chart
- [ ] Gainers vs Losers tables
- [ ] Filter by timeframe: 1W, 1M, 3M, 1Y
- [ ] Show/hide balance for privacy

### 3. Stop Loss Tracker
- [ ] Table of all open positions with current SL
- [ ] Color-coded status: Safe (green) | Warning (yellow) | Critical (red)
- [ ] Risk % indicator bar
- [ ] Distance to SL in rupees and percentage
- [ ] Edit stop loss inline
- [ ] Close position button
- [ ] Active alerts notification panel
- [ ] SL Engine logic explanation

### 4. Portfolio (Additional)
- [ ] All holdings with purchase history
- [ ] Closed trades / P&L history
- [ ] Trade notes and entry/exit reasons
- [ ] Export portfolio as CSV

---

## Integration Points

### Existing Systems to Connect
1. **screen_gpt.py**: Stock screening and recommendation engine
2. **SL Engine** (entry_engine.py): Stop loss calculation logic
3. **Market Data API**: Charts and price data
4. **Dhan API**: Live order placement and portfolio data
5. **TimescaleDB**: Trade history and historical data

### New Components Needed
1. **FastAPI Backend** (Port 8004): Central API server
2. **React Frontend**: SPA deployed to web
3. **WebSocket Connection**: Real-time price updates
4. **Database Migrations**: Add new tables for trades, positions, P&L tracking

---

## Deployment Strategy

### Backend
- **Server**: Same VPS (165.232.187.97)
- **Port**: 8004 (FastAPI)
- **Systemd Service**: `trade-web-api.service`
- **Nginx**: Route `/api/` to FastAPI, static files to frontend

### Frontend
- **Build**: `npm run build` → production bundle
- **Hosting Options**:
  - Option A: Serve from same VPS (static files via Nginx)
  - Option B: Deploy to Vercel/Netlify (recommended for auto-scaling)
- **Domain**: `ohmstockvault.duckdns.org/app` or new subdomain `app.ohmstockvault.duckdns.org`

### Database
- Existing TimescaleDB (no changes needed)
- Add new tables: `user_trades`, `user_positions`, `sl_alerts`

---

## Security Considerations
- [ ] User authentication (JWT tokens)
- [ ] API rate limiting
- [ ] HTTPS/SSL (already set up)
- [ ] Input validation
- [ ] Order confirmation dialog
- [ ] Audit logging for trades

---

## Implementation Timeline
- **Phase 1**: Backend API endpoints (2-3 days)
- **Phase 2**: Frontend pages & styling (2-3 days)
- **Phase 3**: Integration & testing (1-2 days)
- **Phase 4**: Deployment & optimization (1 day)

**Total**: ~1 week for complete implementation

---

## Next Steps
1. ✅ Review design and mockups
2. [ ] Confirm API endpoint specifications
3. [ ] Set up project structure
4. [ ] Create database schema for trades/positions
5. [ ] Implement backend endpoints
6. [ ] Build frontend React components
7. [ ] Integration testing
8. [ ] Deploy to production

