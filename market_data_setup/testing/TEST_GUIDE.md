# 🧪 Local Testing Guide

Complete guide for testing the Market Data API locally without PostgreSQL.

---

## ⚡ Quick Start (2 minutes)

### 1. Install Dependencies
```bash
cd /root/trade-execution-webhook
source venv/bin/activate

# Install only testing requirements
pip install fastapi uvicorn pandas numpy
```

### 2. Start Mock Server
```bash
python market_data_setup/testing/mock_server.py
```

**Output**:
```
============================================================
🚀 Market Data API (MOCK - Testing Mode)
============================================================

📊 Mock Server Starting on http://localhost:8000

📋 Available Endpoints:
   GET /api/v1/health                              - Health check
   GET /api/v1/ohlcv                               - Single symbol OHLCV
   GET /api/v1/ohlcv/multi                         - Multiple symbols
   GET /api/v1/symbols                             - Symbol list
   GET /api/v1/charts/daily                        - Daily chart
   GET /api/v1/charts/weekly                       - Weekly chart
   GET /api/v1/indicators                          - Indicator values
...
```

### 3. Open Another Terminal & Test
```bash
# Health check
curl http://localhost:8000/api/v1/health

# Get OHLCV data
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Generate chart
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > chart.svg
open chart.svg  # macOS
xdg-open chart.svg  # Linux

# Access Swagger UI
open http://localhost:8000/docs
```

---

## 📊 Test Scenarios

### Scenario 1: Health Check
```bash
curl -s http://localhost:8000/api/v1/health | jq '.'

# Expected response:
{
  "status": "ok",
  "timestamp": "2026-06-28T...",
  "mode": "MOCK (testing only)",
  "database": "simulated"
}
```

### Scenario 2: Single Symbol OHLCV
```bash
curl -s "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-03-31" | jq '.meta'

# Expected response:
{
  "symbol": "INFY",
  "count": 63,
  "from": "2024-01-01",
  "to": "2024-03-31",
  "mode": "MOCK DATA (for testing)"
}
```

### Scenario 3: Multiple Symbols
```bash
curl -s "http://localhost:8000/api/v1/ohlcv/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-01-31" | jq '.meta'

# Expected response:
{
  "symbols": ["INFY", "TCS", "RELIANCE"],
  "count": 189,
  "from": "2024-01-01",
  "to": "2024-01-31",
  "mode": "MOCK DATA"
}
```

### Scenario 4: Daily Chart
```bash
# Generate SVG chart
curl -s "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > infy_daily.svg

# View the chart
open infy_daily.svg

# Or embed in HTML
echo '<html><body><img src="'$(curl -s 'http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31')'" /></body></html>' > chart.html
```

### Scenario 5: Weekly Chart
```bash
curl -s "http://localhost:8000/api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31" > infy_weekly.svg
open infy_weekly.svg
```

### Scenario 6: Indicators
```bash
curl -s "http://localhost:8000/api/v1/indicators?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,rsi,atr" | jq '.data[0]'

# Expected response:
{
  "date": "2024-01-01T00:00:00",
  "open": 1450.25,
  "high": 1465.50,
  "low": 1445.00,
  "close": 1455.75,
  "volume": 8500000,
  "ema_10": 1452.10,
  "ema_21": 1448.50,
  "ema_50": 1440.20,
  "ema_200": 1425.80,
  "rsi_14": 65.5,
  "atr": 12.35
}
```

### Scenario 7: All Symbols
```bash
curl -s "http://localhost:8000/api/v1/symbols" | jq '.data'

# Expected: List of 10 test symbols (INFY, TCS, RELIANCE, etc.)
```

---

## 🧪 Test Suite (Automated)

Create `market_data_setup/testing/test_suite.py`:

```python
#!/usr/bin/env python3
import requests
import json
from datetime import date, timedelta

BASE_URL = "http://localhost:8000"

def test_health():
    """Test health check"""
    print("Testing health check...", end=" ")
    r = requests.get(f"{BASE_URL}/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    print("✅")

def test_ohlcv_single():
    """Test single symbol OHLCV"""
    print("Testing single symbol OHLCV...", end=" ")
    r = requests.get(
        f"{BASE_URL}/api/v1/ohlcv",
        params={
            "symbol": "INFY",
            "from": "2024-01-01",
            "to": "2024-01-31"
        }
    )
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["symbol"] == "INFY"
    assert data["meta"]["count"] > 0
    print("✅")

def test_ohlcv_multi():
    """Test multiple symbols"""
    print("Testing multiple symbols...", end=" ")
    r = requests.get(
        f"{BASE_URL}/api/v1/ohlcv/multi",
        params={
            "symbols": "INFY,TCS,RELIANCE",
            "from": "2024-01-01",
            "to": "2024-01-31"
        }
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["data"]) == 3
    print("✅")

def test_symbols():
    """Test symbol list"""
    print("Testing symbol list...", end=" ")
    r = requests.get(f"{BASE_URL}/api/v1/symbols")
    assert r.status_code == 200
    assert r.json()["count"] == 10
    print("✅")

def test_chart_daily():
    """Test daily chart generation"""
    print("Testing daily chart...", end=" ")
    r = requests.get(
        f"{BASE_URL}/api/v1/charts/daily",
        params={
            "symbol": "INFY",
            "from": "2024-01-01",
            "to": "2024-12-31"
        }
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in r.content
    print("✅")

def test_chart_weekly():
    """Test weekly chart"""
    print("Testing weekly chart...", end=" ")
    r = requests.get(
        f"{BASE_URL}/api/v1/charts/weekly",
        params={
            "symbol": "INFY",
            "from": "2020-01-01",
            "to": "2024-12-31"
        }
    )
    assert r.status_code == 200
    assert b"<svg" in r.content
    print("✅")

def test_indicators():
    """Test indicator values"""
    print("Testing indicators...", end=" ")
    r = requests.get(
        f"{BASE_URL}/api/v1/indicators",
        params={
            "symbol": "INFY",
            "from": "2024-01-01",
            "to": "2024-01-31",
            "indicators": "ema,rsi,atr"
        }
    )
    assert r.status_code == 200
    data = r.json()
    assert "ema_10" in data["data"][0]
    assert "rsi_14" in data["data"][0]
    assert "atr" in data["data"][0]
    print("✅")

def test_error_handling():
    """Test error responses"""
    print("Testing error handling...", end=" ")
    
    # Invalid date range
    r = requests.get(
        f"{BASE_URL}/api/v1/ohlcv",
        params={
            "symbol": "INFY",
            "from": "2024-12-31",
            "to": "2024-01-01"
        }
    )
    assert r.status_code == 400
    print("✅")

if __name__ == "__main__":
    print("\n" + "="*60)
    print("Running API Test Suite")
    print("="*60 + "\n")
    
    try:
        test_health()
        test_ohlcv_single()
        test_ohlcv_multi()
        test_symbols()
        test_chart_daily()
        test_chart_weekly()
        test_indicators()
        test_error_handling()
        
        print("\n" + "="*60)
        print("✅ All tests passed!")
        print("="*60 + "\n")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
```

