# Cogent Agent - Makefile

.PHONY: help build up down logs shell cli test lint format clean

# Default target
help:
	@echo "Cogent Agent - Available Commands"
	@echo ""
	@echo "Docker Commands:"
	@echo "  make build     - Build Docker images"
	@echo "  make up        - Start all services"
	@echo "  make down      - Stop all services"
	@echo "  make logs      - View logs"
	@echo "  make shell     - Shell into agent container"
	@echo "  make cli       - Run CLI client"
	@echo ""
	@echo "Development Commands:"
	@echo "  make install   - Install dependencies locally"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run linters"
	@echo "  make format    - Format code"
	@echo "  make clean     - Clean build artifacts"

# Docker commands
build:
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f

logs-agent:
	docker-compose logs -f agent

logs-nats:
	docker-compose logs -f nats

shell:
	docker-compose exec agent /bin/bash

cli:
	docker-compose run --rm cli

# Start with rebuild
restart: down build up

# Development commands
install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=src --cov-report=html

lint:
	ruff check src/
	mypy src/

format:
	black src/
	ruff check --fix src/

clean:
	rm -rf __pycache__ .pytest_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# NATS management
nats-status:
	@curl -s http://localhost:8222/varz | jq .

nats-streams:
	nats stream ls --server=nats://localhost:4222

nats-kv:
	nats kv ls --server=nats://localhost:4222

# Quick setup for new developers
setup:
	cp -n .env.example .env || true
	mkdir -p workspace
	@echo "Setup complete. Edit .env with your credentials, then run 'make up'"
