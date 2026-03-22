# PolyU Server Deployment Guide

> Last updated: 2026-03-21 | GitHub Issue: #21

## Table of Contents

1. [Server Overview](#server-overview)
2. [Network Architecture](#network-architecture)
3. [Prerequisites](#prerequisites)
4. [First-Time Setup](#first-time-setup)
5. [Updating Deployments](#updating-deployments)
6. [Ollama Model Management](#ollama-model-management)
7. [Database Operations](#database-operations)
8. [Monitoring & Troubleshooting](#monitoring--troubleshooting)
9. [Multi-User GPU Etiquette](#multi-user-gpu-etiquette)

---

## Server Overview

### Specifications

| Resource | Value |
|----------|-------|
| GPU | 2x NVIDIA RTX 6000 Ada Generation (46 GB VRAM each, 92 GB total) |
| RAM | 220 GB |
| CPU | 148 cores |
| Disk | 7.8 TB (5% used) |
| OS | Ubuntu 22.04 (kernel 6.8.0-90-generic) |
| CUDA | 13.0 |
| NVIDIA Driver | 580.95.05 |
| Container Runtime | Podman (rootless, Docker-compatible) |

### Server Access

**Network topology:** Local machine -> Bastion host -> GPU server

| Node | Host | Port | User |
|------|------|------|------|
| Bastion | `<BASTION_IP>` | `<BASTION_PORT>` | `<BASTION_USER>` |
| GPU server | `<GPU_IP>` | `<GPU_PORT>` | `<GPU_USER>` |

**SSH configuration** (`~/.ssh/config` on developer machine):

```
Host polyu-bastion
    HostName <BASTION_IP>
    Port <BASTION_PORT>
    User <BASTION_USER>

Host polyu-gpu
    HostName <GPU_IP>
    Port <GPU_PORT>
    User <GPU_USER>
    ProxyJump polyu-bastion
```

Connect: `ssh polyu-gpu`

SSH key pair: `~/.ssh/id_ed25519`. Public key installed on both bastion and GPU server.

---

## Network Architecture

```
Campus Network / VPN
        |
        v
  +-----------+
  |   Nginx   |  :80  (0.0.0.0 - only externally exposed port)
  +-----------+
    |       |
    v       v
+-------+ +----------------+
|obd-ui | |diagnostic-api  |  :8000 (internal only)
| :3001 | +----------------+
+-------+        |
                 v
         +-------------+    +-----------+
         |  PostgreSQL  |    |  Ollama   |
         |  + pgvector  |    | (GPU CDI) |
         |    :5432     |    |  :11434   |
         +-------------+    +-----------+
```

- **Nginx** is the sole external gateway (port 80).
- All other services bind to `127.0.0.1` (container-internal network).
- Browser requests to `/v1/*`, `/v2/*`, `/auth/*`, `/health`, `/docs` are
  proxied to the FastAPI backend; everything else goes to the Next.js frontend.
- SSE streaming (`/v2/obd/{id}/diagnose`) has `proxy_buffering off` for
  real-time token delivery.

---

## Prerequisites

### 1. Podman and podman-compose

```bash
sudo apt update
sudo apt install -y podman
pip install podman-compose
```

Verify:

```bash
podman --version     # 3.4+ expected
podman-compose -v    # any version
```

### 2. NVIDIA Container Toolkit (CDI)

CDI (Container Device Interface) replaces Docker's `runtime: nvidia` for
rootless GPU passthrough in Podman.

```bash
# Add NVIDIA repository
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update
sudo apt install -y nvidia-container-toolkit

# Generate CDI spec (must re-run after driver updates)
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml

# Verify devices are listed
nvidia-ctk cdi list
```

Expected output should include `nvidia.com/gpu=0` and `nvidia.com/gpu=1`.

### 3. Git

```bash
sudo apt install -y git
```

---

## First-Time Setup

### Automated (recommended)

```bash
git clone <repo-url> && cd stf_ai_diagnosis_platform_v1
bash infra/scripts/polyu-setup.sh
```

The setup script will:
1. Verify Podman, podman-compose, and NVIDIA CDI
2. Copy `.env.polyu.example` to `.env` (prompts you to edit)
3. Build and start all services
4. Pull Ollama models (qwen3.5:9b, nomic-embed-text, llava)
5. Run health checks
6. Print access URLs

### Manual step-by-step

1. **Clone the repository:**

   ```bash
   git clone <repo-url> && cd stf_ai_diagnosis_platform_v1
   ```

2. **Create and edit `.env`:**

   ```bash
   cp infra/.env.polyu.example infra/.env
   nano infra/.env
   ```

   Generate secrets:

   ```bash
   echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
   echo "APP_DB_PASSWORD=$(openssl rand -hex 24)"
   echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
   ```

3. **Start services:**

   ```bash
   podman-compose \
       -f infra/docker-compose.yml \
       -f infra/docker-compose.polyu.yml \
       up -d --build
   ```

4. **Pull Ollama models:**

   ```bash
   podman exec stf-ollama ollama pull qwen3.5:9b
   podman exec stf-ollama ollama pull nomic-embed-text
   podman exec stf-ollama ollama pull llava
   ```

5. **Verify GPU access:**

   ```bash
   podman exec stf-ollama nvidia-smi
   ```

6. **Check health:**

   ```bash
   curl http://127.0.0.1:80/health        # Nginx -> API
   curl http://127.0.0.1:8000/health       # API direct
   podman exec stf-postgres pg_isready -U stf_user -d stf_diagnosis
   ```

---

## Updating Deployments

### Automated

```bash
ssh polyu-gpu
cd /path/to/stf_ai_diagnosis_platform_v1
bash infra/scripts/polyu-deploy.sh
```

### Manual

```bash
ssh polyu-gpu
cd /path/to/stf_ai_diagnosis_platform_v1
git pull
podman-compose \
    -f infra/docker-compose.yml \
    -f infra/docker-compose.polyu.yml \
    up -d --build
```

### Update workflow

```
Developer machine                  PolyU GPU server
-----------------                  -----------------
git push origin main   ------>     ssh polyu-gpu
                                   cd /path/to/project
                                   bash infra/scripts/polyu-deploy.sh
```

---

## Ollama Model Management

```bash
# List installed models
podman exec stf-ollama ollama list

# Pull a new model
podman exec stf-ollama ollama pull <model-name>

# Remove a model (free VRAM/disk)
podman exec stf-ollama ollama rm <model-name>

# Check model info
podman exec stf-ollama ollama show <model-name>
```

With 92 GB VRAM, larger models are feasible. The default `qwen3.5:9b`
uses approximately 6 GB VRAM.

---

## Database Operations

### Backup PostgreSQL

```bash
# Dump to SQL file
podman exec stf-postgres pg_dump \
    -U stf_user -d stf_diagnosis \
    --clean --if-exists \
    > backup_$(date +%Y%m%d_%H%M%S).sql

# Compressed backup
podman exec stf-postgres pg_dump \
    -U stf_user -d stf_diagnosis \
    -Fc > backup_$(date +%Y%m%d_%H%M%S).dump
```

### Restore PostgreSQL

```bash
# From SQL file
cat backup_YYYYMMDD_HHMMSS.sql | \
    podman exec -i stf-postgres psql -U stf_user -d stf_diagnosis

# From compressed dump
podman exec -i stf-postgres pg_restore \
    -U stf_user -d stf_diagnosis \
    --clean --if-exists < backup_YYYYMMDD_HHMMSS.dump
```

### Migrate data from local dev

```bash
# On local machine: dump
docker exec stf-postgres pg_dump \
    -U stf_user -d stf_diagnosis -Fc > local_dump.dump

# Transfer to server
scp local_dump.dump polyu-gpu:/tmp/

# On server: restore
cat /tmp/local_dump.dump | \
    podman exec -i stf-postgres pg_restore \
    -U stf_user -d stf_diagnosis --clean --if-exists
```

### Run Alembic migrations

```bash
podman exec stf-diagnostic-api alembic upgrade head
```

---

## Monitoring & Troubleshooting

### Logs

```bash
COMPOSE="podman-compose -f infra/docker-compose.yml -f infra/docker-compose.polyu.yml"

# All services
$COMPOSE logs -f

# Specific service
$COMPOSE logs -f diagnostic-api
$COMPOSE logs -f ollama
$COMPOSE logs -f nginx
$COMPOSE logs -f postgres

# Last N lines
$COMPOSE logs --tail=100 diagnostic-api
```

### Container status

```bash
$COMPOSE ps
podman ps -a   # all containers including stopped
```

### GPU monitoring

```bash
# One-shot
nvidia-smi

# Continuous (updates every 2s)
watch -n 2 nvidia-smi

# Compact format
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu \
    --format=csv,noheader
```

### Common issues

**Ollama cannot access GPU:**

```bash
# Re-generate CDI spec
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list   # verify devices listed

# Restart Ollama container
podman restart stf-ollama

# Verify inside container
podman exec stf-ollama nvidia-smi
```

**Port 80 already in use:**

```bash
# Check what's using port 80
sudo ss -tlnp | grep :80

# Use a different port in .env
NGINX_PORT=8080
```

**Database connection refused:**

```bash
# Check if postgres container is running
podman ps | grep stf-postgres

# Check postgres logs
podman logs stf-postgres --tail=50

# Verify from inside the API container
podman exec stf-diagnostic-api python -c "
from app.config import get_settings
s = get_settings()
print(s.database_url)
"
```

**Build fails (out of disk/memory):**

```bash
# Clean up unused images and build cache
podman system prune -a
podman image prune -a
```

---

## Multi-User GPU Etiquette

This server is shared with other researchers. Please follow these guidelines:

1. **Check GPU usage before starting:** Run `nvidia-smi` and verify there
   is enough free VRAM before starting Ollama with large models.

2. **Use a single GPU if possible:** The default configuration requests
   all GPUs (`nvidia.com/gpu=all`). If other users need a GPU, you can
   restrict to one GPU by editing `docker-compose.polyu.yml`:

   ```yaml
   ollama:
     devices:
       - nvidia.com/gpu=0
   ```

3. **Stop services when not in use:** If you are not actively using the
   platform, stop it to free GPU memory:

   ```bash
   podman-compose -f infra/docker-compose.yml \
       -f infra/docker-compose.polyu.yml down
   ```

4. **Monitor VRAM usage:** Keep `watch nvidia-smi` running in a separate
   terminal during active use.

5. **Communicate:** Coordinate with other GPU users before running
   VRAM-intensive operations (e.g., pulling larger models).

---

## Notes

- AWS reverse proxy is only needed if off-campus access is required.
  For campus or VPN access, direct connection to the server IP is sufficient.
- SSL/TLS can be added later by mounting certificates in the Nginx container
  and updating `nginx.conf` to listen on port 443.
- The `PREMIUM_LLM_ENABLED=true` setting requires outbound internet access
  to reach OpenRouter. Verify the server can reach `https://openrouter.ai`.
