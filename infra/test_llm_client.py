
import asyncio
import os
import sys

# Add project root to sys.path to ensure imports work
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "diagnostic_api"))

from app.expert.client import ExpertLLMClient
from app.expert.schemas import LLMDiagnosisResponse

async def main():
    print("Initializing ExpertLLMClient...")
    # Ensure this URL matches your internal docker network or localhost port
    # If running from host: http://localhost:11434/v1
    # If running inside docker: http://ollama:11434/v1
    client = ExpertLLMClient(base_url="http://localhost:11434/v1") 
    
    print("Generating diagnosis for test case: 2015 Ford F-150 Misfire")
    
    vehicle_info = "2015 Ford F-150, 3.5L EcoBoost, 120k miles"
    symptoms = "Customer reports rough idle and check engine light blinking. Code P0300 Random Misfire detected."
    context = """
    [Retrieved Manual Section: Ignition System]
    Common causes for P0300 on 3.5L EcoBoost:
    1. Worn Spark Plugs (Gap usually expands over time).
    2. Carbon Buildup on Intake Valves.
    3. Cracked Porcelain on Spark Plugs due to high heat.
    4. Ignition Coil failure (COP).
    
    [TSB 20-2342]
    Addressed moisture accumulation in intercooler causing misfire on hard acceleration.
    """
    
    try:
        diagnosis = await client.generate_diagnosis(vehicle_info, symptoms, context)
        
        print("\n--- Diagnosis Generated Successfully ---")
        print(f"Summary: {diagnosis.summary}")
        print(f"Requires Human Review: {diagnosis.requires_human_review}")
        print("\nSubsystem Risks:")
        for risk in diagnosis.subsystem_risks:
            print(f"- {risk.subsystem_name}: {risk.risk_level} (Conf: {risk.confidence})")
            print(f"  Reasoning: {risk.reasoning}")
            
        print("\nRecommendations:")
        for rec in diagnosis.recommendations:
            print(f"- {rec}")
            
        print("\nCitations:")
        for cit in diagnosis.citations:
            print(f"- {cit.doc_id}: {cit.text_snippet[:50]}...")

    except Exception as e:
        print(f"\n[ERROR] Failed to generate diagnosis: {e}")

if __name__ == "__main__":
    asyncio.run(main())
