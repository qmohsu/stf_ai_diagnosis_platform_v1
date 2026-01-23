"""CRUD operations for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026
"""

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app import models, models_db


def create_diagnostic_session(
    db: Session, request: models.DiagnosticRequest
) -> models_db.DiagnosticSession:
    """Create a new diagnostic session."""
    # Ensure vehicle exists (simple upsert or find-or-create)
    vehicle = db.query(models_db.Vehicle).filter(models_db.Vehicle.id == request.vehicle_id).first()
    if not vehicle:
        vehicle = models_db.Vehicle(id=request.vehicle_id)
        db.add(vehicle)
        db.commit()
        db.refresh(vehicle)
    
    # Create session
    db_session = models_db.DiagnosticSession(
        vehicle_id=request.vehicle_id,
        status="PENDING",
        request_payload=request.model_dump(mode='json'),
        result_payload=None,
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    return db_session


def get_diagnostic_session(db: Session, session_id: UUID) -> Optional[models_db.DiagnosticSession]:
    """Retrieve diagnostic session by ID."""
    return db.query(models_db.DiagnosticSession).filter(models_db.DiagnosticSession.id == session_id).first()


def update_diagnostic_result(
    db: Session, session_id: UUID, result: models.DiagnosticResponse
) -> Optional[models_db.DiagnosticSession]:
    """Update session with diagnostic results."""
    db_session = get_diagnostic_session(db, session_id)
    if not db_session:
        return None
    
    db_session.status = "COMPLETED"
    db_session.result_payload = result.model_dump(mode='json')
    # Extract risk score from the first subsystem if available, or average
    if result.subsystem_risks:
        # Simple heuristic: max risk
        db_session.risk_score = max(r.risk_level for r in result.subsystem_risks)
    
    db.commit()
    db.refresh(db_session)
    return db_session
