# STF AI Diagnosis Platform v1

## Overview

This project builds an edge–cloud AI diagnostic system that fuses heterogeneous vehicle data (OBD-II, vibration/acoustics, cameras, GNSS/IMU) to detect anomalies on-vehicle in real time and perform deep fault classification and prediction in the cloud using temporal deep learning (e.g., CNN-LSTM/Transformer with attention).

## Key Features

- **Real-time Edge Detection**: Lightweight edge models for sub-second anomaly alerts
- **Deep Cloud Analysis**: Temporal deep learning for comprehensive fault classification
- **Knowledge Distillation**: Compress cloud models into efficient edge models
- **Fleet-Scale Learning**: Continuous retraining with labeled maintenance data
- **Multi-Modal Data Fusion**: OBD-II, vibration/acoustics, cameras, GNSS/IMU integration
- **Web-Based Platform**: Predictive maintenance dashboards for drivers, technicians, and managers
- **Location Intelligence**: Indoor–outdoor inference and tracking

## Architecture

The system implements a hybrid edge-cloud architecture:

- **Edge Layer**: Real-time anomaly detection using distilled models
- **Cloud Layer**: Deep fault classification using CNN-LSTM/Transformer with attention
- **Fleet Management**: Web-based platform for operational insights

## Documentation

For detailed design specifications, see the [Design Document](doc/design_doc.md).

## Development Standards

This project strictly follows the **Google Python Style Guide** and uses:

- **FastAPI** for API endpoints
- **Pydantic** for data validation
- **Weaviate** for RAG pipelines
- **Ollama/vLLM** for local LLM inference
- **Dify** for agentic workflows

## Project Structure

```
stf_ai_diagnosis_platform_v1/
├── .cursor/              # Cursor IDE rules and configurations
├── doc/                  # Design documents and specifications
├── docs/                 # Security baseline and operational docs
├── diagnostic_api/       # FastAPI backend (Phase 1 stub)
│   ├── app/              # Application code
│   ├── Dockerfile        # Container build configuration
│   └── requirements.txt  # Python dependencies
├── rag/                  # Ingestion and chunking scripts (planned)
├── expert_model/         # Prompts, JSON schemas, validators (planned)
├── training/             # Dataset builder and training configs (planned)
├── eval/                 # Evaluation harness (planned)
├── infra/                # Docker and network configurations
│   ├── docker-compose.yml   # Service orchestration
│   ├── .env.example         # Environment template
│   ├── Makefile             # Convenience commands
│   ├── README_LOCAL_SETUP.md # Setup instructions
│   └── init-scripts/        # Database initialization
└── tests/                # Unit and integration tests (planned)
```

## Getting Started

### Phase 1 - Local Deployment

The STF AI Diagnosis Platform is now ready for local deployment! 🚀

**Quick Start:**

1. **Prerequisites:** Docker, Docker Compose, Git
2. **Clone the repository**
3. **Configure environment:**
   ```bash
   cd infra
   cp .env.example .env
   # Edit .env and set passwords
   ```
4. **Start all services:**
   ```bash
   make init  # Pull images and build
   make up    # Start services
   make health # Verify
   ```
5. **Access the platform:**
   - Dify Web UI: http://127.0.0.1:3000
   - Diagnostic API: http://127.0.0.1:8000/docs
   - API Health: http://127.0.0.1:8000/health

**Full Documentation:**
- [Local Setup Guide](infra/README_LOCAL_SETUP.md) - Complete installation and troubleshooting
- [Security Baseline](docs/SECURITY_BASELINE.md) - Network rules, secrets management, privacy controls
- [Design Document](doc/design_doc.md) - Architecture and system design
- [Development Plan](doc/dev_plan.md) - Project roadmap and task breakdown

**What's Included (Phase 1):**
- ✅ Dify workflow orchestration + UI
- ✅ Ollama local LLM inference
- ✅ Weaviate vector store for RAG
- ✅ FastAPI diagnostic API (stub)
- ✅ Postgres database
- ✅ Redis cache and message broker
- ✅ Docker Compose orchestration
- ✅ Makefile for easy management
- ✅ Comprehensive documentation

## Privacy & Security

- All raw sensor data remains in the backend
- Only derived features and summaries are processed by LLMs
- Automated PII redaction implemented
- Docker network isolation enforced

## License

*To be determined*

## Author

AI Pilot Lead - January 2026

