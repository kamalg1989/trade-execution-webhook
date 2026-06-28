# 📊 Charting API Specification

Complete API documentation for daily/weekly chart generation with technical indicators.

**Response Format**: SVG or PNG image files  
**Indicators**: EMA, RSI, ATR, MACD, Bollinger Bands  
**Input Format**: Consistent with Data API (symbol/list, from/to dates)

---

## 🎯 Overview

The Charting API generates candlestick charts with technical indicators for single or multiple symbols.

### Key Features
- ✅ Single symbol charts
- ✅ Multiple symbol charts (one chart per symbol)
- ✅ Daily timeframe (daily candles)
- ✅ Weekly timeframe (aggregated from daily)
- ✅ SVG format (lightweight, embeddable)
- ✅ PNG format (image files)
- ✅ Multiple indicators overlaid
- ✅ Caching enabled (1 hour for daily, 24h for weekly)

---

## 📡 Endpoints

### 1. Daily Chart - Single Symbol

**Endpoint**: `GET /api/v1/charts/daily`

**Parameters**:
```
symbol      (required)  : NSE symbol (e.g., INFY, TCS)
from        (required)  : Start date (YYYY-MM-DD)
to          (required)  : End date (YYYY-MM-DD)
indicators  (optional)  : Comma-separated indicators (ema, rsi, atr, macd, bb, all, none)
                          Default: ema
format      (optional)  : svg | png (Default: svg)
```

**Example Requests**:

```bash
# Basic: SVG chart with EMA
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31"

# PNG format
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&format=png" > chart.png

# Multiple indicators
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=ema,rsi,macd" > chart.svg

# All indicators
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=all" > chart.svg

# No indicators (candles only)
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31&indicators=none" > chart.svg
```

**Response - SVG**:
```
Content-Type: image/svg+xml
Body: SVG image (embeddable in HTML)

Example:
<svg width="1200" height="600" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#1a1a1a"/>
  <text x="10" y="25" font-size="16" fill="#fff">INFY - Daily</text>
  <!-- Candlesticks, EMA lines, etc. -->
</svg>
```

**Response - PNG**:
```
Content-Type: image/png
Body: Binary PNG file
```

**HTTP Headers**:
```
Cache-Control: public, max-age=3600  (1 hour cache)
X-Cache-Status: HIT|MISS|BYPASS
Content-Type: image/svg+xml | image/png
```

---

### 2. Daily Chart - Multiple Symbols

**Endpoint**: `GET /api/v1/charts/daily/multi`

**Parameters**:
```
symbols     (required)  : Comma-separated symbols (INFY,TCS,RELIANCE)
             OR Array in query: symbols=INFY&symbols=TCS&symbols=RELIANCE
from        (required)  : Start date (YYYY-MM-DD)
to          (required)  : End date (YYYY-MM-DD)
indicators  (optional)  : Comma-separated indicators (Default: ema)
format      (optional)  : svg | png (Default: svg)
max_symbols (optional)  : Limit charts to N symbols (Default: 10)
```

**Example Requests**:

```bash
# Get charts for 3 symbols (comma-separated)
curl "http://localhost:8000/api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31"

# Array format
curl "http://localhost:8000/api/v1/charts/daily/multi?symbols=INFY&symbols=TCS&symbols=RELIANCE&from=2024-01-01&to=2024-12-31"

# Get PNG files for multiple symbols
curl "http://localhost:8000/api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE,HDFCBANK&from=2024-01-01&to=2024-12-31&format=png"

# With all indicators
curl "http://localhost:8000/api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31&indicators=all"

# Download as batch ZIP (optional)
curl "http://localhost:8000/api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&to=2024-12-31&format=png&batch=zip" > charts.zip
```

**Response - Multiple SVG**:
```
Content-Type: multipart/mixed
Body: Multiple SVG images, one for each symbol

Example:
--boundary
Content-Type: image/svg+xml
Content-Disposition: inline; filename="INFY_daily.svg"

<svg>...</svg>
--boundary
Content-Type: image/svg+xml
Content-Disposition: inline; filename="TCS_daily.svg"

<svg>...</svg>
--boundary
```

