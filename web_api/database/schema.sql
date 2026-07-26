-- Stop Loss Tracking Tables
-- Created: 2026-07-03
-- Purpose: Track SL orders, positions, and P&L for web platform

-- SL Positions Table
CREATE TABLE IF NOT EXISTS sl_positions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    parent_order_id VARCHAR(50),
    symbol VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price NUMERIC(10, 2) NOT NULL,
    stop_loss NUMERIC(10, 2) NOT NULL,
    initial_stop_loss NUMERIC(10, 2),
    current_price NUMERIC(10, 2),
    target_price NUMERIC(10, 2),
    status VARCHAR(20) DEFAULT 'OPEN',  -- OPEN, CLOSED, EXECUTED, CANCELLED
    exit_price NUMERIC(10, 2),
    exchange_token VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE INDEX idx_sl_positions_user ON sl_positions(user_id);
CREATE INDEX idx_sl_positions_symbol ON sl_positions(symbol);
CREATE INDEX idx_sl_positions_parent_order ON sl_positions(parent_order_id);
CREATE INDEX idx_sl_positions_status ON sl_positions(status);
CREATE INDEX idx_sl_positions_created ON sl_positions(created_at);

-- User Trades Table (for portfolio tracking)
CREATE TABLE IF NOT EXISTS user_trades (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price NUMERIC(10, 2) NOT NULL,
    entry_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    exit_price NUMERIC(10, 2),
    exit_date TIMESTAMP,
    status VARCHAR(20) DEFAULT 'OPEN',  -- OPEN, CLOSED, PARTIAL
    pnl NUMERIC(12, 2),
    pnl_percent NUMERIC(6, 2),
    trade_type VARCHAR(10) DEFAULT 'BUY'  -- BUY, SELL
);

CREATE INDEX idx_user_trades_user ON user_trades(user_id);
CREATE INDEX idx_user_trades_symbol ON user_trades(symbol);
CREATE INDEX idx_user_trades_status ON user_trades(status);
CREATE INDEX idx_user_trades_date ON user_trades(entry_date);

-- SL Audit Log (for tracking changes)
CREATE TABLE IF NOT EXISTS sl_audit_log (
    id SERIAL PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES sl_positions(id),
    action VARCHAR(50) NOT NULL,  -- CREATE, UPDATE, CANCEL, EXECUTE
    old_sl NUMERIC(10, 2),
    new_sl NUMERIC(10, 2),
    old_status VARCHAR(20),
    new_status VARCHAR(20),
    reason VARCHAR(255),
    user_id INTEGER,
    dhan_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_log_position ON sl_audit_log(position_id);
CREATE INDEX idx_audit_log_action ON sl_audit_log(action);
CREATE INDEX idx_audit_log_date ON sl_audit_log(created_at);

-- Portfolio History (for P&L tracking)
CREATE TABLE IF NOT EXISTS portfolio_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    portfolio_value NUMERIC(15, 2) NOT NULL,
    invested_value NUMERIC(15, 2) NOT NULL,
    unrealized_pnl NUMERIC(15, 2) NOT NULL,
    realized_pnl NUMERIC(15, 2) NOT NULL,
    total_pnl NUMERIC(15, 2) NOT NULL,
    position_count INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_portfolio_history_user ON portfolio_history(user_id);
CREATE INDEX idx_portfolio_history_date ON portfolio_history(timestamp);

-- SL Alert History (for tracking alerts sent)
CREATE TABLE IF NOT EXISTS sl_alerts (
    id SERIAL PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES sl_positions(id),
    alert_type VARCHAR(20) NOT NULL,  -- WARNING, CRITICAL
    alert_message TEXT NOT NULL,
    current_price NUMERIC(10, 2),
    stop_loss NUMERIC(10, 2),
    distance_percent NUMERIC(6, 2),
    acknowledged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sl_alerts_position ON sl_alerts(position_id);
CREATE INDEX idx_sl_alerts_type ON sl_alerts(alert_type);
CREATE INDEX idx_sl_alerts_date ON sl_alerts(created_at);

-- Recommendations Cache (for daily recommendations)
CREATE TABLE IF NOT EXISTS stock_recommendations (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    company_name VARCHAR(255),
    current_price NUMERIC(10, 2),
    change_percent NUMERIC(6, 2),
    target_price NUMERIC(10, 2),
    stop_loss NUMERIC(10, 2),
    confidence_score NUMERIC(3, 0),
    analysis_reason TEXT,
    recommended_qty INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP
);

CREATE INDEX idx_recommendations_symbol ON stock_recommendations(symbol);
CREATE INDEX idx_recommendations_generated ON stock_recommendations(generated_at);

-- Grant permissions
GRANT ALL PRIVILEGES ON sl_positions TO root;
GRANT ALL PRIVILEGES ON user_trades TO root;
GRANT ALL PRIVILEGES ON sl_audit_log TO root;
GRANT ALL PRIVILEGES ON portfolio_history TO root;
GRANT ALL PRIVILEGES ON sl_alerts TO root;
GRANT ALL PRIVILEGES ON stock_recommendations TO root;
