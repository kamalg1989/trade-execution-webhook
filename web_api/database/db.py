"""
Database connection and ORM models for web platform
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

# Database configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://root:postgres@localhost:5432/trading_platform"
)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_session():
    """Get database session"""
    return SessionLocal()


# ORM Models
class SLPosition(Base):
    """Stop Loss Position model"""
    __tablename__ = "sl_positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    order_id = Column(String(50), unique=True, nullable=False, index=True)
    parent_order_id = Column(String(50), index=True)
    symbol = Column(String(10), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    initial_stop_loss = Column(Float)
    current_price = Column(Float)
    target_price = Column(Float)
    status = Column(String(20), default='OPEN', index=True)
    exit_price = Column(Float)
    exchange_token = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime)

    # Relationships
    audit_logs = relationship("SLAuditLog", back_populates="position")
    alerts = relationship("SLAlert", back_populates="position")


class UserTrade(Base):
    """User trade model for portfolio tracking"""
    __tablename__ = "user_trades"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    order_id = Column(String(50), unique=True, nullable=False)
    symbol = Column(String(10), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    entry_date = Column(DateTime, default=datetime.utcnow, index=True)
    exit_price = Column(Float)
    exit_date = Column(DateTime)
    status = Column(String(20), default='OPEN', index=True)
    pnl = Column(Float)
    pnl_percent = Column(Float)
    trade_type = Column(String(10), default='BUY')


class SLAuditLog(Base):
    """Audit log for SL changes"""
    __tablename__ = "sl_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("sl_positions.id"), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    old_sl = Column(Float)
    new_sl = Column(Float)
    old_status = Column(String(20))
    new_status = Column(String(20))
    reason = Column(String(255))
    user_id = Column(Integer)
    dhan_response = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    position = relationship("SLPosition", back_populates="audit_logs")


class PortfolioHistory(Base):
    """Portfolio performance history"""
    __tablename__ = "portfolio_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    portfolio_value = Column(Float, nullable=False)
    invested_value = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False)
    realized_pnl = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    position_count = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class SLAlert(Base):
    """SL Alert model"""
    __tablename__ = "sl_alerts"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("sl_positions.id"), nullable=False, index=True)
    alert_type = Column(String(20), nullable=False, index=True)
    alert_message = Column(Text, nullable=False)
    current_price = Column(Float)
    stop_loss = Column(Float)
    distance_percent = Column(Float)
    acknowledged = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    position = relationship("SLPosition", back_populates="alerts")


class StockRecommendation(Base):
    """Stock recommendation cache"""
    __tablename__ = "stock_recommendations"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    company_name = Column(String(255))
    current_price = Column(Float)
    change_percent = Column(Float)
    target_price = Column(Float)
    stop_loss = Column(Float)
    confidence_score = Column(Integer)
    analysis_reason = Column(Text)
    recommended_qty = Column(Integer)
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    valid_until = Column(DateTime)


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully")


if __name__ == "__main__":
    init_db()
