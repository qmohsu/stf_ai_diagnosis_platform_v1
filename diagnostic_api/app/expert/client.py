"""Client for interacting with the Expert Model."""

from typing import Optional, Dict, Any
from app.expert.prompts import USER_PROMPT_TEMPLATE, SYSTEM_PROMPT
from app.expert.validate import validate_llm_output
from app.expert.schemas import LLMDiagnosisResponse

# TODO (APP-07): Implement actual LLM call via Ollama
# For APP-05, we just need the structure to be importable and testable

async def generate_diagnosis(
    vehicle_info: str,
    symptoms: str,
    context: str
) -> Optional[LLMDiagnosisResponse]:
    """
    Generate a diagnosis using the Expert Model.
    
    1. Formats the prompt.
    2. Calls LLM (Stubbed for now).
    3. Validates output.
    """
    # format prompt
    prompt = USER_PROMPT_TEMPLATE.format(
        vehicle_info=vehicle_info,
        symptoms=symptoms,
        context=context
    )
    
    # print(f"DEBUG: System Prompt: {SYSTEM_PROMPT}")
    # print(f"DEBUG: User Prompt: {prompt}")
    
    # Placeholder for actual LLM response
    # In APP-05 we are testing the Schema/Prompt/Validation layer
    # so we don't necessarily need to hit Ollama yet, 
    # but the infrastructure is ready.
    
    return None
