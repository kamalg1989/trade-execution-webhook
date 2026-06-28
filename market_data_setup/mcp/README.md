# Market Data API - MCP Server

MCP (Model Context Protocol) server that exposes the Market Data API as Claude-compatible tools for visual analysis of stock charts and OHLCV data.

## Features

**7 Tools Available:**

1. **get_health** - Check API health status
2. **get_symbols** - Get list of available NSE symbols with metadata
3. **get_ohlcv** - Fetch OHLCV data for a single symbol
4. **get_multi_ohlcv** - Fetch OHLCV data for multiple symbols
5. **get_daily_chart** - Generate daily candlestick chart (SVG)
6. **get_weekly_chart** - Generate weekly candlestick chart (SVG)
7. **get_combined_chart** - Generate daily + weekly combined chart (SVG)

## Installation

### Option 1: Install from Source

```bash
cd market_data_setup/mcp
pip install -e .
```

### Option 2: Manual Setup

```bash
pip install mcp httpx
```

## Running the Server

```bash
python -m market_data_setup.mcp.server
```

Or if installed as script:

```bash
market-data-api-mcp
```

## Configuration

Set the API base URL by editing `server.py`:

```python
API_BASE_URL = "http://165.232.187.97/api/v1"  # Change this if needed
```

## Usage in Claude

Once the MCP server is running, Claude can use these tools to:

- **Fetch stock data**: Get OHLCV data for analysis
- **Generate charts**: Create SVG charts for visual analysis
- **Multi-symbol analysis**: Compare multiple stocks simultaneously
- **Technical indicators**: Include EMA, RSI, ATR, MACD
- **Theme support**: Light and dark theme charts

### Example Claude Prompt

```
Show me the daily and weekly charts for TCS stock from June to December 2024.
Include EMA indicators and use light theme.
```

Claude will:
1. Call `get_combined_chart` with the parameters
2. Receive SVG chart data
3. Display the chart inline

## API Endpoints Exposed

| Tool | API Endpoint | Returns |
|------|-------------|---------|
| get_health | `/health` | JSON status |
| get_symbols | `/symbols` | JSON list |
| get_ohlcv | `/ohlcv` | JSON data |
| get_multi_ohlcv | `/ohlcv/multi` | JSON data |
| get_daily_chart | `/charts/daily` | SVG image |
| get_weekly_chart | `/charts/weekly` | SVG image |
| get_combined_chart | `/charts/combined` | SVG image |

## Example Commands

```python
# Get OHLCV data
get_ohlcv(
    symbol="INFY",
    from_date="2024-01-01",
    to_date="2024-12-31"
)

# Generate combined chart
get_combined_chart(
    symbol="TCS",
    from_date="2024-06-01",
    to_date="2024-12-01",
    indicators="ema",
    theme="light"
)

# Get multiple symbols
get_multi_ohlcv(
    symbols="INFY,TCS,RELIANCE",
    from_date="2024-01-01",
    to_date="2024-12-31"
)
```

## Features

- **15 years of data** (2011-2026)
- **2,953 NSE stocks** (equity only)
- **Real-time updates** (daily at 18:00 IST)
- **Technical indicators** (EMA, RSI, ATR, MACD)
- **Light/Dark themes** for charts
- **SVG format** (lightweight, responsive)
- **Async API** for fast responses

## Architecture

```
Claude
  ↓
MCP Server (server.py)
  ↓
HTTP Client (httpx)
  ↓
Market Data API
  ↓
TimescaleDB + PostgreSQL
```

## Data Coverage

- **Period**: Last 15 years (2011-2026)
- **Stocks**: 2,953 NSE equity stocks (ES type)
- **Frequency**: Daily OHLCV candles
- **Updates**: Automatic daily at 18:00 IST
- **Compression**: Data > 30 days auto-compressed

## License

© 2026 Kamal Prabakaran
