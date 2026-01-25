from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.api.v1.schemas import FeedbackRequest
from app.models_db import DiagnosticSession, DiagnosticFeedback

router = APIRouter()

@router.post("/", status_code=status.HTTP_201_CREATED)
def submit_feedback(feedback: FeedbackRequest, db: Session = Depends(get_db)):
    """
    Submit feedback for a diagnostic session.
    """
    # Verify session exists
    session_record = db.query(DiagnosticSession).filter(DiagnosticSession.id == feedback.session_id).first()
    if not session_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnostic session {feedback.session_id} not found"
        )
    
    # Check if feedback already exists
    existing = db.query(DiagnosticFeedback).filter(DiagnosticFeedback.session_id == feedback.session_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback already submitted for this session"
        )

    db_feedback = DiagnosticFeedback(
        session_id=feedback.session_id,
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
        comments=feedback.comments,
        corrected_diagnosis=feedback.corrected_diagnosis
    )
    db.add(db_feedback)
    db.commit()
    db.refresh(db_feedback)
    
    # TODO: [RAG Enrichment] If rating >= 4, trigger background task to ingest this session 
    # as a "Solved Case" into Weaviate. This creates a "Knowledge Flywheel".

    return {"status": "success", "feedback_id": str(db_feedback.id)}
