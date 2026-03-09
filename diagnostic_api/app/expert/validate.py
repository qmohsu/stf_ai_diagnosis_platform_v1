"""Validation logic for Expert Model output."""

import json
import logging
import re
from typing import Optional

from pydantic import ValidationError

from app.expert.schemas import LLMDiagnosisResponse

logger = logging.getLogger(__name__)


def validate_llm_output(
    raw_text: str,
) -> Optional[LLMDiagnosisResponse]:
    """Parse and validate the raw text response from the LLM.

    Handles:
    - Markdown code block stripping
    - JSON parsing
    - Pydantic schema validation

    Args:
        raw_text: Raw LLM output string.

    Returns:
        Validated LLMDiagnosisResponse or None on failure.
    """
    clean_text = raw_text.strip()

    # 1. Strip Markdown code blocks if present
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, clean_text)
    if match:
        clean_text = match.group(1)

    # 2. Parse JSON
    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON format from LLM", exc_info=e)
        return None

    # 3. Pydantic Validation
    try:
        model = LLMDiagnosisResponse(**data)
        return model
    except ValidationError as e:
        logger.warning("LLM output schema mismatch", exc_info=e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected error validating LLM output", exc_info=e,
        )
        return None
