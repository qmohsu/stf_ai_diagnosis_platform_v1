
import requests
import json
import time

URL = "http://localhost:8000/v1/vehicle/diagnose"

payload = {
    "vehicle_id": "VIN1234567890",
    "time_range": {
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-31T23:59:59Z"
    },
    "subsystems": ["powertrain"],
    "include_evidence": True
}

print(f"Sending request to {URL}...")
try:
    start_time = time.time()
    response = requests.post(URL, json=payload)
    duration = time.time() - start_time
    
    print(f"Status Code: {response.status_code}")
    print(f"Duration: {duration:.2f}s")
    
    if response.status_code == 200:
        data = response.json()
        print("\nResponse:")
        print(json.dumps(data, indent=2))
        print(f"\nSession ID: {data.get('session_id')}")
    else:
        print("\nError Response:")
        print(response.text)

except Exception as e:
    print(f"Request failed: {e}")
