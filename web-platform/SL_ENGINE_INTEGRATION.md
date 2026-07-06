# Stop Loss Engine Integration

Complete integration of SL Engine logic with web platform for real-time stop loss management.

---

## Architecture

```
┌─────────────────────────────────────────┐
│   Frontend (StopLossTracker.jsx)        │
│  - Display all positions with SL status │
│  - View button → SLOrderModal           │
│  - Edit/Update SL in real-time          │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│   FastAPI Backend (routers/sl_engine.py)│
│  - /api/sl-alerts (GET)                 │
│  - /api/sl-orders/{id} (GET)            │
│  - /api/update-sl (POST)                │
│  - /api/cancel-sl-order/{id} (POST)     │
└──────────────┬──────────────────────────┘
               │
       ┌───────┼───────┐
       ▼       ▼       ▼
    SLEngine DhanAPI  DB
  (Python)  (Orders) (Positions)
```

---

## Risk Zone Logic (From entry_engine.py)

### Safe Zone (Green)
```python
distance = current_price - stop_loss
distance_percent = (distance / current_price) * 100

if distance_percent > 10:
    status = "SAFE"
    action = "Normal monitoring"
    alert = False
```

**Characteristics:**
- Price is >10% above SL
- Position is secure
- No alerts triggered
- Can hold or add to position

### Warning Zone (Yellow)
```python
if 5 < distance_percent <= 10:
    status = "WARNING"
    action = "Monitor closely, consider reducing position"
    alert = True
    severity = "LOW"
```

**Characteristics:**
- Price is 5-10% above SL
- Risk is increasing
- Alerts are active
- Consider partial exits
- Avoid averaging down

### Critical Zone (Red)
```python
if distance_percent <= 5:
    status = "CRITICAL"
    action = "Exit immediately or reduce position size"
    alert = True
    severity = "HIGH"
    auto_exit_possible = True
```

**Characteristics:**
- Price is <5% above SL
- Imminent loss risk
- Critical alerts active
- SL will likely execute soon
- Position should be exited

---

## SL Order Management

### Order Types
```python
ORDER_TYPES = {
    "PARENT": "Main buy order (e.g., BUY 100 shares @ 3850)",
    "SL": "Stop loss order (e.g., SELL 100 shares @ 3700 if touched)",
    "TARGET": "Target/Profit taking order (e.g., SELL 100 @ 4200)"
}
```

### Order Statuses
```python
ORDER_STATUSES = {
    "PENDING": "Order placed, awaiting market opening or conditions",
    "ACTIVE": "SL is live and monitoring",
    "TRIGGERED": "Price hit SL, exit order placed",
    "EXECUTED": "Position closed at SL price",
    "PARTIAL": "Partially filled",
    "CANCELLED": "Manually cancelled by user",
    "REJECTED": "Rejected by exchange"
}
```

---

## Frontend Components

### 1. StopLossTracker Page

**Props:** None (Fetches from API)

**State:**
- `positions` - All open positions with SL status
- `alerts` - Active risk alerts
- `selectedOrder` - For modal
- `autoRefresh` - Auto-update toggle

**Features:**
- Color-coded status badges (safe/warning/critical)
- Real-time risk % bar
- View SL Orders button (Eye icon)
- Edit SL button (Pencil icon)
- Close position button (Trash icon)
- Auto-refresh every 5 seconds

**API Calls:**
```javascript
// Fetch positions with SL status
GET /api/sl-alerts

// Response:
{
  "positions": [
    {
      "id": "POS001",
      "symbol": "TCS",
      "currentPrice": 3850,
      "stopLoss": 3700,
      "status": "safe|warning|critical",
      "riskPercent": 3.9,
      "parentOrderId": "ORD123"
    }
  ],
  "alerts": [
    {
      "symbol": "INFY",
      "message": "⚠️ WARNING: Price within 5% of SL",
      "severity": "warning|critical",
      "timestamp": "2026-07-03T16:45:00Z"
    }
  ]
}
```

### 2. SLOrderModal Component

**Props:**
- `position` - Selected position object
- `onClose` - Close handler
- `onSave` - Save handler

**Displays:**
- Position summary (current price, SL, distance)
- Active SL orders from Dhan API
- Order details (status, trigger price, quantity)
- Update SL form
- Tips and warnings

**Features:**
- View actual Dhan SL orders
- Copy order IDs to clipboard
- Cancel active SL orders
- Update SL with validation
- Shows impact of new SL

