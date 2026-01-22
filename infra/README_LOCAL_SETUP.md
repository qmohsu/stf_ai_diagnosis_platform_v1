# STF AI Diagnosis Platform - Local Setup Guide

**Author:** Li-Ta Hsu  
**Date:** January 2026  
**Version:** 1.0.0

---

## Table of Contents

1. [Overview](#overview)
2. [System Requirements](#system-requirements)
3. [Prerequisites](#prerequisites)
4. [Quick Start](#quick-start)
5. [Detailed Setup](#detailed-setup)
6. [Service Access](#service-access)
7. [Verification & Testing](#verification--testing)
8. [Common Operations](#common-operations)
9. [Troubleshooting](#troubleshooting)
10. [Next Steps](#next-steps)

---

## Overview

This guide provides step-by-step instructions to deploy the STF AI Diagnosis Platform on a local laptop for Phase 1 pilot testing. The platform includes:

- **Dify** (workflow orchestration + UI)
- **Ollama** (local LLM inference)
- **Weaviate** (vector store for RAG)
- **Diagnostic API** (FastAPI backend)
- **Postgres** (database)
- **Redis** (cache and message broker)

All services run locally with **no external API calls** and are bound to `127.0.0.1` for security.

---

## System Requirements

### Minimum Requirements

- **OS:** Linux (Ubuntu 22.04+), macOS (12+), or Windows 10/11 with WSL2
- **RAM:** 16 GB (recommended: 32 GB)
- **CPU:** 4 cores (recommended: 8 cores)
- **Storage:** 50 GB free disk space
- **GPU:** Optional (NVIDIA GPU with CUDA support for faster inference)

### Recommended Specifications

- **RAM:** 32 GB or more
- **CPU:** 8+ cores (Intel i7/i9 or AMD Ryzen 7/9)
- **Storage:** SSD with 100+ GB free space
- **GPU:** NVIDIA RTX 3060 or better (12GB+ VRAM)

### Performance Notes

- **CPU-only inference** is supported but will be slower (~5-10 seconds per query)
- **GPU inference** significantly improves performance (~1-2 seconds per query)
- Adjust resource limits in `.env` based on your hardware

---

## Prerequisites

### 1. Install Docker

**Linux (Ubuntu/Debian):**
```bash
# Install Docker Engine
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Verify installation
docker --version
docker-compose --version
```

**macOS:**
```bash
# Install Docker Desktop for Mac
# Download from: https://www.docker.com/products/docker-desktop

# Verify installation
docker --version
docker-compose --version
```

**Windows (WSL2):**
```powershell
# Install Docker Desktop for Windows
# Download from: https://www.docker.com/products/docker-desktop

# Enable WSL2 integration in Docker Desktop settings
# Verify installation in WSL2 terminal:
docker --version
docker-compose --version
```

### 2. Install Git

```bash
# Linux
sudo apt-get update && sudo apt-get install -y git

# macOS
brew install git

# Windows (WSL2)
sudo apt-get update && sudo apt-get install -y git
```

### 3. Install Python 3.11+ (Optional for development)

```bash
# Linux
sudo apt-get update && sudo apt-get install -y python3.11 python3-pip

# macOS
brew install python@3.11

# Verify
python3 --version
```

### 4. Configure Docker Resources

Ensure Docker has sufficient resources allocated:

- **Docker Desktop:** Go to Settings → Resources
  - **CPUs:** 4+ cores
  - **Memory:** 8+ GB
  - **Swap:** 2+ GB
  - **Disk:** 50+ GB

---

## Quick Start

### 1. Clone the Repository

```bash
cd ~/projects  # or your preferred directory
git clone <repository-url> stf_ai_diagnosis_platform_v1
cd stf_ai_diagnosis_platform_v1
```

### 2. Configure Environment

```bash
cd infra

# Copy environment template
cp .env.example .env

# Edit .env and set passwords (required!)
nano .env  # or use your preferred editor
```

**Required changes in `.env`:**
```bash
# Set strong passwords (replace CHANGE_ME_* values)
POSTGRES_PASSWORD=your_strong_postgres_password_here
REDIS_PASSWORD=your_strong_redis_password_here
WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS=your_weaviate_api_key_here
DIFY_SECRET_KEY=your_dify_secret_key_here
APP_DB_PASSWORD=your_app_db_password_here
```

### 3. Initialize and Start

```bash
# Initialize (pull images, build custom images)
make init

# Start all services
make up

# This will start:
# - Postgres
# - Redis
# - Weaviate
# - Ollama
# - Dify (api, worker, web)
# - Diagnostic API
```

### 4. Verify Services

```bash
# Check container status
make ps

# Run health checks
make health

# View logs
make logs
```

### 5. Pull Ollama Model

```bash
# Pull the default model (llama3:8b)
# This may take 10-30 minutes depending on your connection
make ollama-pull

# Verify model is available
make ollama-list
```

### 6. Access the Platform

Once all services are healthy, access:

- **Dify Web UI:** http://127.0.0.1:3000
- **Diagnostic API:** http://127.0.0.1:8000
- **API Docs:** http://127.0.0.1:8000/docs

---

## Detailed Setup

### Step 1: Environment Configuration

The `.env` file contains all configuration for the stack. Key sections:

#### Network Configuration
```bash
BIND_HOST=127.0.0.1  # Bind to localhost only (security)
DIFY_WEB_PORT=3000
DIFY_API_PORT=5001
DIAGNOSTIC_API_PORT=8000
WEAVIATE_PORT=8080
```

#### Database Configuration
```bash
POSTGRES_VERSION=15.6-alpine
POSTGRES_DB=dify
POSTGRES_USER=dify_user
POSTGRES_PASSWORD=<set-strong-password>

APP_DB_NAME=stf_diagnosis
APP_DB_USER=stf_app_user
APP_DB_PASSWORD=<set-strong-password>
```

#### Ollama Configuration
```bash
OLLAMA_VERSION=0.1.26
OLLAMA_DEFAULT_MODEL=llama3:8b
# For smaller laptops, consider: llama3:8b-q4_0 (quantized)
```

### Step 2: Resource Limits (Optional)

Edit `.env` to adjust for your hardware:

```bash
# For low-memory systems (8-16GB RAM)
POSTGRES_MAX_CONNECTIONS=50
REDIS_MAXMEMORY=256mb

# For high-memory systems (32GB+ RAM)
POSTGRES_MAX_CONNECTIONS=200
REDIS_MAXMEMORY=1gb
```

### Step 3: GPU Support (Linux Only)

If you have an NVIDIA GPU:

```bash
# Install NVIDIA Container Toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
    sudo tee /etc/apt/sources.list.d/nvidia-docker.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker

# Verify GPU access
docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi
```

Then uncomment GPU configuration in `docker-compose.yml` (ollama service).

### Step 4: Initialize Services

```bash
cd infra

# Pull all Docker images
make pull

# Build custom images (diagnostic-api)
make build

# Initialize database and volumes
make init
```

### Step 5: Start Services

```bash
# Start all services in detached mode
make up

# Services will start in dependency order:
# 1. Postgres, Redis
# 2. Weaviate
# 3. Ollama
# 4. Dify services
# 5. Diagnostic API
```

### Step 6: Monitor Startup

```bash
# Watch logs in real-time
make logs

# Or check specific services
make logs-api      # Diagnostic API logs
make logs-dify     # Dify services logs
make logs-ollama   # Ollama logs

# Check health status
make health
```

Expected output:
```
Service Health Check:

  ✓ postgres: healthy
  ✓ redis: healthy
  ✓ weaviate: healthy
  ✓ ollama: healthy
  ✓ dify-api: healthy
  ✓ dify-worker: healthy
  ✓ dify-web: healthy
  ✓ diagnostic-api: healthy
```

---

## Service Access

### Dify Web UI

**URL:** http://127.0.0.1:3000

**First-time Setup:**
1. Navigate to http://127.0.0.1:3000
2. Create admin account
3. Configure LLM provider:
   - Type: OpenAI-compatible
   - Endpoint: http://ollama:11434/v1
   - Model: llama3:8b
4. Configure vector store (Weaviate already connected)

### Diagnostic API

**URL:** http://127.0.0.1:8000

**Interactive Docs:** http://127.0.0.1:8000/docs

**Example Request:**
```bash
curl -X POST "http://127.0.0.1:8000/v1/vehicle/diagnose" \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_id": "V12345",
    "time_range": {
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-01-22T23:59:59Z"
    }
  }'
```

### Weaviate

**URL:** http://127.0.0.1:8080

**Health Check:**
```bash
curl http://127.0.0.1:8080/v1/.well-known/ready
```

### Ollama

**URL:** http://127.0.0.1:11434

**List Models:**
```bash
curl http://127.0.0.1:11434/api/tags
```

### Database Access

**Postgres:**
```bash
# Connect via docker exec
make exec-postgres

# Or use psql directly
PGPASSWORD=<your-password> psql -h 127.0.0.1 -U dify_user -d dify
```

**Redis:**
```bash
# Connect via docker exec
make exec-redis

# Or use redis-cli directly
redis-cli -h 127.0.0.1 -a <your-redis-password>
```

---

## Verification & Testing

### Automated Health Checks

```bash
# Run comprehensive health checks
make test-health

# Expected output:
# Testing Postgres...
# ✓ Postgres is ready
#
# Testing Redis...
# ✓ Redis is ready
#
# Testing Weaviate...
# ✓ Weaviate is ready
#
# Testing Ollama...
# ✓ Ollama is ready
#
# Testing Dify API...
# ✓ Dify API is ready
#
# Testing Diagnostic API...
# ✓ Diagnostic API is ready
```

### Manual Verification

#### 1. Test Diagnostic API
```bash
curl -s http://127.0.0.1:8000/health | jq
```

Expected:
```json
{
  "status": "healthy",
  "timestamp": "2026-01-22T10:00:00.000Z",
  "version": "0.1.0",
  "services": {
    "api": "healthy",
    "database": "healthy",
    "weaviate": "healthy",
    "llm": "healthy"
  }
}
```

#### 2. Test RAG Retrieval
```bash
curl -X POST "http://127.0.0.1:8000/v1/rag/retrieve" \
  -H "Content-Type: application/json" \
  -d '{"query": "P0171 fault code", "top_k": 3}' | jq
```

#### 3. Test Ollama
```bash
curl http://127.0.0.1:11434/api/generate -d '{
  "model": "llama3:8b",
  "prompt": "What is a vehicle diagnostic code?",
  "stream": false
}'
```

---

## Common Operations

### Starting and Stopping

```bash
# Start all services
make up

# Stop all services (keeps data)
make down

# Restart all services
make restart
```

### Viewing Logs

```bash
# All services
make logs

# Specific service
docker-compose logs -f <service-name>

# Example: diagnostic-api
docker-compose logs -f diagnostic-api
```

### Accessing Containers

```bash
# Diagnostic API shell
make shell-api

# Dify API shell
make shell-dify

# Postgres psql
make exec-postgres

# Redis CLI
make exec-redis
```

### Managing Ollama Models

```bash
# Pull a model
make ollama-pull

# List installed models
make ollama-list

# Pull a different model
docker exec stf-ollama ollama pull mistral:7b

# Remove a model
docker exec stf-ollama ollama rm <model-name>
```

### Backup and Restore

```bash
# Create backup
make backup

# Backups saved to infra/backups/ with timestamp

# Restore from backup (manual)
# 1. Stop services
make down

# 2. Remove old volumes
docker volume rm stf_postgres_data

# 3. Restore volume
docker run --rm -v stf_postgres_data:/data -v $(pwd)/backups:/backup \
  alpine tar xzf /backup/postgres_YYYYMMDD_HHMMSS.tar.gz -C /data

# 4. Restart services
make up
```

### Cleanup

```bash
# Stop and remove containers (keeps volumes)
make clean

# DANGER: Remove all data volumes
make reset-volumes
# Type 'DELETE' to confirm
```

---

## Troubleshooting

### Issue: Containers fail to start

**Check Docker resources:**
```bash
docker info | grep -i memory
docker info | grep -i cpus
```

**Solution:** Increase Docker resources in Docker Desktop settings.

---

### Issue: Port already in use

**Error:** `Bind for 0.0.0.0:3000 failed: port is already allocated`

**Check what's using the port:**
```bash
# Linux/macOS
lsof -i :3000

# Windows
netstat -ano | findstr :3000
```

**Solution:** Either stop the conflicting service or change the port in `.env`:
```bash
DIFY_WEB_PORT=3001  # Use different port
```

---

### Issue: Ollama model download is slow

**Check download progress:**
```bash
docker-compose logs -f ollama
```

**Solutions:**
- Use a smaller/quantized model: `llama3:8b-q4_0`
- Download on a faster network
- Resume interrupted downloads (Ollama handles this automatically)

---

### Issue: Out of memory errors

**Symptoms:** Containers crash or services become unresponsive

**Check resource usage:**
```bash
docker stats
```

**Solutions:**
1. Use a smaller LLM model
2. Reduce Postgres max_connections
3. Reduce Redis maxmemory
4. Stop unused services temporarily

---

### Issue: Database connection errors

**Error:** `FATAL: password authentication failed`

**Check environment variables:**
```bash
docker-compose config | grep POSTGRES_PASSWORD
```

**Solution:** Ensure `.env` file has correct passwords and restart:
```bash
make down
make up
```

---

### Issue: Weaviate not responding

**Check Weaviate logs:**
```bash
docker-compose logs weaviate
```

**Common causes:**
- Insufficient memory
- Corrupted data volume

**Solution:**
```bash
# Restart Weaviate
docker-compose restart weaviate

# If persistent, reset Weaviate data
make down
docker volume rm stf_weaviate_data
make up
```

---

### Issue: Diagnostic API health check fails

**Check logs:**
```bash
make logs-api
```

**Common causes:**
- Database not ready
- Weaviate not accessible
- Configuration errors

**Solution:**
```bash
# Check service dependencies
make health

# Restart diagnostic-api
docker-compose restart diagnostic-api
```

---

### Issue: GPU not detected (Linux)

**Check GPU access:**
```bash
docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi
```

**Solution:**
1. Ensure NVIDIA drivers are installed
2. Install nvidia-container-toolkit
3. Restart Docker daemon
4. Uncomment GPU config in docker-compose.yml

---

### Issue: Services start but Dify UI shows errors

**Check Dify API logs:**
```bash
make logs-dify
```

**Common causes:**
- Database migrations pending
- Secret key mismatch
- Vector store not configured

**Solution:**
```bash
# Recreate Dify containers
docker-compose up -d --force-recreate dify-api dify-worker dify-web
```

---

### Getting Help

If you encounter issues not covered here:

1. **Check logs:** `make logs` or `docker-compose logs <service>`
2. **Verify health:** `make health` and `make test-health`
3. **Check GitHub Issues:** Search for similar problems
4. **Collect diagnostics:**
   ```bash
   # Save logs for all services
   docker-compose logs > debug_logs.txt
   
   # Save system info
   docker info > debug_info.txt
   docker-compose ps > debug_ps.txt
   ```

---

## Next Steps

### 1. Configure Dify Workflows

- Create diagnostic workflow in Dify UI
- Configure LLM prompts
- Connect to diagnostic API endpoints
- Test end-to-end flow

### 2. Ingest RAG Data

See the RAG ingestion guide (to be created in Phase 1.1) for:
- Preparing SOP documents
- Chunking and embedding
- Ingesting into Weaviate

### 3. Development Setup

For local development of diagnostic_api:

```bash
cd diagnostic_api

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Run locally (connects to Docker services)
uvicorn app.main:app --reload --port 8001
```

### 4. Review Security Baseline

Read `SECURITY_BASELINE.md` for:
- Network isolation rules
- Secret management
- Backup procedures
- Phase 2 security enhancements

---

## Acceptance Checklist

✅ All prerequisites installed  
✅ `.env` configured with strong passwords  
✅ `make init` completed successfully  
✅ `make up` starts all services  
✅ `make health` shows all services healthy  
✅ Ollama model downloaded  
✅ Dify UI accessible and admin account created  
✅ Diagnostic API returns valid responses  
✅ RAG retrieval returns chunks with citations  

---

**Congratulations!** Your STF AI Diagnosis Platform is ready for Phase 1 pilot testing.

For questions or issues, refer to the troubleshooting section or contact the development team.
