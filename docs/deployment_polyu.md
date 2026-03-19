# PolyU Server Deployment Guide

> Last updated: 2026-03-19

## Server Access

**Network topology:** Local machine → Bastion host → GPU server

| Node | Host | Port | User |
|------|------|------|------|
| Bastion | `<BASTION_IP>` | `<BASTION_PORT>` | `<BASTION_USER>` |
| GPU server | `<GPU_IP>` | `<GPU_PORT>` | `<GPU_USER>` |

### SSH Configuration

`~/.ssh/config` on the developer machine:

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

Connect with a single command:

```bash
ssh polyu-gpu
```

SSH key pair: `~/.ssh/id_ed25519`.
Public key is installed on both the bastion and GPU server.

## Server Specifications

| Resource | Value |
|----------|-------|
| GPU | 2x NVIDIA RTX 6000 Ada Generation (46 GB VRAM each, 92 GB total) |
| RAM | 220 GB |
| CPU | 148 cores |
| Disk | 7.8 TB (5% used) |
| OS | Ubuntu 22.04 (kernel 6.8.0-90-generic) |
| CUDA | 13.0 |
| NVIDIA Driver | 580.95.05 |

## Container Runtime: Podman

The server uses **Podman** instead of Docker because multiple users
share the machine and not all can be granted root access.  Podman runs
rootless by default and is command-compatible with Docker.

Key differences for deployment:

- Replace `docker` commands with `podman`.
- Replace `docker-compose` with `podman-compose`.
- GPU passthrough uses CDI (Container Device Interface) instead of
  `--gpus all` / `runtime: nvidia`.

## Deployment Workflow

```
Developer machine                  PolyU GPU server
─────────────────                  ─────────────────
git push origin main   ──────►    ssh polyu-gpu
                                  cd /path/to/project
                                  git pull
                                  podman-compose up -d --build
```

### First-time Setup

1. **Install Podman and podman-compose:**

   ```bash
   sudo apt update
   sudo apt install -y podman
   pip install podman-compose
   ```

2. **Install NVIDIA Container Toolkit (CDI):**

   ```bash
   # Add NVIDIA repo
   distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
       | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" \
       | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
       | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt update
   sudo apt install -y nvidia-container-toolkit

   # Generate CDI spec
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   nvidia-ctk cdi list   # verify devices are listed
   ```

3. **Clone and start services:**

   ```bash
   git clone <repo-url> && cd stf_ai_diagnosis_platform_v1
   cp infra/.env.example infra/.env
   # Edit infra/.env with production values
   podman-compose -f infra/docker-compose.yml up -d --build
   ```

4. **Verify GPU access inside Ollama container:**

   ```bash
   podman exec -it ollama nvidia-smi
   ```

### Updating

```bash
ssh polyu-gpu
cd /path/to/stf_ai_diagnosis_platform_v1
git pull
podman-compose -f infra/docker-compose.yml up -d --build
```

## Notes

- The server is shared — other users run workloads on the GPUs.
  Monitor VRAM usage with `nvidia-smi` before starting services.
- AWS reverse proxy is only needed if external (off-campus) access
  is required.  For campus-only or VPN access, direct connection
  is sufficient.
- Bind application ports to `0.0.0.0` (not `127.0.0.1`) so the
  service is reachable from the campus network.  Nginx should still
  be the sole ingress gateway.
