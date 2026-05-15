.PHONY: help install test test-unit test-integration test-fast test-cov lint clean build run deploy

help:
	@echo "F1 Telemetry API - Available Commands"
	@echo ""
	@echo "  make install    Install dependencies"
	@echo "  make test       Run tests"
	@echo "  make lint       Check code quality"
	@echo "  make clean      Clean generated files"
	@echo "  make build      Build Docker image"
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

run:
	python server.py

deploy:
	docker-compose up -d