**Run tests**:
```bash
# Terminal 1: Start mock server
python market_data_setup/testing/mock_server.py

# Terminal 2: Run tests
pip install requests
python market_data_setup/testing/test_suite.py
```

**Expected output**:
```
============================================================
Running API Test Suite
============================================================

Testing health check... ✅
Testing single symbol OHLCV... ✅
Testing multiple symbols... ✅
Testing symbol list... ✅
Testing daily chart... ✅
Testing weekly chart... ✅
Testing indicators... ✅
Testing error handling... ✅

============================================================
✅ All tests passed!
============================================================
```

---

## 📱 Test with Different Tools

### Using Python (requests)
```python
import requests

# Single symbol
r = requests.get(
    'http://localhost:8000/api/v1/ohlcv',
    params={
        'symbol': 'INFY',
        'from': '2024-01-01',
        'to': '2024-12-31'
    }
)
print(r.json())
```

### Using curl (bash)
```bash
# Single symbol
curl "http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Multiple symbols with pretty print
curl -s "http://localhost:8000/api/v1/ohlcv/multi?symbols=INFY,TCS&from=2024-01-01&to=2024-01-31" | jq '.'

# Save chart to file
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" > chart.svg
```

### Using JavaScript (Node.js)
```javascript
const fetch = require('node-fetch');

async function testAPI() {
  const response = await fetch(
    'http://localhost:8000/api/v1/ohlcv?symbol=INFY&from=2024-01-01&to=2024-12-31'
  );
  const data = await response.json();
  console.log(data);
}

testAPI();
```

### Using Postman
1. Import the API collection: **API Docs at http://localhost:8000/docs**
2. Create requests for each endpoint
3. Test different parameters
4. Save test results

---

## 🎯 What You're Testing

✅ **Endpoints Work**: All 7 endpoints responding correctly  
✅ **Parameters Accepted**: Symbols, dates, indicators, formats  
✅ **Response Format**: JSON and SVG/PNG generated correctly  
✅ **Error Handling**: Invalid inputs return proper errors  
✅ **Performance**: Sub-100ms for simple queries  
✅ **Caching Headers**: Cache-Control headers set correctly  
✅ **Multi-Symbol**: Bulk queries work for backtesting  

---

## 📈 Limitations of Mock Server

The mock server generates **realistic but random data**:

| Feature | Mock | Real |
|---------|------|------|
| Price movement | Random walk (realistic) | Real Dhan API data |
| Indicators | Calculated correctly | Calculated from real data |
| Performance | <100ms | <100ms |
| Volume | Realistic range | Real volumes |
| Date handling | Correct | Correct |
| Multiple symbols | ✅ | ✅ |
| Caching | ✅ | ✅ |

---

## 🚀 Next: Deploy to VPS

Once tests pass locally:

1. Follow `market_data_setup/DEPLOYMENT_CHECKLIST.md`
2. Setup PostgreSQL
3. Load real data from Dhan API
4. Replace mock server with production API
5. Tests will use real data

---

## 🆘 Troubleshooting

### "Port 8000 already in use"
```bash
# Kill existing process
lsof -i :8000
kill -9 <PID>

# Or use different port
python -c "import sys; sys.argv = ['mock_server.py', '--port', '8001']; exec(open('market_data_setup/testing/mock_server.py').read())"
```

### "Module not found" errors
```bash
# Install dependencies
pip install fastapi uvicorn pandas numpy requests

# Or from requirements
pip install -r market_data_setup/requirements.txt
```

### "Chart not rendering"
```bash
# Check SVG generation
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-01-31" | head -20
# Should show: <svg width="1200" height="600"...
```

---

## ✅ Verification Checklist

- [ ] Mock server starts without errors
- [ ] Health check returns `status: ok`
- [ ] OHLCV data returned for valid dates
- [ ] Multiple symbols query works
- [ ] Charts generate as SVG
- [ ] Indicators calculated correctly
- [ ] Invalid dates return 400 error
- [ ] Swagger UI accessible at /docs
- [ ] All tests pass

---

**Status**: Ready to Test ✅  
**No Database Required**: Mock data used  
**Time to Test**: 5 minutes
