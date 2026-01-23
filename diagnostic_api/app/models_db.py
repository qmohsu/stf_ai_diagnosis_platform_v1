"""Database models for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class User(Base):
    """User table (stub for future auth)."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Vehicle(Base):
    """Vehicle registry."""

    __tablename__ = "vehicles"

    id = Column(String(50), primary_key=True, index=True)  # VIN or internal ID
    make = Column(String(50), nullable=True)
    model = Column(String(50), nullable=True)
    year = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    diagnostic_sessions = relationship("DiagnosticSession", back_populates="vehicle")


class DiagnosticSession(Base):
    """Diagnostic session records."""

    __tablename__ = "diagnostic_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    vehicle_id = Column(String(50), ForeignKey("vehicles.id"), nullable=False, index=True)
    
    # Status tracking
    status = Column(String(20), default="PENDING", index=True)  # PENDING, COMPLETED, FAILED
    
    # Request/Response payloads stored as JSONB for flexibility
    request_payload = Column(JSONB, nullable=False)
    result_payload = Column(JSONB, nullable=True)
    
    # Extracted fields for efficient querying without parsing JSONB
    risk_score = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle", back_populates="diagnostic_sessions")
