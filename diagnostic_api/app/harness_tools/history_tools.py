"""History tool wrapper for the harness agent loop.

``search_case_history`` queries the ``diagnosis_history`` table
for past diagnoses with matching DTC codes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import or_
from sqlalchemy.orm import Session as SASession

from app.db.session import SessionLocal
from app.harness.tool_registry import ToolDefinition
from app.models_db import DiagnosisHistory, OBDAnalysisSession

logger = logging.getLogger(__name__)

_MAX_DIAG_LEN = 300  # Truncate diagnosis text in results.


async def search_case_history(
    input_data: Dict[str, Any],
) -> str:
    """Search past diagnosis cases for similar faults.

    Joins ``DiagnosisHistory`` with ``OBDAnalysisSession`` and
    filters by DTC codes found in the session's
    ``parsed_summary_payload->'dtc_codes'`` field.

    Args:
        input_data: Must contain ``dtc_codes`` (list of str).
            Optional ``vehicle_id`` (str) and ``limit`` (int,
            default 5).

    Returns:
        Text summaries of past diagnosis results, or a message
        if none found.
    """
    dtc_codes: List[str] = input_data["dtc_codes"]
    vehicle_id: str | None = input_data.get("vehicle_id")
    limit: int = input_data.get("limit", 5)

    if not dtc_codes:
        return "No DTC codes provided for case search."

    db: SASession = SessionLocal()
    try:
        query = (
            db.query(DiagnosisHistory)
            .join(
                OBDAnalysisSession,
                DiagnosisHistory.session_id
                == OBDAnalysisSession.id,
            )
        )

        # Filter by DTC codes in parsed_summary_payload.
        # The dtc_codes field is stored as a comma-separated
        # string in the JSONB, e.g. "P0300, P0301".
        dtc_filters = []
        for code in dtc_codes:
            dtc_filters.append(
                OBDAnalysisSession.parsed_summary_payload[
                    "dtc_codes"
                ].astext.ilike(f"%{code}%")
            )
        query = query.filter(or_(*dtc_filters))

        if vehicle_id:
            query = query.filter(
                OBDAnalysisSession.vehicle_id == vehicle_id,
            )

        rows = (
            query.order_by(DiagnosisHistory.created_at.desc())
            .limit(limit)
            .all()
        )

        if not rows:
            return "No similar past cases found."

        lines: List[str] = []
        for row in rows:
            diag = row.diagnosis_text or ""
            if len(diag) > _MAX_DIAG_LEN:
                diag = diag[:_MAX_DIAG_LEN] + "..."

            created = (
                row.created_at.strftime("%Y-%m-%d %H:%M")
                if row.created_at
                else "unknown"
            )
            lines.append(
                f"[{created}] {row.provider}/{row.model_name}"
                f" -- {diag}"
            )

        return "\n".join(lines)

    except Exception as exc:
        logger.error(
            "search_case_history failed",
            exc_info=exc,
        )
        raise
    finally:
        db.close()


# ------------------------------------------------------------------
# ToolDefinition export
# ------------------------------------------------------------------

SEARCH_CASE_HISTORY_DEF = ToolDefinition(
    name="search_case_history",
    description=(
        "Search past diagnosis cases for similar faults. "
        "Returns summaries of past diagnoses with provider, "
        "model, and date."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "dtc_codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "DTC codes to search for "
                    "(e.g., ['P0300', 'P0301'])"
                ),
            },
            "vehicle_id": {
                "type": "string",
                "description": (
                    "Optional vehicle ID to filter by"
                ),
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "description": (
                    "Maximum number of past cases to return"
                ),
            },
        },
        "required": ["dtc_codes"],
    },
    handler=search_case_history,
    is_read_only=True,
)
