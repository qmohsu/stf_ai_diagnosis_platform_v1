# ============================================================================
# STF AI Diagnosis Platform - Makefile
# Author: Li-Ta Hsu
# Date: January 2026
# ============================================================================
# Convenience commands for managing the local Docker stack
# ============================================================================

.PHONY: help up down restart logs ps health status clean reset-volumes \
        pull build check-env init obd-agent-test

.DEFAULT_GOAL := help

# Colors for output
CYAN := \033[0;36m
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Docker Compose command
DOCKER_COMPOSE := docker-compose
COMPOSE_FILE := docker-compose.yml

help: ## Show this help message
	@echo "$(CYAN)STF AI Diagnosis Platform - Docker Stack Management$(NC)"
	@echo ""
	@echo "$(GREEN)Available commands:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(YELLOW)Quick Start:$(NC)"
	@echo "  1. Copy .env.example to .env and fill in passwords"
	@echo "  2. Run: make init"
	@echo "  3. Run: make up"
	@echo "  4. Check status: make health"

check-env: ## Check if .env file exists
	@if [ ! -f .env ]; then \
		echo "$(RED)Error: .env file not found!$(NC)"; \
		echo "$(YELLOW)Please copy .env.example to .env and configure it:$(NC)"; \
		echo "  cp .env.example .env"; \
		exit 1; \
	fi

init: check-env ## Initialize the environment (first-time setup)
	@echo "$(GREEN)Initializing STF AI Diagnosis Platform...$(NC)"
	@mkdir -p init-scripts
	@echo "$(GREEN)Pulling Docker images (this may take a while)...$(NC)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) pull
	@echo "$(GREEN)Building custom images...$(NC)"
	@if [ -d ../diagnostic_api ]; then \
		$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) build diagnostic-api; \
	else \
		echo "$(YELLOW)Warning: diagnostic_api directory not found, skipping build$(NC)"; \
	fi
	@echo "$(GREEN)Initialization complete!$(NC)"
	@echo "$(CYAN)Next step: Run 'make up' to start all services$(NC)"

pull: check-env ## Pull latest Docker images
	@echo "$(GREEN)Pulling Docker images...$(NC)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) pull

build: check-env ## Build custom Docker images
	@echo "$(GREEN)Building custom images...$(NC)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) build

up: check-env ## Start all services
	@echo "$(GREEN)Starting STF AI Diagnosis Platform...$(NC)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d
	@echo "$(GREEN)Services started!$(NC)"
	@echo "$(CYAN)Run 'make health' to check service status$(NC)"
	@echo "$(CYAN)Run 'make logs' to view logs$(NC)"
	@echo ""
	@echo "$(YELLOW)Access points:$(NC)"
	@echo "  Dify Web UI:      http://127.0.0.1:3000"
	@echo "  Dify API:         http://127.0.0.1:5001"
	@echo "  Diagnostic API:   http://127.0.0.1:8000"
	@echo "  Weaviate:         http://127.0.0.1:8080"
	@echo "  Ollama:           http://127.0.0.1:11434"

down: ## Stop all services
	@echo "$(YELLOW)Stopping STF AI Diagnosis Platform...$(NC)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down
	@echo "$(GREEN)Services stopped$(NC)"

restart: down up ## Restart all services

logs: ## Show logs from all services (Ctrl+C to exit)
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f

logs-api: ## Show logs from diagnostic API only
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f diagnostic-api

logs-dify: ## Show logs from Dify services
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f dify-api dify-worker dify-web

logs-ollama: ## Show logs from Ollama
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) logs -f ollama

ps: ## Show status of all containers
	@echo "$(CYAN)Container Status:$(NC)"
	@$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) ps

status: ps ## Alias for ps

health: ## Check health status of all services
	@echo "$(CYAN)Service Health Check:$(NC)"
	@echo ""
	@for service in postgres redis weaviate ollama dify-api dify-worker dify-web diagnostic-api; do \
		status=$$(docker inspect -f '{{.State.Health.Status}}' stf-$$service 2>/dev/null || echo "no healthcheck"); \
		if [ "$$status" = "healthy" ]; then \
			echo "  $(GREEN)✓$(NC) $$service: healthy"; \
		elif [ "$$status" = "no healthcheck" ]; then \
			running=$$(docker inspect -f '{{.State.Running}}' stf-$$service 2>/dev/null || echo "false"); \
			if [ "$$running" = "true" ]; then \
				echo "  $(YELLOW)?$(NC) $$service: running (no healthcheck)"; \
			else \
				echo "  $(RED)✗$(NC) $$service: not running"; \
			fi; \
		else \
			echo "  $(RED)✗$(NC) $$service: $$status"; \
		fi; \
	done

clean: down ## Stop services and remove containers (keeps volumes)
	@echo "$(YELLOW)Removing containers...$(NC)"
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down --remove-orphans
	@echo "$(GREEN)Cleanup complete (volumes preserved)$(NC)"

