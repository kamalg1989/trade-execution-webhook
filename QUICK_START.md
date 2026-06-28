# Market Data API MCP - Quick Start & Reference Guide

## 🚀 30-Second Quickstart

```bash
# 1. Test connectivity
curl http://165.232.187.97:8002/

# 2. Get health status
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_health" \
  -H "Content-Type: application/json" -d '{}'

# 3. Get TCS daily chart
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_daily_chart" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"TCS",
    "from_date":"2024-06-01",
    "to_date":"2024-12-31",
    "indicators":"ema",
    "theme":"light"
  }' > tcs_chart.svg
```

---

## 📍 Global Access URLs

| Purpose | URL |
|---------|-----|
| **MCP Server** | `http://165.232.187.97:8002/` |
| **API Documentation** | `http://165.232.187.97/api/v1/docs` |
| **API Base** | `http://165.232.187.97/api/v1/` |
| **Full MCP Guide** | GitHub: MCP_INTEGRATION_GUIDE.md |

---

## 🛠️ 7 Available Tools - At a Glance

| # | Tool | Purpose | Input | Output | Example |
|---|------|---------|-------|--------|---------|
| 1 | `get_health` | API status | None | JSON status | `{"status":"ok","database":"connected"}` |
| 2 | `get_symbols` | Stock list | `sector?` | JSON array | `[{symbol,name,sector}]` |
| 3 | `get_ohlcv` | Single stock data | symbol, dates | JSON array | OHLCV candles |
| 4 | `get_multi_ohlcv` | Multiple stocks | symbols, dates | JSON object | `{symbol: [candles]}` |
| 5 | `get_daily_chart` | Daily SVG chart | symbol, dates, indicators, theme | SVG image | Chart with EMA/RSI/ATR/MACD |
| 6 | `get_weekly_chart` | Weekly SVG chart | symbol, dates, indicators, theme | SVG image | Weekly chart SVG |
| 7 | `get_combined_chart` | Daily+Weekly SVG | symbol, dates, indicators, theme | SVG image | Stacked daily+weekly charts |

---

## 💬 5 Common Use Cases

### **Use Case 1: Get Stock Data for Analysis**
```bash
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_ohlcv" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "INFY",
    "from_date": "2024-01-01",
    "to_date": "2024-12-31"
  }'
```
**Result**: 251 trading day OHLCV records for backtesting

---

### **Use Case 2: Generate Trading Chart**
```bash
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_daily_chart" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "RELIANCE",
    "from_date": "2024-01-01",
    "to_date": "2024-12-31",
    "indicators": "macd",
    "theme": "dark"
  }' > reliance_macd.svg
```
**Result**: Professional SVG chart with MACD indicator

---

### **Use Case 3: Compare Multiple Stocks**
```bash
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_multi_ohlcv" \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": "TCS,INFY,WIPRO",
    "from_date": "2024-06-01",
    "to_date": "2024-12-31"
  }'
```
**Result**: OHLCV data for all 3 stocks in one call

---

### **Use Case 4: Multi-Timeframe Analysis (Claude)**
```
Ask Claude: "Generate combined daily+weekly charts for TCS from June-December 2024 
with EMA indicators. What's the long-term trend?"
```
**Result**: Claude calls get_combined_chart, displays both charts, provides analysis

---

### **Use Case 5: Sector Screening**
```bash
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_symbols" \
  -H "Content-Type: application/json" \
  -d '{"sector": "IT"}'
```
**Result**: List of all IT sector stocks (TCS, INFY, WIPRO, etc.)

---

## 📊 Parameter Quick Reference

### **Chart Parameters**
```json
{
  "symbol": "TCS",                    // Required: NSE stock symbol
  "from_date": "2024-06-01",          // Required: YYYY-MM-DD
  "to_date": "2024-12-31",            // Required: YYYY-MM-DD
  "indicators": "ema",                // Optional: ema|rsi|atr|macd|all|none (default: ema)
  "theme": "light"                    // Optional: light|dark (default: light)
}
```

### **Indicator Meanings**
| Indicator | Default Period | What it Shows |
|-----------|-----------------|---------------|
| **EMA** | 9/21 | Trend direction (fast & slow moving averages) |
| **RSI** | 14 | Momentum (overbought >70, oversold <30) |
| **ATR** | 14 | Volatility (high volatility = wider moves) |
| **MACD** | 12/26/9 | Momentum crossovers and divergences |

---

## 🎯 Ask Claude (Copy & Paste Prompts)

### **Prompt 1: Daily Analysis**
```
"Show me the daily chart for TCS from June 1 to December 31, 2024.
Include EMA indicators. What's the trend and what are the entry/exit points?"
```

