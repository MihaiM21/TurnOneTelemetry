.PHONY: help install test lint clean build run deploy

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

test:
	pytest tests/ -v

lint:
	flake8 src/ server.py --max-line-length=120

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache htmlcov .coverage

build:
	docker build -t f1-telemetry-api:latest .

run:
	python server.py

deploy:
	docker-compose up -d