reset-volumes: ## DANGER: Remove all data volumes (requires confirmation)
	@echo "$(RED)WARNING: This will delete ALL data!$(NC)"
	@echo "$(YELLOW)This includes:$(NC)"
	@echo "  - Database data"
	@echo "  - Vector store data"
	@echo "  - Ollama models"
	@echo "  - Application logs"
	@echo ""
	@read -p "Type 'DELETE' to confirm: " confirm; \
	if [ "$$confirm" = "DELETE" ]; then \
		echo "$(RED)Stopping services and removing volumes...$(NC)"; \
		$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down -v; \
		echo "$(RED)All volumes deleted!$(NC)"; \
	else \
		echo "$(GREEN)Cancelled$(NC)"; \
	fi

backup: ## Create backup of persistent data
	@echo "$(GREEN)Creating backup...$(NC)"
	@mkdir -p backups
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	docker run --rm -v stf_postgres_data:/data -v $$(pwd)/backups:/backup \
		alpine tar czf /backup/postgres_$$timestamp.tar.gz -C /data .; \
	docker run --rm -v stf_weaviate_data:/data -v $$(pwd)/backups:/backup \
		alpine tar czf /backup/weaviate_$$timestamp.tar.gz -C /data .; \
	echo "$(GREEN)Backup complete: backups/*_$$timestamp.tar.gz$(NC)"

exec-postgres: ## Open psql shell in Postgres container
	@docker exec -it stf-postgres psql -U $${POSTGRES_USER:-dify_user} -d $${POSTGRES_DB:-dify}

exec-redis: ## Open redis-cli in Redis container
	@docker exec -it stf-redis redis-cli -a $${REDIS_PASSWORD}

shell-api: ## Open shell in diagnostic-api container
	@docker exec -it stf-diagnostic-api /bin/bash

shell-dify: ## Open shell in dify-api container
	@docker exec -it stf-dify-api /bin/bash

ollama-pull: ## Pull default Ollama model (llama3:8b)
	@echo "$(GREEN)Pulling Ollama model (this may take a while)...$(NC)"
	@docker exec -it stf-ollama ollama pull $${OLLAMA_DEFAULT_MODEL:-llama3:8b}
	@echo "$(GREEN)Model pulled successfully$(NC)"

ollama-list: ## List available Ollama models
	@docker exec stf-ollama ollama list

test-health: ## Run health check tests on all endpoints
	@echo "$(CYAN)Testing service endpoints...$(NC)"
	@echo ""
	@echo "Testing Postgres..."
	@docker exec stf-postgres pg_isready -U $${POSTGRES_USER:-dify_user} && \
		echo "$(GREEN)✓$(NC) Postgres is ready" || echo "$(RED)✗$(NC) Postgres failed"
	@echo ""
	@echo "Testing Redis..."
	@docker exec stf-redis redis-cli -a $${REDIS_PASSWORD} ping | grep -q PONG && \
		echo "$(GREEN)✓$(NC) Redis is ready" || echo "$(RED)✗$(NC) Redis failed"
	@echo ""
	@echo "Testing Weaviate..."
	@curl -sf http://127.0.0.1:8080/v1/.well-known/ready > /dev/null && \
		echo "$(GREEN)✓$(NC) Weaviate is ready" || echo "$(RED)✗$(NC) Weaviate failed"
	@echo ""
	@echo "Testing Ollama..."
	@curl -sf http://127.0.0.1:11434/api/version > /dev/null && \
		echo "$(GREEN)✓$(NC) Ollama is ready" || echo "$(RED)✗$(NC) Ollama failed"
	@echo ""
	@echo "Testing Dify API..."
	@curl -sf http://127.0.0.1:5001/health > /dev/null && \
		echo "$(GREEN)✓$(NC) Dify API is ready" || echo "$(RED)✗$(NC) Dify API failed"
	@echo ""
	@echo "Testing Diagnostic API..."
	@curl -sf http://127.0.0.1:8000/health > /dev/null && \
		echo "$(GREEN)✓$(NC) Diagnostic API is ready" || echo "$(RED)✗$(NC) Diagnostic API failed"

watch: ## Watch container status (refreshes every 2s)
	@watch -n 2 'docker-compose -f $(COMPOSE_FILE) ps'

smoke-test: ## Run comprehensive smoke tests
	@echo "$(GREEN)Running smoke tests...$(NC)"
	@bash smoke_test.sh

obd-agent-test: ## Run OBD agent tests (simulation only, no GPL deps)
	@echo "$(GREEN)Running OBD agent tests...$(NC)"
	@cd .. && pip install -q -r obd_agent/requirements-sim.txt && python -m pytest obd_agent/tests/ -v
	@echo "$(GREEN)OBD agent tests complete$(NC)"