**API Calls:**
```javascript
// Fetch SL orders for position
GET /api/sl-orders/{positionId}

// Response:
{
  "orders": [
    {
      "orderId": "SL_ORD123",
      "triggerPrice": 3700,
      "orderType": "STOPLOSS",
      "status": "ACTIVE",
      "quantity": 100,
      "validity": "DAY"
    }
  ],
  "parentOrderId": "ORD123"
}

// Update SL
POST /api/update-sl
{
  "positionId": "POS001",
  "stopLoss": 3650
}

// Cancel SL order
POST /api/cancel-sl-order/{orderId}
```

---

## Backend Implementation

### Router: sl_engine.py

#### GET /api/sl-alerts
```python
def get_sl_alerts():
    """
    Fetches all open positions and calculates risk zones
    Uses SLEngine.calculate_risk_zone() logic
    """
    positions = query_open_positions()

    for pos in positions:
        # Get current price from Dhan
        current_price = dhan.get_price(pos.symbol)

        # Calculate distance
        distance_pct = (current_price - pos.stop_loss) / current_price * 100

        # Determine zone
        if distance_pct > 10:
            zone = "safe"
        elif distance_pct > 5:
            zone = "warning"
        else:
            zone = "critical"

        # Create alert if warning/critical
        if zone in ["warning", "critical"]:
            alerts.append(create_alert(pos, zone))

    return {positions, alerts}
```

#### GET /api/sl-orders/{position_id}
```python
def get_sl_orders(position_id):
    """
    Fetches actual SL orders from Dhan API for a position
    """
    position = query_position(position_id)
    parent_order_id = position.parent_order_id

    # Fetch from Dhan API
    orders = dhan.get_order_status(parent_order_id)

    # Filter SL orders
    sl_orders = [o for o in orders if o.type in ["STOPLOSS", "SL"]]

    return {
        "orders": sl_orders,
        "parentOrderId": parent_order_id
    }
```

#### POST /api/update-sl
```python
def update_stop_loss(position_id, new_sl):
    """
    1. Validate new SL (must be reasonable)
    2. Cancel existing SL order from Dhan
    3. Place new SL order using SLEngine
    4. Update DB
    5. Log change
    """
    position = query_position(position_id)

    # Validation
    if new_sl > position.current_price:
        raise Error("SL cannot be above current price")

    # Cancel existing SL
    sl_orders = dhan.get_order_status(position.parent_order_id)
    for order in sl_orders:
        if order.type == "STOPLOSS":
            dhan.cancel_order(order.id)

    # Place new SL using SLEngine
    sl_engine = SLEngine()
    new_order = sl_engine.create_stop_loss_order(
        parent_order_id=position.parent_order_id,
        symbol=position.symbol,
        quantity=position.quantity,
        stop_price=new_sl
    )

    # Update DB
    position.stop_loss = new_sl
    db.commit()

    # Log
    audit_log.add(f"Updated SL: {old_sl} → {new_sl}")

    return {"success": True, "newSLOrderId": new_order.id}
```

#### POST /api/cancel-sl-order/{order_id}
```python
def cancel_sl_order(order_id):
    """
    Cancel a specific SL order from Dhan API
    WARNING: Position will have no SL protection
    """
    result = dhan.cancel_order(order_id)

    if result.success:
        logger.warning(f"SL order cancelled: {order_id}")
        return {"success": True}
    else:
        raise Error("Failed to cancel SL order")
```

---

## Database Schema

### SL_Positions Table
```sql
CREATE TABLE sl_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    parent_order_id VARCHAR(50),
    symbol VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price DECIMAL(10, 2) NOT NULL,
    stop_loss DECIMAL(10, 2) NOT NULL,
    initial_stop_loss DECIMAL(10, 2),
    current_price DECIMAL(10, 2),
    status VARCHAR(20) DEFAULT 'OPEN',
    exchange_token VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sl_positions_symbol ON sl_positions(symbol);
CREATE INDEX idx_sl_positions_parent_order ON sl_positions(parent_order_id);
```

### SL_Audit_Log Table
```sql
CREATE TABLE sl_audit_log (
    id SERIAL PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES sl_positions(id),
    action VARCHAR(50) NOT NULL,
    old_sl DECIMAL(10, 2),
    new_sl DECIMAL(10, 2),
    reason VARCHAR(255),
    user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Real-Time Updates

### WebSocket Connection (Optional Enhancement)
```python
# Backend: Send SL updates via WebSocket
@app.websocket("/ws/sl-alerts")
async def websocket_sl_alerts(websocket: WebSocket):
    await websocket.accept()

    while True:
        # Every 2 seconds, send updated positions
        positions = get_sl_alerts()
        await websocket.send_json(positions)
        await asyncio.sleep(2)
