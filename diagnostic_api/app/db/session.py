"""Database session configuration.

Author: Li-Ta Hsu
Date: January 2026
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Create engine with pool configuration suitable for containerized environment
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_port if settings.db_port < 20 else 20,  # Just a heuristic, usually driven by env vars
    max_overflow=10,
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
