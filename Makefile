.PHONY: help install test test-unit test-integration test-fast test-cov lint clean build run deploy deploy-dev backup backup-list backup-verify restore

help:
	@echo "F1 Telemetry API - Available Commands"
	@echo ""
	@echo "  make install    Install dependencies"
	@echo "  make test       Run tests"
	@echo "  make lint       Check code quality"
	@echo "  make clean      Clean generated files"
	@echo "  make build      Build Docker image"
	@echo "  make run-image  Run API using Docker image with env vars from .env"
	@echo "  make deploy-dev Run API + MongoDB with docker-compose.dev.yml"
	@echo "  make run        Run API locally"
	@echo "  make deploy     Deploy with Docker Compose"
	@echo ""

install:
	pip install --upgrade pip
	pip install -r requirements.txt
	pip install -e ".[dev]"

test:
	pytest tests/ -v

# Fast targets bypass the coverage gate (and don't require pytest-cov to be installed).
test-unit:
	pytest tests/unit -v -o addopts=

test-integration:
	pytest tests/integration -v -o addopts=

test-fast:
	pytest -m "not slow" -n auto -o addopts=

test-cov:
	pytest --cov-report=html

lint:
	flake8 src/ server.py --max-line-length=120

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache htmlcov .coverage

build:
	docker build -t t1api:latest .

run-image:
	docker run --env-file .env -p 5000:5000 t1api:latest

run:
	.\.venv\Scripts\activate.ps1
	python server.py

deploy:
	docker-compose up -d

deploy-dev:
	docker-compose -f docker-compose.dev.yml up --build

# ── Backup / Restore ────────────────────────────────────────────────────
# Manual one-off backup (uses the same config as the scheduled run).
backup:
	python -c "from src.services.backup.runner import BackupRunner; r=BackupRunner().run_full(); print('OK', r.manifest.backup_id)"

backup-list:
	python -m src.workers.restore_cli --list

# Verify checksums of a specific backup without restoring.
#   make backup-verify BACKUP_ID=2026-06-06T02-00-00Z
backup-verify:
	python -m src.workers.restore_cli --backup-id $(BACKUP_ID) --verify-only

# Safe restore drill into a throwaway Mongo DB (does not touch production).
#   make restore BACKUP_ID=2026-06-06T02-00-00Z TARGET_DB=T1API_DB_RESTORE_TEST
restore:
	python -m src.workers.restore_cli --backup-id $(BACKUP_ID) --mongo-target-db $(TARGET_DB)
