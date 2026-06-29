# Sharing Market Data API MCP - Plugin Distribution Guide

## 📦 **3 Ways to Share Your MCP Tool**

---

## **Option 1: Simple HTTP URL (Easiest)**

**Best for**: Sharing quickly with anyone

Share this URL with others:
```
http://165.232.187.97:8002/
```

They can immediately start using it with:

### **Python**
```python
import httpx
client = httpx.Client()

response = client.post(
    "http://165.232.187.97:8002/call?tool_name=get_daily_chart",
    json={
        "symbol": "TCS",
        "from_date": "2024-06-01",
        "to_date": "2024-12-31"
    }
)
print(response.json())
```

### **JavaScript**
```javascript
const response = await fetch("http://165.232.187.97:8002/call?tool_name=get_ohlcv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
        symbol: "INFY",
        from_date: "2024-01-01",
        to_date": "2024-12-31"
    })
});
const data = await response.json();
console.log(data);
```

### **cURL**
```bash
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_health" \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

## **Option 2: Python Package (NPM/PyPI)**

**Best for**: Professional distribution

### **Step 1: Create setup.py**

```python
from setuptools import setup, find_packages

setup(
    name="market-data-api-mcp",
    version="1.0.0",
    description="Market Data API - MCP Server for NSE Stock Data",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/trade-execution-webhook",
    packages=find_packages(),
    install_requires=[
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "httpx>=0.24.0",
    ],
    entry_points={
        'console_scripts': [
            'market-data-api-mcp=market_data_setup.mcp.server:main',
        ],
    },
    python_requires=">=3.10",
)
```

### **Step 2: Publish to PyPI**

```bash
# Install build tools
pip install build twine

# Build the package
python -m build

# Upload to PyPI
twine upload dist/*
```

### **Step 3: Others can install with**

```bash
pip install market-data-api-mcp

# Run the server
market-data-api-mcp
```

---

## **Option 3: Docker Container**

**Best for**: Guaranteed consistency across environments

### **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY market_data_setup/mcp/pyproject.toml .
RUN pip install fastapi uvicorn[standard] httpx

# Copy code
COPY market_data_setup/mcp ./

# Expose port
EXPOSE 8002

# Run server
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
```

### **Step 2: Build Docker image**

```bash
docker build -t market-data-mcp:1.0.0 .
```

### **Step 3: Push to Docker Hub**

```bash
docker tag market-data-mcp:1.0.0 yourusername/market-data-mcp:1.0.0
docker push yourusername/market-data-mcp:1.0.0
```

### **Step 4: Others can run with**

```bash
docker run -p 8002:8002 yourusername/market-data-mcp:1.0.0
```

---

## **Option 4: GitHub Repository (Recommended)**

**Best for**: Open source sharing with documentation

### **Step 1: Make repo public**

Your repo is already at:
```
https://github.com/kamalg1989/trade-execution-webhook
```

### **Step 2: Create INSTALLATION.md**

```markdown
# Installation

## Quick Start
```bash
git clone https://github.com/kamalg1989/trade-execution-webhook.git
cd trade-execution-webhook/market_data_setup/mcp
python -m venv venv
source venv/bin/activate
pip install -r ../requirements.txt fastapi uvicorn httpx
python server.py
```
```

### **Step 3: Others can clone and use**

```bash
git clone https://github.com/kamalg1989/trade-execution-webhook.git
cd trade-execution-webhook/market_data_setup/mcp
# Follow setup instructions
```

---

## **Option 5: Single File Distribution**

**Best for**: Ultra-simple sharing - just send one file!

### **Create a standalone server file**

Save this as `market_data_mcp_standalone.py`:

```python
#!/usr/bin/env python3
"""
Market Data API - MCP Server (Standalone)
Run: python market_data_mcp_standalone.py
"""

import asyncio
import httpx
import json
from typing import Optional
from fastapi import FastAPI, Body
import uvicorn

API_BASE_URL = "http://165.232.187.97/api/v1"  # Change this if needed
app = FastAPI(title="Market Data API - MCP Server")

# [Include all the tool definitions and handlers from server.py]
# Just copy the entire server.py content here

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
```

**Others can run with:**
```bash
python market_data_mcp_standalone.py
```

---

## **Option 6: Configuration File Only**

**Best for**: Using your hosted MCP server**

Create a config file `market_data_mcp_config.json`:

```json
{
  "name": "Market Data API MCP",
  "version": "1.0.0",
  "mcp_server": "http://165.232.187.97:8002/",
  "description": "Market Data API - MCP Server for NSE Stock Data",
  "tools": [
    {
      "name": "get_health",
      "description": "Check API health status"
    },
    {
      "name": "get_symbols",
      "description": "Get list of NSE stocks"
    },
    {
      "name": "get_ohlcv",
      "description": "Fetch OHLCV data for a stock"
    },
    {
      "name": "get_multi_ohlcv",
      "description": "Fetch OHLCV for multiple stocks"
    },
    {
      "name": "get_daily_chart",
      "description": "Generate daily candlestick chart"
    },
    {
      "name": "get_weekly_chart",
      "description": "Generate weekly candlestick chart"
    },
    {
      "name": "get_combined_chart",
      "description": "Generate combined daily+weekly chart"
    }
  ]
}
```

**Others can load with:**
```python
import json
with open('market_data_mcp_config.json') as f:
    config = json.load(f)
    
# Use config['mcp_server'] to connect
```

---

## **🎯 RECOMMENDED APPROACH**

### **For Maximum Sharing - Use All Methods:**

1. **Primary**: Keep HTTP server running at `http://165.232.187.97:8002/`
2. **Secondary**: Share GitHub repo with documentation
3. **Tertiary**: Publish to PyPI as optional package
4. **Fallback**: Share config file for custom deployments

---

## **📤 SHARING WITH OTHERS**

### **What to Share**

**Share This:**
```
🔗 MCP Server: http://165.232.187.97:8002/
📖 Docs: http://165.232.187.97/api/v1/docs
📚 GitHub: https://github.com/kamalg1989/trade-execution-webhook
📋 Guide: MCP_INTEGRATION_GUIDE.md
```

**OR (If they want to self-host):**
```
1. GitHub repo
2. Docker image
3. PyPI package
```

---

## **💡 QUICKEST SHARE METHOD**

Just give them:

```
Server URL: http://165.232.187.97:8002/

Example command:
curl -X POST "http://165.232.187.97:8002/call?tool_name=get_health" \
  -H "Content-Type: application/json" -d '{}'

Documentation: https://github.com/kamalg1989/trade-execution-webhook/blob/main/MCP_INTEGRATION_GUIDE.md
```

That's it! They can start using immediately. ✅

---

## **🔐 SECURITY CONSIDERATIONS**

Before sharing publicly:

1. **Rate Limiting**: Add rate limits to API
   ```python
   from slowapi import Limiter
   limiter = Limiter(key_func=get_remote_address)
   ```

2. **Authentication**: Add API keys if needed
   ```python
   from fastapi.security import APIKeyHeader
   ```

3. **Logging**: Monitor who's using the service
   ```python
   logger.info(f"Request from {request.client.host}")
   ```

4. **Resource Limits**: Set max payload size
   ```python
   app = FastAPI(..., max_size=1000000)
   ```

---

## **📊 COMPARISON TABLE**

| Method | Ease | Reach | Control | Maintenance |
|--------|------|-------|---------|-------------|
| **HTTP URL** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| **GitHub** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| **PyPI** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Docker** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Config File** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

---

**Ready to share? Pick a method above!** 🚀