**Response - Multiple PNG as ZIP**:
```
Content-Type: application/zip
Body: Compressed file containing PNG images

Files in archive:
  INFY_daily.png
  TCS_daily.png
  RELIANCE_daily.png
  manifest.json (metadata)
```

---

### 3. Weekly Chart - Single Symbol

**Endpoint**: `GET /api/v1/charts/weekly`

**Parameters**:
```
symbol      (required)  : NSE symbol
from        (required)  : Start date (YYYY-MM-DD)
to          (required)  : End date (YYYY-MM-DD)
indicators  (optional)  : Comma-separated indicators (Default: ema)
format      (optional)  : svg | png (Default: svg)
```

**Example Requests**:

```bash
# 5-year weekly chart
curl "http://localhost:8000/api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31&indicators=ema" > infy_weekly.svg

# PNG format
curl "http://localhost:8000/api/v1/charts/weekly?symbol=INFY&from=2020-01-01&to=2024-12-31&indicators=ema,macd&format=png" > infy_weekly.png

# All indicators
curl "http://localhost:8000/api/v1/charts/weekly?symbol=INFY&from=2010-01-01&to=2024-12-31&indicators=all" > infy_15year.svg
```

**Response**: Same as daily (SVG or PNG image)

**HTTP Headers**:
```
Cache-Control: public, max-age=86400  (24 hour cache)
```

---

### 4. Weekly Chart - Multiple Symbols

**Endpoint**: `GET /api/v1/charts/weekly/multi`

**Parameters**:
```
symbols     (required)  : Comma-separated symbols
from        (required)  : Start date
to          (required)  : End date
indicators  (optional)  : Comma-separated indicators (Default: ema)
format      (optional)  : svg | png (Default: svg)
batch       (optional)  : zip | tar (for multiple files)
```

**Example Requests**:

```bash
# Multiple weekly charts
curl "http://localhost:8000/api/v1/charts/weekly/multi?symbols=INFY,TCS,RELIANCE&from=2020-01-01&to=2024-12-31"

# As ZIP
curl "http://localhost:8000/api/v1/charts/weekly/multi?symbols=INFY,TCS,RELIANCE,HDFCBANK,ICICIBANK&from=2020-01-01&to=2024-12-31&format=png&batch=zip" > weekly_charts.zip
```

---

## 📊 Indicator Specifications

### Available Indicators

| Indicator | Code | Description | Default | Panel |
|-----------|------|-------------|---------|-------|
| **EMA 10** | ema | Exponential Moving Avg (10) | Yes | Main |
| **EMA 21** | ema | Exponential Moving Avg (21) | Yes | Main |
| **EMA 50** | ema | Exponential Moving Avg (50) | Yes | Main |
| **EMA 200** | ema | Exponential Moving Avg (200) | Yes | Main |
| **RSI 14** | rsi | Relative Strength Index | No | Separate |
| **ATR 14** | atr | Average True Range | No | Separate |
| **MACD** | macd | MACD + Signal + Histogram | No | Separate |
| **BB 20** | bb | Bollinger Bands (20, 2) | No | Main |

### Color Scheme

```
Candlesticks:
  Up (Green):  #00ff00
  Down (Red):  #ff0000

EMA Lines:
  EMA 10:  #0066ff (Blue)
  EMA 21:  #00ff00 (Green)
  EMA 50:  #ffaa00 (Orange)
  EMA 200: #ff0000 (Red)

Indicators:
  RSI:      #9966ff (Purple)
  MACD:     #00ccff (Cyan)
  Bollinger: #ffcc00 (Yellow)

Grid:
  Lines:    #333333 (Dark gray)
```

### Indicator Combinations

```bash
# Valid combinations:
ema             # EMA 10, 21, 50, 200 only
rsi             # RSI 14 only
atr             # ATR 14 only
macd            # MACD + Signal + Histogram
bb              # Bollinger Bands
ema,rsi         # EMAs + RSI (two panels)
ema,rsi,macd    # EMAs + RSI + MACD (three panels)
all             # All available indicators
none            # Candles only (no indicators)
```