### **Prompt 2: Sector Research**
```
"List all stocks in the FINANCE sector. Get 6-month OHLCV data for the top 3.
Which one has the best momentum? Support with charts."
```

### **Prompt 3: Multi-Timeframe**
```
"Generate a combined daily+weekly chart for RELIANCE covering all of 2024
with MACD indicators. Provide a trading outlook based on the charts."
```

### **Prompt 4: Backtesting**
```
"Get 5 years of OHLCV data for INFY (2019-2024) with light theme daily chart.
I need this for backtesting a moving average crossover strategy."
```

### **Prompt 5: Portfolio Review**
```
"Show me dark-themed daily charts for TCS, INFY, RELIANCE for the last 3 months.
Identify which are in uptrends and which are in downtrends."
```

---

## 🔧 Integration Examples

### **Python - Get Data**
```python
import httpx

mcp = "http://165.232.187.97:8002/call"

response = httpx.post(
    f"{mcp}?tool_name=get_ohlcv",
    json={
        "symbol": "INFY",
        "from_date": "2024-01-01",
        "to_date": "2024-12-31"
    }
)

data = response.json()["data"]
for candle in data[:5]:
    print(f"{candle['date']}: O={candle['open']:.2f} C={candle['close']:.2f}")
```

### **Python - Generate Chart**
```python
import httpx

response = httpx.post(
    "http://165.232.187.97:8002/call?tool_name=get_daily_chart",
    json={
        "symbol": "TCS",
        "from_date": "2024-06-01",
        "to_date": "2024-12-31",
        "theme": "light"
    }
)

svg = response.json()["data"]["data"]
with open("chart.svg", "w") as f:
    f.write(svg)
```

### **JavaScript - Fetch Multiple**
```javascript
const mcp = "http://165.232.187.97:8002/call";

const response = await fetch(`${mcp}?tool_name=get_multi_ohlcv`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
        symbols: "TCS,INFY,RELIANCE",
        from_date: "2024-01-01",
        to_date: "2024-12-31"
    })
});

const { data } = await response.json();
console.log(`TCS: ${data.TCS.length} candles`);
console.log(`INFY: ${data.INFY.length} candles`);
```

---

## 📖 Where to Get More Info

### **Complete Documentation**
1. **Swagger UI**: `http://165.232.187.97/api/v1/docs`
   - Interactive tool testing
   - Full parameter documentation
   - Response schema examples
   - Try-it-out functionality

2. **GitHub**: `https://github.com/kamalg1989/trade-execution-webhook`
   - Source code
   - MCP_INTEGRATION_GUIDE.md (comprehensive guide)
   - Implementation details

3. **This Repository**
   - `QUICK_START.md` (this file)
   - `MCP_INTEGRATION_GUIDE.md` (detailed reference)
   - Source code in `market_data_setup/mcp/`

---

## ✅ What's Included

### **Data**
- ✅ 2,953 NSE equity stocks
- ✅ 15+ years of daily OHLCV (2011-2026)
- ✅ ~10.8 million candles total
- ✅ Auto-updates daily at 18:00 IST

### **Features**
- ✅ Technical indicators (EMA, RSI, ATR, MACD)
- ✅ SVG charts with light/dark themes
- ✅ Batch multi-symbol queries
- ✅ High performance (<100ms queries)

### **Availability**
- ✅ Global access via public IP
- ✅ 24/7 uptime
- ✅ Firewall configured (port 8002 open)
- ✅ Production-ready

---

## 🎓 Learning Path

1. **Start here**: Run the 30-second quickstart above ✅
2. **Explore**: Visit Swagger docs for interactive testing
3. **Read**: Full guide in MCP_INTEGRATION_GUIDE.md
4. **Build**: Use Python/JavaScript examples above
5. **Scale**: Integrate with Claude for AI analysis

---

## 📞 Support

- **GitHub Issues**: Report bugs in the repository
- **VPS Status**: 165.232.187.97 (Bangalore, India)
- **Service Status**: Check `/call?tool_name=get_health`
- **Logs**: SSH to VPS and run: `journalctl -u market-data-api -n 50`

---

## 🟢 Status

| Component | Status | Details |
|-----------|--------|---------|
| **MCP Server** | ✅ Running | Port 8002, 24/7 |
| **API Server** | ✅ Running | Port 8001 |
| **Database** | ✅ Connected | TimescaleDB 2.28.1 |
| **Auto-Updates** | ✅ Active | Daily 18:00 IST |
| **Firewall** | ✅ Configured | Port 8002 open |

---

**Last Updated**: June 29, 2026  
**Version**: 1.0.0  
**Status**: Production Ready ✅
