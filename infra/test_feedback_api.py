
import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_feedback_api():
    print(f"Testing Feedback API against {BASE_URL}...")
    
    # 1. Create a Diagnostic Session
    print("\n[1] Creating Diagnostic Session...")
    payload = {
        "vehicle_id": "TEST-VIN-FEEDBACK",
        "make": "Toyota",
        "model": "Camry",
        "year": 2020,
        "mileage": 50000,
        "time_range": {"start": "2023-01-01", "end": "2023-01-07"}, # Required by DiagnosticRequest
        "symptoms": "Brake squeal",
        "include_evidence": False
    }

    try:
        # Note: The diagnose endpoint in main.py is currently at /v1/vehicle/diagnose
        # but in main.py there is also app.include_router(diagnose.router, prefix="/v1/diagnose")
        # I should check which one I should use. 
        # main.py has @app.post("/v1/vehicle/diagnose", ...) directly.
        # And it also includes diagnose.router at /v1/diagnose.
        # Let's use the one that is guaranteed to return session_id. 
        # The main.py direct endpoint returns DiagnosticResponse which has session_id.
        
        response = requests.post(
            f"{BASE_URL}/v1/vehicle/diagnose",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code != 200:
            print(f"[FAILED] Diagnosis creation failed: {response.status_code}")
            print(response.text)
            return

        data = response.json()
        session_id = data.get("session_id")
        print(f"[SUCCESS] Session Created: {session_id}")
        
        if not session_id:
             print("[FAILED] No session_id returned")
             return

    
        # 2. Submit Feedback
        print("\n[2] Submitting Feedback...")
        feedback_payload = {
            "session_id": session_id,
            "rating": 5,
            "is_helpful": True,
            "comments": "Great diagnosis!",
            "corrected_diagnosis": None
        }
        
        fb_response = requests.post(
            f"{BASE_URL}/v1/feedback/",
            json=feedback_payload,
            headers={"Content-Type": "application/json"}
        )
        
        if fb_response.status_code == 201:
            fb_data = fb_response.json()
            print(f"[SUCCESS] Feedback Submitted! ID: {fb_data.get('feedback_id')}")
        else:
            print(f"[FAILED] Feedback submission failed: {fb_response.status_code}")
            print(fb_response.text)
            return

        # 3. Submit Duplicate Feedback (Expect 409)
        print("\n[3] Testing Duplicate Feedback (Expect 409)...")
        dup_response = requests.post(
            f"{BASE_URL}/v1/feedback/",
            json=feedback_payload,
            headers={"Content-Type": "application/json"}
        )
        
        if dup_response.status_code == 409:
            print("[SUCCESS] Duplicate rejected correctly.")
        else:
            print(f"[FAILED] Duplicate NOT rejected: {dup_response.status_code}")
            
    except Exception as e:
        print(f"\n[ERROR] Connection failed: {e}")

if __name__ == "__main__":
    time.sleep(1)
    test_feedback_api()
