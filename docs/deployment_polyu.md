# PolyU Server Deployment Guide

> Last updated: 2026-10-30 | GitHub Issues: #21, #24, #270

The first-time setup sections record the original V1/V2 deployment. For
the V3 frontend root switch, follow [Updating Deployments](#updating-deployments)
and the current procedure in the repository root `CLAUDE.md`; public
server validation of the new frontend is still pending.

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
10. [Cloudflare Tunnel (Public Access)](#cloudflare-tunnel-public-access)

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
Internet HTTPS -> Cloudflare Tunnel -> Nginx :8080
                                       |
                         +-------------+--------------+
                         |                            |
                         | /                          | /v3/
                         v                            v
                  V3 frontend :3002 ------------> V3 API :8002
                  (Next.js BFF)     server-side call    |
                                                       v
                                               V3 database

Nginx /v1/, /v2/, /auth/, /health -> V1/V2 API :8001
                                      |           |
                                      v           v
                                PostgreSQL     Ollama

Old obd-ui :3001 -> PolyU legacy-ui profile only (stopped at cutover)
```

- **Cloudflare Tunnel** provides public HTTPS access via `stf-diagnosis.dev`.
- **Nginx** is the sole internal gateway (port 8080).
- The V3 frontend binds only to host loopback on :3002. The V3 API uses
  host networking on :8002; Nginx and the frontend reach it through
  `127.0.0.1:8002`. V1/V2 services retain their existing host-network settings.
- Browser requests to `/v1/*`, `/v2/*`, `/auth/*`, and `/health` remain
  on the V1/V2 API; `/v3/*` goes to the V3 API. The public root goes to
  the V3 Next.js frontend, whose BFF calls the V3 API from the server.
- The old `obd-ui` webpage is not started by the default PolyU Compose
  profile after the cutover; local V1/V2 development is unchanged.
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
4. Pull Ollama models (qwen3.5:27b-q8_0, nomic-embed-text, llava)
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
        up -d --build postgres ollama diagnostic-api nginx
   ```

   The explicit list keeps the retired `obd-ui` out of the default PolyU
   startup, including with older Podman Compose versions.

4. **Pull Ollama models:**

   ```bash
   podman exec stf-ollama ollama pull qwen3.5:27b-q8_0
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

The commands below describe the historical V1/V2 stack. For the issue #270
V3 frontend cutover, use the V3 Compose project and branch-test/restore
procedure in the repository root `CLAUDE.md`. Before the branch test, record
the server's Git remotes, original branch and commit. For this PR, fetch
`codex/issue-270-v3-frontend-server` explicitly from
`https://github.com/SpaceDX666/stf_ai_diagnosis_platform_v1.git` and run
`git switch --detach FETCH_HEAD`; the server's `origin` may point elsewhere.
The frontend's public origin is `https://stf-diagnosis.dev`; health and login can be checked
through a local SSH tunnel, while an authenticated browser flow requires
the public root switch. The configuration is committed separately from
any public-server validation.

Nginx uses a single-file bind mount for `nginx.conf`. After checking out
the branch, wait until the V3 frontend is healthy, then recreate **only**
the Nginx container so it reads the checked-out file's new inode:

```bash
cd ~/stf_ai_diagnosis_platform_v1/infra
podman rm -f stf-nginx
~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d nginx
podman exec stf-nginx grep 'server 127.0.0.1:3002' /etc/nginx/nginx.conf
podman exec stf-nginx nginx -t
```

For rollback, restore the recorded original server branch and commit
(usually `main`) and the old `obd-ui` service. Recreate only `stf-nginx`
with the same two commands, then verify
`server 127.0.0.1:3001` inside the container and test the public root.
A reload alone may keep the old bind-mounted file. The V3 API, worker,
database, and V1/V2 backends do not need to restart for this frontend
cutover.

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
    down
podman-compose \
    -f infra/docker-compose.yml \
    -f infra/docker-compose.polyu.yml \
    up -d --build postgres ollama diagnostic-api nginx
```

Podman 3.4 does not reliably recreate a running container after `up --build`.
The `down`/`up` sequence refreshes the V1/V2 containers (with a brief outage);
the explicit service list leaves the retired `obd-ui` stopped. This legacy
Compose project does not include the independent V3 frontend. For a V3
frontend branch test or cutover, follow `CLAUDE.md` instead.

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

With 96 GB VRAM, the default `qwen3.5:27b-q8_0` (dense 27B, Q8
quantization) uses approximately 30 GB VRAM, leaving ample room for
KV cache and concurrent requests.

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

## Cloudflare Tunnel (Public Access)

The platform is publicly accessible at **https://stf-diagnosis.dev** via a
permanent Cloudflare Tunnel. The tunnel routes external HTTPS traffic to the
Nginx gateway on `127.0.0.1:8080`.

### Architecture

```
Internet (HTTPS)
       |
       v
  Cloudflare Edge (HK)
       |  (QUIC)
       v
  cloudflared (systemd user service)
       |
       v
  Nginx (:8080)  -->  V3 frontend (:3002) / V3 API (:8002)
                         + V1/V2 diagnostic-api (:8001)
```

### Configuration

Tunnel config is stored on the server (not in git):

- **Credentials**: `~/.cloudflared/<TUNNEL_ID>.json`
- **Certificate**: `~/.cloudflared/cert.pem`
- **Config**: `~/.cloudflared/config.yml`

```yaml
# ~/.cloudflared/config.yml
tunnel: <TUNNEL_ID>
credentials-file: /home/talon/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: stf-diagnosis.dev
    service: http://127.0.0.1:8080
  - service: http_status:404
```

### Management

```bash
# Check tunnel status
systemctl --user status cloudflared

# Restart tunnel
systemctl --user restart cloudflared

# View tunnel logs
journalctl --user -u cloudflared -f

# Stop tunnel (e.g., for maintenance)
systemctl --user stop cloudflared
```

The service is configured with `loginctl enable-linger`, so it persists
across SSH sessions and server reboots.

### Troubleshooting

**Tunnel not connecting:**

```bash
# Check if cloudflared is running
systemctl --user status cloudflared

# Check logs for errors
journalctl --user -u cloudflared --since "10 min ago"

# Manually test the tunnel
~/.local/bin/cloudflared tunnel run stf-diagnosis
```

**Domain not resolving:**

```bash
# Verify DNS CNAME exists
dig stf-diagnosis.dev CNAME

# Re-add DNS route if needed
~/.local/bin/cloudflared tunnel route dns stf-diagnosis stf-diagnosis.dev
```

---

## Notes

- The `PREMIUM_LLM_ENABLED=true` setting requires outbound internet access
  to reach OpenRouter. Verify the server can reach `https://openrouter.ai`.
