"""Validation logic for Expert Model output."""

import json
import re
from typing import Optional
from pydantic import ValidationError
from app.expert.schemas import LLMDiagnosisResponse

def validate_llm_output(raw_text: str) -> Optional[LLMDiagnosisResponse]:
    """
    Parse and validate the raw text response from the LLM.
    
    Handles:
    - Markdown code block stripping
    - JSON parsing
    - Pydantic schema validation
    """
    clean_text = raw_text.strip()
    
    # 1. Strip Markdown code blocks if present
    # Look for ```json ... ``` or just ``` ... ```
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, clean_text)
    if match:
        clean_text = match.group(1)
        
    # 2. Parse JSON
    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        print(f"Validation Error: Invalid JSON format. {e}")
        # In a real system, we might retry the LLM call here.
        return None
        
    # 3. Pydantic Validation
    try:
        model = LLMDiagnosisResponse(**data)
        return model
    except ValidationError as e:
        print(f"Validation Error: Schema mismatch. {e}")
        return None
    except Exception as e:
        print(f"Validation Error: Unexpected error. {e}")
        return None
