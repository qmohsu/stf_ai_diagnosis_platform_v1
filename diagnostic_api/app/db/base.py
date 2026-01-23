"""Database base model.

Author: Li-Ta Hsu
Date: January 2026
"""

from typing import Any

from sqlalchemy.ext.declarative import as_declarative, declared_attr


@as_declarative()
class Base:
    """Base class for all database models."""

    id: Any
    __name__: str

    # Generate __tablename__ automatically from class name
    @declared_attr
    def __tablename__(cls) -> str:
        """Convert CamelCase class name to snake_case table name.
        
        Simple implementation: just lowercase the class name.
        For production, might want a proper regex converter, 
        but explicit tablenames in models are often safer.
        """
        return cls.__name__.lower()
