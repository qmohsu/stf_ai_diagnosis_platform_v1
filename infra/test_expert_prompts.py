"""Verification script for APP-05 (Expert Prompts & Schemas)."""

import sys
import json
from app.expert.schemas import LLMDiagnosisResponse
from app.expert.validate import validate_llm_output
from app.expert.prompts import USER_PROMPT_TEMPLATE

def test_validation_logic():
    print("Testing Validation Logic...")
    
    # 1. Test Valid JSON wrapped in Markdown
    valid_response = """
    Here is the diagnosis:
    ```json
    {
      "summary": "Mass Air Flow sensor failure detected.",
      "subsystem_risks": [
        {
          "subsystem_name": "Fuel System",
          "risk_level": 0.9,
          "confidence": 0.95,
          "predicted_faults": ["P0101"],
          "reasoning": "Sensor readings are out of range."
        }
      ],
      "recommendations": ["Inspect MAF sensor"],
      "citations": [],
      "requires_human_review": false
    }
    ```
    """
    
    result = validate_llm_output(valid_response)
    if result and isinstance(result, LLMDiagnosisResponse):
        print("✅ Correctly parsed valid Markdown-JSON.")
    else:
        print("❌ Failed to parse valid Markdown-JSON.")
        sys.exit(1)

    # 2. Test Invalid Schema (missing fields)
    invalid_response = """
    ```json
    {
      "summary": "Bad response",
      "recommendations": []
    }
    ```
    """
    result = validate_llm_output(invalid_response)
    if result is None:
        print("✅ Correctly rejected invalid schema (missing fields).")
    else:
        print("❌ Failed to reject invalid schema.")
        sys.exit(1)
        
    print("Validation Logic Tests Passed!")

def test_prompt_formatting():
    print("\nTesting Prompt Formatting...")
    try:
        prompt = USER_PROMPT_TEMPLATE.format(
            vehicle_info="2020 Toyota Camry",
            symptoms="Stalling at idle",
            context="Manual says check IAC valve."
        )
        if "2020 Toyota Camry" in prompt and "check IAC valve" in prompt:
            print("✅ Prompt formatting works.")
        else:
            print("❌ Prompt formatting failed.")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Prompt formatting raised error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_validation_logic()
    test_prompt_formatting()
