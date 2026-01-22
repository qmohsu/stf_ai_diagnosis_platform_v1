# STF AI Diagnosis Platform - Security Baseline

**Author:** Li-Ta Hsu  
**Date:** January 2026  
**Version:** 1.0.0  
**Classification:** Internal Use Only

---

## Table of Contents

1. [Overview](#overview)
2. [Threat Model & Assumptions](#threat-model--assumptions)
3. [Network Security](#network-security)
4. [Access Control](#access-control)
5. [Data Protection](#data-protection)
6. [Secrets Management](#secrets-management)
7. [Container Security](#container-security)
8. [Logging & Monitoring](#logging--monitoring)
9. [Backup & Recovery](#backup--recovery)
10. [Known Limitations (Phase 1)](#known-limitations-phase-1)
11. [Phase 2 Security Roadmap](#phase-2-security-roadmap)
12. [Incident Response](#incident-response)

---

## Overview

This document defines the security baseline for the STF AI Diagnosis Platform Phase 1 pilot deployment. The platform is designed for **local-first, laptop-based deployment** with strict privacy and network isolation requirements.

### Security Objectives (Phase 1)

1. **Privacy First:** No raw sensor data (waveforms, audio, video) leaves the system
2. **Network Isolation:** All services run locally; no external API calls
3. **Data Protection:** PII and sensitive data are redacted before logging
4. **Secret Management:** Secrets stored securely, never committed to version control
5. **Audit Trail:** All interactions logged for Phase 1.5 training data collection

### Out of Scope (Phase 1)

- Multi-user authentication (single admin user)
- HTTPS/TLS for internal services (localhost only)
- Advanced intrusion detection
- Compliance certifications (GDPR, SOC2, etc.)

---

## Threat Model & Assumptions

### Deployment Context

- **Environment:** Personal laptop, single user (technician or developer)
- **Physical Security:** Laptop is physically secured by the user
- **Network:** No public network exposure; all services bind to `127.0.0.1`
- **Users:** Single trusted user with full system access

### Assets to Protect

1. **Diagnostic Data:** Vehicle sensor summaries, risk scores, recommendations
2. **RAG Corpus:** SOPs, manuals, maintenance logs (proprietary)
3. **Interaction Logs:** User queries and system responses (training data)
4. **Credentials:** Database passwords, API keys, secret keys
5. **LLM Model:** Locally stored model weights

### Threat Scenarios (In Scope)

| Threat | Mitigation |
|--------|------------|
| **Data exfiltration** via LLM API | Network isolation; no external API calls |
| **Raw sensor data leakage** to LLM | Input validation; explicit data boundaries |
| **PII in logs** exposed to unauthorized users | Automated PII redaction before logging |
| **Secrets in version control** | Gitignore `.env`; use `.env.example` |
| **Unauthorized container access** | Internal Docker network; localhost binding |
| **Data loss** from container failure | Named volumes; backup procedures |

### Threat Scenarios (Out of Scope for Phase 1)

- Network-based attacks (no network exposure)
- Malware on host system (host security is user's responsibility)
- Physical theft of laptop (disk encryption recommended but not enforced)
- Side-channel attacks on LLM inference

---

## Network Security

### Network Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Laptop (127.0.0.1 only)                                    │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Docker Network: stf-internal (172.28.0.0/16)       │  │
│  │                                                        │  │
│  │  ┌─────────┐  ┌─────────┐  ┌──────────┐           │  │
│  │  │ Postgres│  │  Redis  │  │ Weaviate │           │  │
│  │  └────┬────┘  └────┬────┘  └─────┬────┘           │  │
│  │       │            │              │                  │  │
│  │  ┌────▼────────────▼──────────────▼─────┐          │  │
│  │  │     Dify API + Worker + Web          │          │  │
│  │  └────┬─────────────────────────────────┘          │  │
│  │       │                                              │  │
│  │  ┌────▼────────────┐    ┌───────────────┐          │  │
│  │  │ Diagnostic API  │◄───┤    Ollama     │          │  │
│  │  └─────────────────┘    └───────────────┘          │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘
│         │                                                   │
│    127.0.0.1:3000 (Dify Web UI)                          │
│    127.0.0.1:8000 (Diagnostic API)                       │
│                                                               │
└───────────────────────────────────────────────────────────────┘
        ▲
        │
   Local User Only (no remote access)
```

### Network Isolation Rules

#### 1. Localhost Binding

All exposed services **MUST** bind to `127.0.0.1` only:

```yaml
# docker-compose.yml
ports:
  - "127.0.0.1:3000:3000"  # ✅ Correct
  - "3000:3000"             # ❌ Wrong (exposes to 0.0.0.0)
```

**Verification:**
```bash
# Check listening ports
netstat -tuln | grep -E ':(3000|5001|8000|8080|11434)'

# Should show:
# tcp  127.0.0.1:3000  0.0.0.0:*  LISTEN
# NOT:
# tcp  0.0.0.0:3000    0.0.0.0:*  LISTEN
```

#### 2. Internal Docker Network

Services communicate via internal Docker network only:

- **Network:** `stf-internal` (bridge, not host)
- **Subnet:** `172.28.0.0/16`
- **DNS:** Docker internal DNS for service discovery

**No container should:**
- Use `host` network mode
- Expose ports to `0.0.0.0` without explicit localhost binding
- Make outbound calls to external IPs (except during initial setup)

#### 3. Egress Control

**Allowed at runtime:**
- Container-to-container communication within `stf-internal` network
- DNS resolution (for internal service names only)

**Blocked at runtime:**
- Outbound HTTPS to external LLM APIs (OpenAI, Anthropic, etc.)
- Outbound HTTP to external services (except Dify image pulls on first boot)

**Implementation:**
- Dify SSRF proxy configured to allow-list internal services only
- `ALLOW_EXTERNAL_APIS=false` enforced in diagnostic_api

**Exception:** Initial Docker image pulls require internet access. After initial setup, runtime should be fully air-gapped.

### Firewall Rules (Host Level)

**Recommended (optional):**
```bash
# Linux (iptables)
# Block all Docker container egress except to internal network
sudo iptables -I DOCKER-USER -s 172.28.0.0/16 -d 172.28.0.0/16 -j ACCEPT
sudo iptables -I DOCKER-USER -s 172.28.0.0/16 ! -d 172.28.0.0/16 -j DROP

# macOS/Windows: Configure Docker Desktop network settings
```

---

## Access Control

### User Roles (Phase 1)

| Role | Access Level | Description |
|------|--------------|-------------|
| **Admin** | Full access | Single user with access to all services |
| **System** | Internal only | Service-to-service communication |

**Note:** Multi-user RBAC is **out of scope for Phase 1**. Laptop user has full administrative access to all services.

### Service Authentication

#### Postgres

- **User:** `dify_user` (Dify database), `stf_app_user` (application database)
- **Auth:** Password-based (from `.env`)
- **Access:** Only accessible from Docker internal network

#### Redis

- **Auth:** Password-based (`REDIS_PASSWORD`)
- **Access:** Only accessible from Docker internal network

#### Weaviate

- **Auth:** API key-based (`WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS`)
- **Anonymous Access:** Disabled (`WEAVIATE_AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=false`)
- **Access:** Only accessible from Docker internal network

#### Dify

- **Admin UI:** Username/password (set on first login)
- **API:** API key-based (generated in Dify UI)
- **Access:** Web UI on `127.0.0.1:3000`

#### Diagnostic API

- **Auth:** None (Phase 1; intended for internal Dify use only)
- **Access:** Only accessible from `127.0.0.1:8000` and Docker internal network

**Phase 2:** Add API key authentication for diagnostic API.

---

## Data Protection

### Data Classification

| Data Type | Classification | Storage Location | Protection |
|-----------|----------------|------------------|------------|
| Raw sensor data (waveforms, audio, video) | **Sensitive** | NOT stored; not passed to LLM | N/A |
| Sensor summaries (risk scores, features) | **Internal** | Postgres (interaction_logs) | Encrypted at rest (volume) |
| User queries (technician input) | **Internal** | Postgres (interaction_logs) | PII redaction |
| LLM responses (recommendations) | **Internal** | Postgres (interaction_logs) | Encrypted at rest (volume) |
| SOPs, manuals (RAG corpus) | **Confidential** | Weaviate (vector store) | Access control |
| Credentials (passwords, API keys) | **Secret** | `.env` file (gitignored) | Host file permissions |

### Privacy Boundaries

**Critical Rule:** Raw sensor data **MUST NOT** be passed to the LLM.

**Allowed in LLM context:**
- Text summaries (e.g., "vibration amplitude increased by 20%")
- Risk scores (e.g., "risk_level: 0.75")
- Fault codes (e.g., "P0171")
- Manually entered notes (after PII redaction)

**Prohibited in LLM context:**
- Raw waveforms (vibration, sound)
- Audio recordings
- Video frames
- Full GNSS tracks (only start/end locations if needed)
- Unredacted PII (names, phone numbers, addresses)

**Enforcement:**
- Input validation in `diagnostic_api` (schema validation)
- Explicit type constraints in Pydantic models
- Unit tests to verify data boundaries

### PII Redaction

**Required before:**
- Logging to `interaction_logs`
- Passing user input to LLM
- Exporting "case packages" for Phase 1.5

**PII Types:**
- Names (technician, vehicle owner)
- Phone numbers
- Email addresses
- Precise GPS coordinates (round to nearest km if needed)
- License plate numbers
- VIN (use pseudonymous vehicle_id instead)

**Implementation:**
- Automated redaction in `diagnostic_api` (regex + NER)
- `REDACT_PII=true` enforced in production
- Redacted fields replaced with `[REDACTED]` or pseudonymized tokens

### Encryption at Rest

**Phase 1:**
- Docker volumes use host filesystem (no separate encryption)
- **Recommendation:** Enable full disk encryption on host (BitLocker, FileVault, LUKS)

**Phase 2:**
- Encrypted Docker volumes (using dm-crypt or similar)
- Encrypted backups with passphrase protection

---

## Secrets Management

### Secret Types

1. **Database Passwords:** Postgres, Redis
2. **API Keys:** Weaviate, Dify
3. **Secret Keys:** Dify session encryption

### Storage & Distribution

**Current (Phase 1):**
- Secrets stored in `.env` file
- `.env` file is gitignored
- `.env.example` provided as template (no real secrets)
- User creates `.env` from template and fills in secrets

**Security Checklist:**
- ✅ `.env` listed in `.gitignore`
- ✅ `.env.example` contains only placeholders
- ✅ Strong passwords enforced (min 16 chars recommended)
- ✅ Secrets loaded via environment variables only (not hardcoded)

### Secret Generation

**Recommended commands:**
```bash
# Generate strong password (Linux/macOS)
openssl rand -base64 32

# Generate API key
openssl rand -hex 32

# Generate Dify secret key
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Secret Rotation

**Phase 1:**
- Manual rotation by updating `.env` and restarting services
- No automated rotation mechanism

**Rotation procedure:**
1. Stop services: `make down`
2. Update `.env` with new secrets
3. Update database passwords if changed:
   ```bash
   docker exec stf-postgres psql -U postgres -c \
     "ALTER USER dify_user WITH PASSWORD 'new_password';"
   ```
4. Restart services: `make up`

### Secret Exposure Prevention

**Pre-commit checks (recommended):**
```bash
# Install pre-commit hooks
pip install pre-commit detect-secrets

# Configure .pre-commit-config.yaml
# (See DevOps rules for configuration)

# Prevents commits containing secrets
```

**Git history cleanup (if secrets committed):**
```bash
# If secrets accidentally committed, clean history:
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch infra/.env' \
  --prune-empty --tag-name-filter cat -- --all

# Then rotate compromised secrets immediately
```

---

## Container Security

### Image Sources

All images **MUST** come from:
- Official Docker Hub repositories (e.g., `postgres:15.6-alpine`)
- Verified publishers (e.g., `langgenius/dify-api:0.6.13`)
- Internal builds (e.g., `stf-diagnostic-api:0.1.0`)

**No third-party unverified images allowed.**

### Image Pinning

All images **MUST** be pinned to specific versions:

```yaml
# ✅ Correct
image: postgres:15.6-alpine

# ❌ Wrong (unpredictable)
image: postgres:latest
```

**Rationale:** Ensures reproducibility and prevents unexpected behavior from image updates.

### Vulnerability Scanning

**Recommended (manual for Phase 1):**
```bash
# Scan images before deployment
docker scan postgres:15.6-alpine
docker scan langgenius/dify-api:0.6.13
```

**Phase 2:** Automated vulnerability scanning in CI/CD pipeline.

### Container Isolation

- **User:** All containers run as non-root users where possible
- **Capabilities:** Drop unnecessary Linux capabilities
- **Read-only:** Use read-only root filesystems where possible

**Example (Postgres):**
```yaml
postgres:
  image: postgres:15.6-alpine
  user: postgres
  read_only: false  # Postgres requires write access to data dir
  security_opt:
    - no-new-privileges:true
```

### Resource Limits

Prevent resource exhaustion attacks:

```yaml
# docker-compose.yml
services:
  diagnostic-api:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

---

## Logging & Monitoring

### Logging Strategy

#### What to Log

**Diagnostic API:**
- All API requests (method, endpoint, status code)
- User input (after PII redaction)
- Retrieved RAG chunks (doc_id, section, score)
- LLM tool calls and responses
- Errors and exceptions (with stack traces)

**Interaction Logs:**
- Stored in Postgres `interaction_logs` table
- Schema: `session_id`, `vehicle_id`, `user_input`, `retrieved_chunks`, `tool_outputs`, `final_response`, `timestamp`

**Container Logs:**
- All service logs available via `docker-compose logs`

#### What NOT to Log

- Raw sensor data (waveforms, audio, video)
- Unredacted PII (names, phone numbers, emails)
- Passwords or API keys
- Full stack traces that might leak secrets

### Log Format

**Structured JSON logging (diagnostic_api):**
```json
{
  "timestamp": "2026-01-22T10:00:00.000Z",
  "level": "INFO",
  "service": "diagnostic_api",
  "event": "diagnostic_request",
  "session_id": "uuid",
  "vehicle_id": "V12345",
  "user_input": "[REDACTED]",
  "status": "success"
}
```

### Log Storage

- **Diagnostic API logs:** Persisted to `/app/logs/diagnostic_api.log` (Docker volume)
- **Interaction logs:** Postgres database (backed up with database)
- **Container logs:** Docker logs (ephemeral; use `docker-compose logs > backup.log` to persist)

### Log Retention

**Phase 1:**
- Logs retained indefinitely (manual cleanup required)
- Recommended: Archive logs older than 90 days

**Phase 2:**
- Automated log rotation (logrotate)
- Configurable retention policies

### Monitoring & Alerting

**Phase 1 (manual):**
- Health checks via `make health`
- Manual log review

**Phase 2 (automated):**
- Prometheus metrics
- Grafana dashboards
- Alerting on service failures

---

## Backup & Recovery

### Backup Strategy

#### What to Backup

1. **Postgres databases:** `dify` and `stf_diagnosis`
2. **Weaviate vector store:** RAG corpus
3. **Ollama models:** Downloaded LLM models
4. **Configuration:** `.env` file (store securely, separate from code)
5. **Dify storage:** Uploaded files and workflow configs

#### Backup Frequency

**Phase 1 (manual):**
- After significant configuration changes
- Before version upgrades
- Weekly for production use

**Phase 2 (automated):**
- Daily incremental backups
- Weekly full backups

#### Backup Procedure

```bash
# Automated backup (creates timestamped archives)
make backup

# Manual backup of specific volumes
docker run --rm -v stf_postgres_data:/data -v $(pwd)/backups:/backup \
  alpine tar czf /backup/postgres_$(date +%Y%m%d_%H%M%S).tar.gz -C /data .

docker run --rm -v stf_weaviate_data:/data -v $(pwd)/backups:/backup \
  alpine tar czf /backup/weaviate_$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

### Backup Storage

**Phase 1:**
- Backups stored in `infra/backups/` on host filesystem
- **Recommendation:** Copy backups to external drive or cloud storage (encrypted)

**Security:**
- Backups contain sensitive data; treat as classified
- Encrypt backups before transferring: `gpg -c backup_file.tar.gz`
- Store backup decryption keys separately from backups

### Recovery Procedure

```bash
# 1. Stop services
make down

# 2. Remove old volumes (CAUTION: destructive)
docker volume rm stf_postgres_data

# 3. Restore from backup
docker run --rm -v stf_postgres_data:/data -v $(pwd)/backups:/backup \
  alpine tar xzf /backup/postgres_YYYYMMDD_HHMMSS.tar.gz -C /data

# 4. Restart services
make up

# 5. Verify data integrity
make test-health
```

### Disaster Recovery

**Scenarios:**
1. **Container failure:** Automatic restart (unless-stopped policy)
2. **Data corruption:** Restore from last known good backup
3. **Laptop failure:** Restore to new laptop from backups
4. **Accidental deletion:** Restore from backup (no undo mechanism)

**RTO (Recovery Time Objective):** < 4 hours  
**RPO (Recovery Point Objective):** Last backup (up to 7 days for Phase 1)

---

## Known Limitations (Phase 1)

### Security Limitations

1. **No HTTPS/TLS:** Internal services use HTTP (localhost only)
2. **No API authentication:** Diagnostic API is unauthenticated
3. **No RBAC:** Single admin user; no role-based access control
4. **No audit trail:** Limited logging of administrative actions
5. **No automated secret rotation:** Manual process only
6. **No container hardening:** Minimal seccomp/AppArmor profiles
7. **No intrusion detection:** No IDS/IPS
8. **No automated backups:** Manual backup process

### Privacy Limitations

1. **PII redaction is regex-based:** May miss edge cases (Phase 2: NER)
2. **No differential privacy:** Interaction logs may reveal patterns
3. **No consent management:** Single user, implied consent

### Operational Limitations

1. **Single point of failure:** No redundancy; laptop failure = downtime
2. **No disaster recovery automation:** Manual restore process
3. **No monitoring dashboards:** Health checks via CLI only

---

## Phase 2 Security Roadmap

### Short-term (Next 3 months)

1. **API Authentication:** Add API key auth for diagnostic API
2. **RBAC:** Multi-user support with role-based access control
3. **Audit Logging:** Comprehensive audit trail for all actions
4. **Automated Backups:** Daily backups with cloud sync
5. **Secret Rotation:** Automated secret rotation policies

### Medium-term (Next 6 months)

1. **TLS/HTTPS:** Enable TLS for all internal services
2. **Advanced PII Redaction:** NER-based redaction with GPT-4 fallback
3. **Container Hardening:** Seccomp profiles, AppArmor, read-only filesystems
4. **Vulnerability Scanning:** Automated image scanning in CI/CD
5. **Monitoring & Alerting:** Prometheus + Grafana + PagerDuty integration

### Long-term (Next 12 months)

1. **Zero Trust Architecture:** mTLS between all services
2. **Hardware Security Module (HSM):** For secret storage
3. **Compliance Certifications:** GDPR, ISO 27001, SOC 2
4. **Penetration Testing:** Annual security audits
5. **Disaster Recovery Site:** Cloud-based backup site for failover

---

## Incident Response

### Incident Types

| Severity | Examples | Response Time |
|----------|----------|---------------|
| **Critical** | Data breach, secret exposure | Immediate (< 1 hour) |
| **High** | Service outage, data corruption | < 4 hours |
| **Medium** | Performance degradation | < 1 day |
| **Low** | Non-critical errors | < 1 week |

### Incident Response Procedure

#### 1. Detection

- Monitor logs: `make logs`
- Check health: `make health`
- User reports issues

#### 2. Containment

- Isolate affected services: `docker-compose stop <service>`
- Collect logs: `docker-compose logs > incident_logs.txt`
- Preserve evidence (don't restart services immediately)

#### 3. Investigation

- Review logs for root cause
- Check for unauthorized access (Postgres logs, API logs)
- Verify data integrity

#### 4. Remediation

- Apply fix (patch, config change, secret rotation)
- Restore from backup if needed
- Restart services: `make up`

#### 5. Recovery

- Verify all services healthy: `make test-health`
- Test end-to-end functionality
- Monitor for recurring issues

#### 6. Post-Incident Review

- Document incident in incident log
- Identify root cause
- Implement preventive measures
- Update security baseline if needed

### Incident Contact

**Phase 1 (laptop deployment):**
- User is responsible for incident response
- Contact development team for technical support

**Phase 2 (multi-user deployment):**
- Security team on-call rotation
- Incident escalation procedures

---

## Security Checklist

### Deployment Checklist

- [ ] `.env` file created with strong, unique passwords
- [ ] `.env` file gitignored (verify with `git status`)
- [ ] All services bind to `127.0.0.1` (verify with `netstat`)
- [ ] Docker volumes created for persistence
- [ ] Firewall rules configured (optional but recommended)
- [ ] Backups tested and verified
- [ ] Health checks passing
- [ ] Logs reviewed for errors

### Operational Checklist (Weekly)

- [ ] Review logs for anomalies
- [ ] Verify all services healthy
- [ ] Check disk space on volumes
- [ ] Create backup
- [ ] Test restore from backup (monthly)

### Decommissioning Checklist

- [ ] Stop all services: `make down`
- [ ] Create final backup
- [ ] Securely delete volumes: `make reset-volumes`
- [ ] Delete `.env` file (contains secrets)
- [ ] Wipe Docker volumes: `docker volume prune -a --force`
- [ ] (Optional) Secure erase disk if disposing of hardware

---

**Document Version:** 1.0.0  
**Last Updated:** January 2026  
**Next Review:** April 2026 (or upon Phase 2 kickoff)

For questions or security concerns, contact: Li-Ta Hsu (Lead AI Systems Engineer)