---

## 💻 Response Formats

### SVG Format (Lightweight)

**Advantages**:
- ✅ Embeddable in HTML
- ✅ Scalable (no pixelation)
- ✅ Small file size (~100-300 KB)
- ✅ Fast generation (<200ms)
- ✅ Cacheable

**Example HTML Usage**:
```html
<!-- Embed directly -->
<img src="http://api.example.com/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" 
     alt="INFY Daily Chart" 
     style="max-width: 100%; height: auto;">

<!-- Or in an iframe -->
<iframe src="http://api.example.com/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" 
        style="width: 1200px; height: 600px; border: none;"></iframe>

<!-- Or download -->
<a href="http://api.example.com/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31" 
   download="INFY_daily.svg">Download Chart</a>
```

### PNG Format (Traditional Images)

**Advantages**:
- ✅ Compatible everywhere
- ✅ Can be embedded in documents
- ✅ Can be printed
- ✅ Larger file size (~500 KB - 2 MB)

**Example Usage**:
```bash
# Save PNG
curl "http://api.example.com/api/v1/charts/daily?symbol=INFY&format=png" > chart.png

# View in image viewer
open chart.png

# Embed in document
# Use as attachment in emails
```

---

## 🔗 Data Format (JSON Alternative)

For programmatic use, get indicator values as JSON:

```bash
curl "http://localhost:8000/api/v1/indicators?symbol=INFY&from=2024-01-01&indicators=ema,rsi,atr,macd" | jq '.'

# Then plot yourself using matplotlib, plotly, etc.
```

Response:
```json
{
  "meta": {
    "symbol": "INFY",
    "count": 250,
    "indicators": ["ema", "rsi", "atr", "macd"]
  },
  "data": [
    {
      "date": "2024-01-01",
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
      "atr_14": 12.35,
      "macd": 3.45,
      "macd_signal": 2.80,
      "macd_hist": 0.65
    },
    ...
  ]
}
```

---

## 🔄 Request/Response Flow

### Single Symbol Chart
```
Client Request
    ↓
GET /api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31
    ↓
API Handler
    ├─ Fetch OHLCV from database (< 100ms)
    ├─ Calculate indicators (< 50ms)
    ├─ Generate SVG chart (< 50ms)
    └─ Return with cache headers (< 200ms total)
    ↓
Client Response
    ├─ Content-Type: image/svg+xml
    ├─ Cache-Control: public, max-age=3600
    └─ Body: SVG image
```

### Multiple Symbol Charts (Batch)
```
Client Request
    ↓
GET /api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&batch=zip
    ↓
API Handler (parallel)
    ├─ Symbol 1: Fetch + Calc + Generate (< 200ms)
    ├─ Symbol 2: Fetch + Calc + Generate (< 200ms)
    ├─ Symbol 3: Fetch + Calc + Generate (< 200ms)
    ├─ Compress to ZIP
    └─ Return (< 700ms total)
    ↓
Client Response
    ├─ Content-Type: application/zip
    └─ Body: ZIP containing PNG files
```

---

## 📈 Chart Dimensions

**Default Size**: 1200 × 600 pixels

**Customizable**:
```
width   (optional): Chart width in pixels (Default: 1200)
height  (optional): Chart height in pixels (Default: 600)
```

**Example**:
```bash
# Larger chart
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&width=1600&height=800"

# Smaller chart
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&width=800&height=400"
```

---

## ⚡ Performance & Caching

### Query Caching
```
Daily charts:  1 hour cache (data doesn't change during day)
Weekly charts: 24 hour cache (calculated from daily)
Multi-symbol:  30 minute cache (shorter due to complexity)
```

### Cache Headers
```
Cache-Control: public, max-age=3600              # 1 hour
X-Cache-Status: HIT|MISS|BYPASS                  # Cache status
X-Cache-Key: sha256(symbol+from+to+indicators)   # Cache key
ETag: "abc123def456..."                          # For conditional requests
```

