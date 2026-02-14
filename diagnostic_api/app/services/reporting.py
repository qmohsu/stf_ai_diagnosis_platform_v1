import json
from datetime import date, datetime
from typing import Dict, List, Any
from sqlalchemy.orm import Session

from app.models_db import DiagnosticSession, DiagnosticFeedback

class DailyReporter:
    """Service to generate daily summaries of diagnostic activity."""

    def __init__(self, db: Session):
        self.db = db

    def generate_report(self, target_date: date) -> Dict[str, Any]:
        """
        Aggregates stats for the given date.
        
        Metrics:
        1. Total Sessions
        2. Average Feedback Rating
        3. Risk Distribution (Top Subsytems)
        4. Flagged Sessions (Risky or Negative Feedback)
        """
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())

        # 1. Fetch Sessions
        sessions = self.db.query(DiagnosticSession).filter(
            DiagnosticSession.created_at >= start_dt,
            DiagnosticSession.created_at <= end_dt
        ).all()
        
        total_sessions = len(sessions)
        
        # 2. Fetch Feedback
        feedbacks = self.db.query(DiagnosticFeedback).filter(
            DiagnosticFeedback.created_at >= start_dt,
            DiagnosticFeedback.created_at <= end_dt
        ).all()
        
        avg_rating = 0.0
        if feedbacks:
            avg_rating = sum(f.rating for f in feedbacks) / len(feedbacks)
            
        # 3. Aggregate Risks (Top 3)
        risk_counts = {}
        flagged_sessions = []

        for session in sessions:
            # Check for high risk or anomalies
            if session.result_payload:
                risks = session.result_payload.get('subsystem_risks', [])
                for risk in risks:
                    name = risk.get('subsystem_name', 'Unknown')
                    level = risk.get('risk_level', 0)
                    
                    # Count prevalence
                    risk_counts[name] = risk_counts.get(name, 0) + 1
                    
                    # Flag high risk sessions
                    if level > 0.8:
                        flagged_sessions.append({
                            "session_id": str(session.id),
                            "reason": f"High Risk: {name} ({level})",
                            "vehicle": session.vehicle_id
                        })

        top_risks = sorted(risk_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # 4. Flag Low Ratings
        for fb in feedbacks:
            if fb.rating <= 2:
                flagged_sessions.append({
                    "session_id": str(fb.session_id),
                    "reason": f"Low Rating: {fb.rating} stars",
                    "comment": fb.comments
                })
        
        report = {
            "date": str(target_date),
            "generated_at": str(datetime.utcnow()),
            "metrics": {
                "total_diagnoses": total_sessions,
                "feedback_count": len(feedbacks),
                "average_rating": round(avg_rating, 2)
            },
            "top_risks": [{"subsystem": k, "count": v} for k, v in top_risks],
            "flagged_sessions_count": len(flagged_sessions),
            "flagged_sessions": flagged_sessions
        }
        
        return report

    def save_report_to_disk(self, report_data: Dict[str, Any], output_path: str):
        """Saves the report as JSON to the specified path."""
        with open(output_path, 'w') as f:
            json.dump(report_data, f, indent=2)
