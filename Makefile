.PHONY: help install test run docker-up docker-down clean lint format db-migrate db-downgrade

help: ## Show this help message
	@echo "Pandit Booking Platform — Development Commands"
	@echo "================================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	pip install -r requirements.txt

dev: ## Start development server with auto-reload
	uvicorn main:app --reload --port 8000 --host 0.0.0.0

test: ## Run all tests with coverage
	pytest --cov=services --cov=shared --cov-report=html

test-unit: ## Run unit tests only
	pytest tests/ -m unit -v

test-integration: ## Run integration tests only
	pytest tests/ -m integration -v

test-fast: ## Run tests without coverage (faster)
	pytest tests/ -v --tb=short

test-debug: ## Run tests in debug mode
	pytest tests/ -v -s --pdb

lint: ## Lint Python code
	pylint services/ shared/ config/ || true
	flake8 services/ shared/ config/ || true

format: ## Format code with black and isort
	black services/ shared/ config/ tests/ main.py
	isort services/ shared/ config/ tests/ main.py

docker-build: ## Build Docker image
	docker build -t pandit-booking:latest .

docker-up: ## Start all services with Docker Compose
	docker-compose up -d
	@echo "✅ Services started. API: http://localhost:8000"

docker-down: ## Stop all Docker services
	docker-compose down

docker-logs: ## View Docker service logs
	docker-compose logs -f api

docker-clean: ## Remove Docker containers and volumes
	docker-compose down -v
	@echo "✅ Docker cleanup complete"

db-init: ## Initialize database (create tables)
	docker-compose exec api python -c "from config.database import init_db; import asyncio; asyncio.run(init_db())"

db-migrate: ## Create new Alembic migration
	alembic revision --autogenerate -m "$(msg)"

db-upgrade: ## Apply pending migrations
	alembic upgrade head

db-downgrade: ## Rollback last migration
	alembic downgrade -1

db-reset: ## Reset database (⚠️ deletes all data)
	docker-compose exec postgres dropdb pandit_db -U postgres
	docker-compose exec postgres createdb pandit_db -U postgres
	alembic upgrade head
	@echo "✅ Database reset complete"

redis-cli: ## Connect to Redis CLI
	docker-compose exec redis redis-cli

es-health: ## Check Elasticsearch health
	curl http://localhost:9200/_cluster/health | jq .

celery-worker: ## Start Celery worker (requires separate terminal)
	celery -A tasks.celery_app worker --loglevel=info

celery-flower: ## Start Flower dashboard for Celery (visit http://localhost:5555)
	celery -A tasks.celery_app flower

seed-data: ## Seed initial pooja data
	python -c "from main import seed_initial_data; import asyncio; asyncio.run(seed_initial_data())"

health-check: ## Check API health
	@curl http://localhost:8000/health | jq . 2>/dev/null || echo "❌ API not running"

docs: ## Open API documentation in browser
	@python -m webbrowser http://localhost:8000/docs

clean: ## Clean up Python cache and build files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .coverage -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name *.egg-info -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Cleanup complete"

freeze: ## Export current dependencies
	pip freeze > requirements.lock

# Development workflow
setup: install docker-up db-migrate seed-data ## Complete setup for development
	@echo "✅ Development environment ready!"
	@echo "   API: http://localhost:8000/docs"
	@echo "   Elasticsearch: http://localhost:9200"
	@echo "   Redis: localhost:6379"

# CI/CD
ci: lint test docker-build ## Run all CI checks
	@echo "✅ All CI checks passed!"