### Conditional Requests
```bash
# First request (cache MISS)
curl -i "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31"
# Returns: X-Cache-Status: MISS, ETag: "abc123", Cache-Control: max-age=3600

# Second request (cache HIT)
curl "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31"
# Returns: X-Cache-Status: HIT (instant, no DB query)

# Conditional request with ETag
curl -H 'If-None-Match: "abc123"' "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31"
# Returns: 304 Not Modified (no body transfer)
```

---

## 🔍 Query Parameters Reference

| Parameter | Type | Required | Default | Max | Notes |
|-----------|------|----------|---------|-----|-------|
| symbol | string | Yes | - | - | Single symbol code |
| symbols | string | Yes (multi) | - | 50 | Comma-separated list |
| from | date | Yes | - | - | YYYY-MM-DD format |
| to | date | Yes | - | - | YYYY-MM-DD format |
| indicators | string | No | ema | - | Comma-separated: ema,rsi,atr,macd,bb,all,none |
| format | string | No | svg | - | svg \| png |
| width | int | No | 1200 | 2400 | Pixels |
| height | int | No | 600 | 1200 | Pixels |
| batch | string | No | - | - | zip \| tar (for multi symbols) |
| theme | string | No | dark | - | dark \| light (future) |

---

## ❌ Error Responses

### 400 Bad Request
```json
{
  "detail": "from_date must be <= to_date"
}
```

### 404 Not Found
```json
{
  "detail": "No data found for symbol INFY in date range"
}
```

### 413 Payload Too Large
```json
{
  "detail": "Maximum 50 symbols per request"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Chart generation failed"
}
```

---

## 🧪 Test Examples

### Using curl
```bash
# Single symbol, SVG
curl -o chart.svg "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&to=2024-12-31"

# Single symbol, PNG
curl -o chart.png "http://localhost:8000/api/v1/charts/daily?symbol=INFY&from=2024-01-01&format=png"

# Multiple symbols, ZIP
curl -o charts.zip "http://localhost:8000/api/v1/charts/daily/multi?symbols=INFY,TCS,RELIANCE&from=2024-01-01&format=png&batch=zip"
```

### Using Python
```python
import requests

# Get SVG chart
response = requests.get(
    'http://localhost:8000/api/v1/charts/daily',
    params={
        'symbol': 'INFY',
        'from': '2024-01-01',
        'to': '2024-12-31',
        'indicators': 'ema,rsi,macd'
    }
)
with open('chart.svg', 'wb') as f:
    f.write(response.content)

# Get PNG charts batch
response = requests.get(
    'http://localhost:8000/api/v1/charts/daily/multi',
    params={
        'symbols': 'INFY,TCS,RELIANCE',
        'from': '2024-01-01',
        'to': '2024-12-31',
        'format': 'png',
        'batch': 'zip'
    }
)
with open('charts.zip', 'wb') as f:
    f.write(response.content)
```

### Using JavaScript/HTML
```html
<!DOCTYPE html>
<html>
<head>
    <title>Chart Viewer</title>
    <style>
        img { max-width: 100%; height: auto; }
        .chart-container { margin: 20px; }
    </style>
</head>
<body>
    <div class="chart-container">
        <h2>INFY Daily Chart</h2>
        <img id="chart" src="" alt="Chart loading...">
    </div>

    <script>
        // Generate chart URL
        const params = new URLSearchParams({
            symbol: 'INFY',
            from: '2024-01-01',
            to: '2024-12-31',
            indicators: 'ema,rsi,macd'
        });
        
        document.getElementById('chart').src = 
            `http://localhost:8000/api/v1/charts/daily?${params}`;
    </script>
</body>
</html>
```

---

## 📋 Summary

| Feature | Single | Multiple |
|---------|--------|----------|
| **Endpoint** | /api/v1/charts/daily | /api/v1/charts/daily/multi |
| **Max Symbols** | 1 | 50 |
| **Response** | SVG/PNG | Multipart/ZIP |
| **Cache** | 1 hour | 30 min |
| **Speed** | <200ms | <700ms |
| **Use Case** | Single stock analysis | Sector comparison |

---

**Status**: Production Ready ✅  
**Last Updated**: 2026-06-28
