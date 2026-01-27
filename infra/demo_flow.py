import requests
import sys
import time
import subprocess
import json
import os
import argparse

# Configuration
API_URL = "http://localhost:8000"
DOCKER_COMPOSE_CMD = ["docker-compose", "-f", "infra/docker-compose.yml", "exec", "-T", "diagnostic-api"]
INGEST_CMD = ["python", "-m", "app.rag.ingest", "--dir", "/app/data"]
REPORT_CMD = ["python", "-m", "app.scripts.generate_daily_report", "--output", "daily_report_demo.json"]
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"

parser = argparse.ArgumentParser()
parser.add_argument("--auto", action="store_true", help="Run without user interaction")
args = parser.parse_args()

def log(msg, color=RESET):
    print(f"{color}{msg}{RESET}")

def step(title):
    print(f"\n{YELLOW}=== STEP: {title} ==={RESET}")
    if not args.auto:
        input("Press Enter to continue...")

def check_health():
    log("Checking API health...", YELLOW)
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        if r.status_code == 200:
            log("API is healthy.", GREEN)
            return True
    except requests.exceptions.ConnectionError:
        pass
    log("API is NOT running. Please start the stack with 'docker-compose up -d'.", RED)
    return False

def run_ingestion():
    log("Running RAG Ingestion...", YELLOW)
    try:
        subprocess.run(DOCKER_COMPOSE_CMD + INGEST_CMD, check=True)
        log("Ingestion complete.", GREEN)
    except subprocess.CalledProcessError:
        log("Ingestion failed. Ensure docker is running.", RED)
        sys.exit(1)

def run_diagnosis():
    log("Submitting Diagnosis Request...", YELLOW)
    # Corrected Endpoint: /v1/vehicle/diagnose (Stateful, returns session_id)
    # Corrected Payload: Includes all required fields
    payload = {
        "vehicle_id": "1FTEW1EF5KFA12345", 
        "make": "Ford",
        "model": "F-150",
        "year": 2015,
        "mileage": 125000,
        "time_range": {"start": "2026-01-01", "end": "2026-01-26"},
        "symptoms": "rough idle and blinking check engine light. P0300 code stored. Contact: 555-0199.",
        "include_evidence": False
    }
    
    try:
        r = requests.post(f"{API_URL}/v1/vehicle/diagnose", json=payload)
        try:
             r.raise_for_status()
        except:
             log(f"Request Error: {r.status_code} {r.text}", RED)
             sys.exit(1)
             
        data = r.json()
        
        session_id = data.get('session_id')
        log(f"\n[SUCCESS] Diagnosis Session ID: {session_id}", GREEN)
        
        # Note: The response structure depends on phase implementation (mock vs real)
        # Assuming typical response has list of risks/recommendations
        risks = data.get("subsystem_risks", [])
        log(f"Risks Identified: {len(risks)}")
        for risk in risks:
             log(f"- {risk.get('subsystem_name')}: {risk.get('predicted_faults')}")
             
        recommendations = data.get("recommendations", [])
        log(f"Recommendations: {recommendations}")
             
        return session_id
        
    except Exception as e:
        log(f"Diagnosis request failed: {e}", RED)
        sys.exit(1)

def submit_feedback(session_id):
    log(f"Submitting Feedback for {session_id}...", YELLOW)
    payload = {
        "session_id": session_id,
        "rating": 5,
        "is_helpful": True,
        "comments": "Spot on diagnosis. Replaced coil 3.",
        "corrected_diagnosis": None
    }
    
    try:
        r = requests.post(f"{API_URL}/v1/feedback/", json=payload)
        r.raise_for_status()
        log("Feedback submitted successfully.", GREEN)
    except Exception as e:
        log(f"Feedback submission failed: {e}", RED)
        if hasattr(e, 'response') and e.response:
             log(f"Response: {e.response.text}", RED)

def generate_report():
    log("Triggering Daily Report via Script...", YELLOW)
    try:
        # Run report generation inside container
        subprocess.run(DOCKER_COMPOSE_CMD + REPORT_CMD, check=True)
        log(f"Report Generated!", GREEN)
             
    except subprocess.CalledProcessError as e:
        log(f"Report generation script failed", RED)

def main():
    print(f"{GREEN}STF AI Diagnosis Platform - Demo Script{RESET}")
    
    if not check_health():
        sys.exit(1)
        
    step("1. Knowledge Base Ingestion")
    run_ingestion()
    
    step("2. AI Diagnosis (API)")
    session_id = run_diagnosis()
    
    step("3. Technician Feedback")
    if session_id:
        submit_feedback(session_id)
        
    step("4. Daily Reporting")
    generate_report()
    
    step("5. Dify Workflow")
    log("You can also try the visual workflow at: http://localhost:3000", GREEN)
    log("Use the 'Auto Diagnosis Expert' app.", GREEN)
    
    log("\n[DEMO COMPLETE]", GREEN)

if __name__ == "__main__":
    main()
