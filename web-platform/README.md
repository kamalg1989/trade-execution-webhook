# Trading Platform - Web UI

Complete web-based trading platform replacing Telegram webhook dependency.

## 📁 Project Structure

```
web-platform/
├── pages/
│   ├── Dashboard.jsx           # Daily recommendations with charts & buy button
│   ├── ProfitLossTracker.jsx   # Portfolio performance analytics
│   ├── StopLossTracker.jsx     # Risk management with SL engine
│   └── Portfolio.jsx           # Holdings & trade history
├── App.jsx                     # Main app component with navigation
├── DESIGN.md                   # Complete architecture & design document
├── README.md                   # This file
├── package.json                # Dependencies
└── vite.config.js              # Vite configuration
```

## 🎯 Features

### 1. Dashboard
- **Daily Recommendations**: AI-screened stocks from `screen_gpt`
- **Stock Selection**: Click to view detailed charts
- **Charts**: Interactive daily candlestick charts with EMA/RSI/MACD
- **Quick Buy**: One-click order placement with Dhan API
- **Live Updates**: Real-time price updates every 1-2 seconds
- **Key Metrics**: Target price, stop loss, confidence, upside potential

### 2. Profit & Loss Tracker
- **Summary Cards**: Total invested, current value, unrealized/realized P&L
- **Performance Charts**: Line chart showing portfolio value over time
- **Portfolio Distribution**: Pie chart of holdings breakdown
- **Gainers/Losers**: Separated tables with color-coded returns
- **Timeframe Filters**: 1W, 1M, 3M, 1Y views
- **Privacy Toggle**: Show/hide balance

### 3. Stop Loss Tracker
- **Position Table**: All open positions with current SL and status
- **Risk Zones**: 
  - 🟢 Safe (Green): >10% above SL
  - 🟡 Warning (Yellow): 5-10% above SL
  - 🔴 Critical (Red): <5% above SL
- **Edit SL**: Inline editing for stop loss levels
- **Close Position**: Remove positions from portfolio
- **Alerts Panel**: Active warnings and critical alerts
- **SL Engine Logic**: Visual explanation of risk zones

### 4. Portfolio
- **Holdings Table**: Current positions with entry prices and returns
- **Closed Trades**: Historical trades with P&L and duration
- **CSV Export**: Download portfolio data
- **Tab Navigation**: Switch between holdings and closed trades

## 🔧 Setup Instructions

### Prerequisites
- Node.js 18+ and npm
- Existing backend APIs running

### Installation

```bash
# Navigate to web-platform directory
cd web-platform

# Install dependencies
npm install

# Create environment file
cp .env.example .env.local
```

### Environment Variables (`.env.local`)
```
VITE_API_BASE_URL=http://localhost:8004
VITE_WS_BASE_URL=ws://localhost:8004
```

### Development Server

```bash
# Start dev server (runs on http://localhost:5173)
npm run dev
```

### Production Build

```bash
# Build optimized bundle
npm run build

# Preview production build locally
npm run preview
```

## 📡 API Integration

The frontend expects these endpoints from the backend:

### GET `/api/recommendations`
```json
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
      "reason": "Bullish breakout above 20-day EMA",
      "recommendedQty": 1
    }
  ]
}
```

### POST `/api/buy`
```json
{
  "symbol": "TCS",
  "quantity": 1,
  "price": 3850.50,
  "stopLoss": 3700
}
```
Response:
```json
{
  "success": true,
  "orderId": "ORD20260703001",
  "message": "Order placed successfully"
}
```

### GET `/api/portfolio?timeframe=1m`
```json
{
  "totalInvested": 500000,
  "totalValue": 543250,
  "unrealizedPnL": 43250,
  "realizedPnL": 12500,
  "positions": [...],
  "performanceHistory": [...]
}
```

### GET `/api/sl-alerts`
```json
{
  "positions": [
    {
      "id": "POS001",
      "symbol": "TCS",
      "currentPrice": 3850,
      "stopLoss": 3700,
      "status": "safe",
      "riskPercent": 3.9
    }
  ],
  "alerts": [...]
}
```

