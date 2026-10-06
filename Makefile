.PHONY: help up down dev-api dev-web test lint migrate bootstrap

help:
	@echo "QA AgentOS — Makefile Commands"
	@echo "  make up          - Start local Docker Compose stack"
	@echo "  make down        - Stop Docker Compose stack"
	@echo "  make dev-api     - Run FastAPI dev server"
	@echo "  make dev-web     - Run Next.js dev server"
	@echo "  make test        - Run Python backend tests"
	@echo "  make lint        - Run linters and type checkers"
	@echo "  make migrate     - Run database migrations"
	@echo "  make bootstrap    - Seed default admin user and demo project"

up:
	docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d --build

down:
	docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml down

dev-api:
	cd apps/api && python -m uvicorn qa_agentos.main:app --reload --port 8000

dev-web:
	pnpm --filter web dev

test:
	python -m pytest apps/api/tests

lint:
	python -m ruff check apps/api
	python -m mypy apps/api/src
	pnpm --filter web lint
	pnpm --filter web typecheck

migrate:
	cd apps/api && alembic upgrade head

bootstrap:
	python -m qa_agentos.core.bootstrap
