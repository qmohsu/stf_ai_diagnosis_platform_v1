
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_end_to_end_diagnosis():
    print(f"Testing End-to-End Diagnosis against {BASE_URL}...")
    
    # Payload with PII to test redaction + symptoms that match our manual chunks
    payload = {
        "vehicle_id": "TEST-VIN-12345",
        "make": "Ford",
        "model": "F-150",
        "year": 2015,
        "mileage": 120000,
        "symptoms": "Customer called 555-0199 reporting rough idle and blinking check engine light. P0300 code present.",
        "dtc_codes": ["P0300"]
    }

    try:
        start_time = time.time()
        response = requests.post(
            f"{BASE_URL}/v1/diagnose/",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        duration = time.time() - start_time
        
        print(f"Request took {duration:.2f} seconds")
        
        if response.status_code == 200:
            data = response.json()
            print("\n[SUCCESS] Diagnosis Generated!")
            
            # Verify Redaction
            print(f"Redacted Symptoms: {data['redacted_symptoms']}")
            assert "555-0199" not in data['redacted_symptoms'], "PII was not redacted!"
            assert "[PHONE_REDACTED]" in data['redacted_symptoms'], "Redaction marker missing!"
            
            # Verify Context Usage
            print(f"Context Used: {data['context_used']}")
            
            # Verify Diagnosis Structure
            diag = data['diagnosis']
            print("\n--- Diagnostic Report ---")
            print(f"Summary: {diag['summary']}")
            print(f"Risks Found: {len(diag['subsystem_risks'])}")
            for risk in diag['subsystem_risks']:
                print(f"- {risk['subsystem_name']}: {risk['risk_level']}")
                
            print(f"Citations: {len(diag['citations'])}")
            
        else:
            print(f"\n[FAILED] Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"\n[ERROR] Connection failed: {e}")

if __name__ == "__main__":
    # Wait a bit for the server to reload if needed (local dev)
    time.sleep(2)
    test_end_to_end_diagnosis()
