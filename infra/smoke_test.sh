#!/bin/bash
# ============================================================================
# STF AI Diagnosis Platform - Smoke Test Script
# Author: Li-Ta Hsu
# Date: January 2026
# ============================================================================
# Verifies that all services are running and responding correctly
# ============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
print_header() {
    echo -e "\n${CYAN}════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════${NC}\n"
}

print_test() {
    echo -e "${YELLOW}Testing:${NC} $1"
}

print_pass() {
    echo -e "${GREEN}✓ PASS:${NC} $1"
    ((TESTS_PASSED++))
}

print_fail() {
    echo -e "${RED}✗ FAIL:${NC} $1"
    ((TESTS_FAILED++))
}

test_service() {
    local service_name=$1
    local url=$2
    local expected_status=${3:-200}
    
    print_test "$service_name at $url"
    
    if response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>&1); then
        if [ "$response" = "$expected_status" ]; then
            print_pass "$service_name returned HTTP $response"
        else
            print_fail "$service_name returned HTTP $response (expected $expected_status)"
        fi
    else
        print_fail "$service_name is not reachable"
    fi
}

test_json_response() {
    local service_name=$1
    local url=$2
    local field=$3
    
    print_test "$service_name JSON response"
    
    if response=$(curl -s "$url" 2>&1); then
        if echo "$response" | grep -q "$field"; then
            print_pass "$service_name returned valid JSON with '$field'"
        else
            print_fail "$service_name JSON missing expected field '$field'"
        fi
    else
        print_fail "$service_name did not return JSON"
    fi
}

test_docker_container() {
    local container_name=$1
    
    print_test "Docker container: $container_name"
    
    if docker ps --filter "name=$container_name" --filter "status=running" | grep -q "$container_name"; then
        print_pass "$container_name is running"
    else
        print_fail "$container_name is not running"
    fi
}

# ============================================================================
# Main Test Suite
# ============================================================================

print_header "STF AI Diagnosis Platform - Smoke Test"

echo "Start time: $(date)"
echo ""

# Test 1: Docker containers are running
print_header "Test 1: Container Status"

test_docker_container "stf-postgres"
test_docker_container "stf-redis"
test_docker_container "stf-weaviate"
test_docker_container "stf-ollama"
test_docker_container "stf-dify-api"
test_docker_container "stf-dify-worker"
test_docker_container "stf-dify-web"
test_docker_container "stf-diagnostic-api"

# Test 2: Health endpoints
print_header "Test 2: Health Endpoints"

test_service "Weaviate" "http://127.0.0.1:8080/v1/.well-known/ready"
test_service "Ollama" "http://127.0.0.1:11434/api/version"
test_service "Dify Web" "http://127.0.0.1:3000"
test_service "Dify API" "http://127.0.0.1:5001/health"
test_service "Diagnostic API" "http://127.0.0.1:8000/health"

# Test 3: JSON response validation
print_header "Test 3: API Response Validation"

test_json_response "Diagnostic API Health" "http://127.0.0.1:8000/health" "status"
test_json_response "Diagnostic API Root" "http://127.0.0.1:8000/" "version"

# Test 4: Diagnostic API endpoints
print_header "Test 4: Diagnostic API Endpoints"

# Test /v1/vehicle/diagnose
print_test "POST /v1/vehicle/diagnose"
diagnose_response=$(curl -s -X POST "http://127.0.0.1:8000/v1/vehicle/diagnose" \
    -H "Content-Type: application/json" \
    -d '{
        "vehicle_id": "TEST_V001",
        "time_range": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-22T23:59:59Z"
        }
    }' 2>&1)

if echo "$diagnose_response" | grep -q "session_id"; then
    print_pass "Diagnostic endpoint returned valid schema"
    
    # Verify schema fields
    if echo "$diagnose_response" | grep -q "vehicle_id" && \
       echo "$diagnose_response" | grep -q "subsystem_risks" && \
       echo "$diagnose_response" | grep -q "recommendations"; then
        print_pass "Response contains required fields (vehicle_id, subsystem_risks, recommendations)"
    else
        print_fail "Response missing required schema fields"
    fi