### GET `/api/charts/daily?symbol=TCS&from_date=2026-03-15&to_date=2026-07-03&theme=light`
Returns: SVG chart with indicators

### GET `/api/portfolio/full`
```json
{
  "holdings": [...],
  "closedTrades": [...]
}
```

## 🏗️ Backend Implementation

Create a new FastAPI server (Port 8004) with these endpoints:

### Sample Backend Code Structure
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routers
from routers import recommendations, orders, portfolio, sl_engine, charts

app.include_router(recommendations.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(sl_engine.router, prefix="/api")
app.include_router(charts.router, prefix="/api")

# Static files
app.mount("/", StaticFiles(directory="dist", html=True), name="static")
```

### Key Integration Points
1. **screen_gpt.py**: Call for daily recommendations
2. **entry_engine.py (SL Engine)**: Risk calculations for SL tracking
3. **Dhan API**: Order placement and portfolio queries
4. **Market Data API**: Chart SVG generation
5. **TimescaleDB**: Trade history and P&L calculations

## 🚀 Deployment

### On VPS (165.232.187.97)

1. **Build frontend**:
```bash
npm run build
```

2. **Copy to VPS**:
```bash
scp -r dist root@165.232.187.97:/root/web-app/
```

3. **Update Nginx**:
```nginx
server {
    listen 80;
    server_name ohmstockvault.duckdns.org;

    # Static files
    location / {
        root /root/web-app/dist;
        try_files $uri /index.html;
    }

    # API proxy
    location /api/ {
        proxy_pass http://localhost:8004;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
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

4. **Systemd Service** (`/etc/systemd/system/trade-web-api.service`):
```ini
[Unit]
Description=Trade Web API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/trade-execution-webhook
ExecStart=/root/trade-execution-webhook/venv/bin/python -m uvicorn web_api.main:app --host 0.0.0.0 --port 8004
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 📊 Technology Stack

- **Frontend**: React 18, Vite, Tailwind CSS, Recharts
- **Icons**: Lucide React
- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL/TimescaleDB
- **Deployment**: Nginx, Systemd
- **Domain**: Duck DNS + Let's Encrypt

## 🔐 Security Checklist

- [ ] User authentication (JWT)
- [ ] Rate limiting on API endpoints
- [ ] HTTPS enforcement
- [ ] Input validation
- [ ] Order confirmation dialogs
- [ ] Audit logging for trades
- [ ] Session management
- [ ] CORS configuration

## 📈 Future Enhancements

- [ ] Mobile responsive design
- [ ] Dark/Light theme toggle
- [ ] WebSocket for real-time updates
- [ ] Advanced charting (TradingView)
- [ ] Multiple user accounts
- [ ] Portfolio comparison tools
- [ ] Email/SMS alerts
- [ ] Mobile app (React Native)
- [ ] Backtesting features
- [ ] Advanced order types (OCO, Bracket)

## 🐛 Development Tips

### Live API Testing
```bash
# Test recommendations endpoint
curl http://localhost:8004/api/recommendations

# Test portfolio endpoint
curl http://localhost:8004/api/portfolio?timeframe=1m

# Test buy order (requires auth)
curl -X POST http://localhost:8004/api/buy \
  -H "Content-Type: application/json" \
  -d '{"symbol":"TCS","quantity":1,"price":3850.50,"stopLoss":3700}'
```

### Debugging
- Use React Developer Tools browser extension
- Check browser console for API errors
- Monitor network tab for API calls
- Use Postman for API endpoint testing

## 📝 Notes

- All charts are fetched as SVG from existing Market Data API
- Stock recommendations sourced from `screen_gpt.py` daily at 18:00 IST
- Stop loss calculations use logic from `entry_engine.py`
- Real-time prices from Dhan API
- Historical data from TimescaleDB

## 🤝 Support

For issues or questions about:
- **Frontend**: Check React component props and API response shapes
- **Backend**: Verify FastAPI routes and database queries
- **Deployment**: Review Nginx config and systemd service logs

## 📄 License

Internal Use Only

---

**Status**: Design phase complete, ready for backend implementation
