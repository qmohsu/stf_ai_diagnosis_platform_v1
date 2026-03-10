"""Verification script for APP-05 (Expert Prompts & Schemas)."""

import sys
from app.expert.prompts import USER_PROMPT_TEMPLATE


def test_prompt_formatting():
    print("\nTesting Prompt Formatting...")
    try:
        prompt = USER_PROMPT_TEMPLATE.format(
            vehicle_info="2020 Toyota Camry",
            symptoms="Stalling at idle",
            context="Manual says check IAC valve."
        )
        if "2020 Toyota Camry" in prompt and "check IAC valve" in prompt:
            print("PASS: Prompt formatting works.")
        else:
            print("FAIL: Prompt formatting failed.")
            sys.exit(1)
    except Exception as e:
        print(f"FAIL: Prompt formatting raised error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_prompt_formatting()