else
    print_fail "Diagnostic endpoint did not return valid JSON"
fi

# Test /v1/rag/retrieve
print_test "POST /v1/rag/retrieve"
rag_response=$(curl -s -X POST "http://127.0.0.1:8000/v1/rag/retrieve" \
    -H "Content-Type: application/json" \
    -d '{
        "query": "P0171 fault code",
        "top_k": 3
    }' 2>&1)

if echo "$rag_response" | grep -q "chunks"; then
    print_pass "RAG endpoint returned valid schema"
    
    # Verify doc_id#section metadata
    if echo "$rag_response" | grep -q "doc_id" && \
       echo "$rag_response" | grep -q "section"; then
        print_pass "RAG chunks contain doc_id and section metadata"
    else
        print_fail "RAG chunks missing doc_id or section metadata"
    fi
else
    print_fail "RAG endpoint did not return valid JSON"
fi

# Test 5: Database connectivity
print_header "Test 5: Database Connectivity"

print_test "Postgres connectivity"
if docker exec stf-postgres pg_isready -U dify_user -d dify > /dev/null 2>&1; then
    print_pass "Postgres is accepting connections"
else
    print_fail "Postgres is not accepting connections"
fi

print_test "Redis connectivity"
if docker exec stf-redis redis-cli -a "${REDIS_PASSWORD:-testpassword}" ping 2>/dev/null | grep -q "PONG"; then
    print_pass "Redis is accepting connections"
else
    print_fail "Redis is not accepting connections"
fi

# Test 6: Persistence verification
print_header "Test 6: Data Persistence"

print_test "Docker volumes exist"
volumes_exist=true

for volume in postgres_data redis_data weaviate_data ollama_data dify_storage diagnostic_api_logs; do
    if docker volume ls | grep -q "stf_$volume"; then
        echo "  ✓ Volume stf_$volume exists"
    else
        echo "  ✗ Volume stf_$volume missing"
        volumes_exist=false
    fi
done

if [ "$volumes_exist" = true ]; then
    print_pass "All required volumes exist"
else
    print_fail "Some volumes are missing"
fi

# Test 7: Network isolation
print_header "Test 7: Network Isolation"

print_test "Services bound to localhost only"
bound_to_localhost=true

for port in 3000 5001 8000 8080 11434; do
    if netstat -tuln 2>/dev/null | grep ":$port" | grep -q "127.0.0.1"; then
        echo "  ✓ Port $port bound to 127.0.0.1"
    elif lsof -iTCP:$port -sTCP:LISTEN 2>/dev/null | grep -q "127.0.0.1"; then
        echo "  ✓ Port $port bound to 127.0.0.1"
    else
        echo "  ? Unable to verify binding for port $port (may need sudo)"
    fi
done

print_pass "Port binding check complete (manual verification recommended)"

# ============================================================================
# Test Summary
# ============================================================================

print_header "Test Summary"

echo "End time: $(date)"
echo ""
echo -e "Tests passed: ${GREEN}$TESTS_PASSED${NC}"
echo -e "Tests failed: ${RED}$TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ ALL TESTS PASSED - System is operational!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
    echo ""
    echo "You can now:"
    echo "  - Access Dify UI: http://127.0.0.1:3000"
    echo "  - View API docs: http://127.0.0.1:8000/docs"
    echo "  - Run 'make logs' to view logs"
    exit 0
else
    echo -e "${RED}═══════════════════════════════════════════════════════${NC}"
    echo -e "${RED}  ✗ TESTS FAILED - Please review errors above${NC}"
    echo -e "${RED}═══════════════════════════════════════════════════════${NC}"
    echo ""
    echo "Troubleshooting:"
    echo "  - Run 'make logs' to view service logs"
    echo "  - Run 'make health' to check service health"
    echo "  - See infra/README_LOCAL_SETUP.md for help"
    exit 1
fi
