# STF AI Diagnosis Platform - Quick Reference

**Author:** Li-Ta Hsu | **Date:** January 2026

---

## 🚀 Quick Start (3 Steps)

```bash
# 1. Setup environment
cd infra && cp .env.example .env && nano .env  # Set passwords

# 2. Start services
make init && make up

# 3. Verify
make health && make smoke-test
```

---

## 📦 Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| **Dify Web UI** | http://127.0.0.1:3000 | Main interface |
| **Diagnostic API** | http://127.0.0.1:8000 | Backend API |
| **API Docs** | http://127.0.0.1:8000/docs | Interactive API docs |
| **Weaviate** | http://127.0.0.1:8080 | Vector store |
| **Ollama** | http://127.0.0.1:11434 | LLM inference |
| **Dify API** | http://127.0.0.1:5001 | Workflow engine |

---

## 🔧 Essential Commands

```bash
# Start/Stop
make up              # Start all services
make down            # Stop all services
make restart         # Restart all services

# Monitor
make ps              # Show container status
make logs            # View all logs (Ctrl+C to exit)
make logs-api        # View diagnostic API logs only
make health          # Check service health
make smoke-test      # Run comprehensive tests

# Database Access
make exec-postgres   # Open PostgreSQL shell
make exec-redis      # Open Redis CLI

# LLM Management
make ollama-pull     # Pull default model (llama3:8b)
make ollama-list     # List installed models

# Maintenance
make backup          # Create backup
make clean           # Stop and remove containers (keep data)
make reset-volumes   # ⚠️ DELETE ALL DATA (requires confirmation)
```

---

## 🔐 Required Secrets (.env)

```bash
POSTGRES_PASSWORD=<strong-password>
REDIS_PASSWORD=<strong-password>
WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS=<api-key>
DIFY_SECRET_KEY=<secret-key>
APP_DB_PASSWORD=<strong-password>
```

**Generate strong passwords:**
```bash
openssl rand -base64 32
```

---

## 🧪 Testing Endpoints

```bash
# Health check
curl http://127.0.0.1:8000/health | jq

# Diagnostic request
curl -X POST http://127.0.0.1:8000/v1/vehicle/diagnose \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_id": "V001",
    "time_range": {
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-01-22T00:00:00Z"
    }
  }' | jq

# RAG retrieval
curl -X POST http://127.0.0.1:8000/v1/rag/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query": "P0171 fault code",
    "top_k": 3
  }' | jq
```

---

## ⚠️ Common Issues

| Issue | Solution |
|-------|----------|
| Port already in use | Change port in `.env` (e.g., `DIFY_WEB_PORT=3001`) |
| Out of memory | Increase Docker resources (Settings → Resources) |
| Database connection failed | Check `.env` passwords match, run `make restart` |
| Ollama model not found | Run `make ollama-pull` (takes 10-30 minutes) |
| Container unhealthy | Check logs: `make logs` or `make logs-api` |

**Full troubleshooting:** See `README_LOCAL_SETUP.md`

---

## 📊 System Requirements

**Minimum:** 16 GB RAM, 4 cores, 50 GB disk  
**Recommended:** 32 GB RAM, 8 cores, 100 GB SSD, NVIDIA GPU (12GB+)

---

## 📚 Documentation

- **Setup:** `infra/README_LOCAL_SETUP.md` (700+ lines)
- **Security:** `docs/SECURITY_BASELINE.md` (800+ lines)
- **Summary:** `infra/DEPLOYMENT_SUMMARY.md` (400+ lines)
- **This file:** Quick reference for daily use

---

## 🔍 Health Check Checklist

```bash
✓ make ps          # All containers running?
✓ make health      # All services healthy?
✓ make smoke-test  # All tests passing?
✓ make ollama-list # Model downloaded?
```

---

## 🛡️ Security Checklist

- [ ] `.env` file created with strong passwords
- [ ] `.env` NOT committed to git (`git status` to verify)
- [ ] All services bind to 127.0.0.1 (not 0.0.0.0)
- [ ] Backups created and stored securely
- [ ] Logs reviewed for errors

---

## 🎯 Next Steps (Phase 1.1)

1. **Ingest RAG data:** Prepare and ingest SOPs/manuals into Weaviate
2. **Configure Dify workflow:** Create diagnostic workflow in UI
3. **Implement full diagnostic API:** Connect to Weaviate and Ollama
4. **End-to-end testing:** Test complete flow from UI to response

---

## 💡 Pro Tips

- Use `make logs -f` to follow logs in real-time
- Run `make smoke-test` after every restart
- Back up before major changes: `make backup`
- Check disk space: `docker system df`
- Clean unused images: `docker image prune`

---

**Questions?** See `infra/README_LOCAL_SETUP.md` or contact: Li-Ta Hsu