```

```javascript
// Frontend: Listen for SL alerts
const ws = new WebSocket('ws://localhost:8004/ws/sl-alerts');
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    setPositions(data.positions);
    setAlerts(data.alerts);
};
```

---

## Integration Checklist

- [ ] Backend router created (sl_engine.py)
- [ ] API endpoints implemented
- [ ] Database tables created
- [ ] Frontend StopLossTracker enhanced
- [ ] SLOrderModal component created
- [ ] Dhan API integration verified
- [ ] Risk zone calculations tested
- [ ] Real-time updates working
- [ ] Alert notifications functional
- [ ] SL order cancellation working
- [ ] Audit logging in place

---

## Example Workflow

### User Views Dashboard
1. User opens Stop Loss Tracker page
2. Frontend calls `GET /api/sl-alerts`
3. Backend fetches all open positions
4. For each position:
   - Gets current price from Dhan API
   - Calculates distance to SL
   - Determines risk zone (safe/warning/critical)
   - Creates alert if needed
5. Frontend displays positions with color-coded status
6. Auto-refresh every 5 seconds

### User Clicks "View SL Orders" for TCS
1. Frontend opens SLOrderModal
2. Calls `GET /api/sl-orders/POS001`
3. Backend fetches orders from Dhan using parent order ID
4. Modal displays:
   - All active SL orders for TCS
   - Order IDs, trigger prices, status
   - Cancel buttons for ACTIVE orders
5. User can cancel or update SL

### User Updates SL from 3700 to 3650
1. User enters new SL value in form
2. Clicks "Update Stop Loss"
3. Frontend validates (must be below current price)
4. Calls `POST /api/update-sl`
5. Backend:
   - Cancels existing SL order from Dhan
   - Places new SL order using SLEngine
   - Updates DB
   - Logs change
6. Frontend confirms update
7. Modal closes and list refreshes

---

## Key Python Code Integration

### From entry_engine.py
```python
# Risk zone calculation
def calculate_risk_zone(current_price, stop_loss, entry_price):
    distance = current_price - stop_loss
    distance_pct = (distance / current_price) * 100

    if distance_pct > 10:
        return "safe"
    elif distance_pct > 5:
        return "warning"
    else:
        return "critical"

# SL order creation
def create_stop_loss_order(parent_order_id, symbol, quantity, stop_price):
    sl_order = {
        'parentOrderId': parent_order_id,
        'transactionType': 'SELL',
        'orderType': 'STOPLOSS',
        'quantity': quantity,
        'triggerPrice': stop_price,
        'validity': 'GTC'  # Good Till Cancelled
    }
    return dhan.place_order(sl_order)
```

### Integration Point
```python
# In web backend
from entry_engine import SLEngine

sl_engine = SLEngine()

# Use for calculations
risk_zone = sl_engine.calculate_risk_zone(3850, 3700, 3800)

# Use for creating orders
new_order = sl_engine.create_stop_loss_order(...)
```

---

## Security & Validation

1. **Validate SL Before Update**
   - Must be below current price
   - Must be at least 2% below entry (hard rule)
   - Cannot update if position closed

2. **Order Cancellation Warning**
   - Show confirmation dialog
   - Warn about loss of protection
   - Log user acknowledgment

3. **Rate Limiting**
   - Max 1 SL update per 5 seconds per position
   - Max 10 SL updates per position per day
   - Prevents rapid changes

4. **Audit Trail**
   - Log all SL changes
   - Track user, old/new values, timestamp
   - Enable compliance review

---

## Testing

### Unit Tests
```python
def test_calculate_risk_zone():
    assert calculate_risk_zone(100, 80, 95) == "safe"  # >10% above
    assert calculate_risk_zone(100, 90, 95) == "warning"  # 5-10% above
    assert calculate_risk_zone(100, 99, 95) == "critical"  # <5% above

def test_update_sl_validation():
    assert update_sl(pos, 150) == Error  # Above current price
    assert update_sl(pos, 3650) == Success  # Valid
```

### Integration Tests
```javascript
// Test SL order view
cy.visit('/stop-loss-tracker');
cy.contains('TCS').click(); // View orders
cy.get('[data-testid="sl-orders"]').should('exist');

// Test SL update
cy.get('[data-testid="edit-sl"]').click();
cy.get('input').type('3650');
cy.get('[data-testid="save"]').click();
cy.contains('Stop loss updated').should('be.visible');
```

---

## Monitoring & Alerts

### Critical Alerts (Auto-notified)
- Position in critical zone (<5% from SL)
- SL order failed to place
- SL order execution failed

### Warning Alerts (User can dismiss)
- Entering warning zone
- SL order status changed
- Price movement alerts

### Dashboard Metrics
- Total positions monitored
- Average distance to SL
- Positions in each risk zone
- Failed/cancelled SL orders

