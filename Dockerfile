# # Use Python 3.12 slim image as base
# FROM python:3.12-slim-bookworm

# # Set environment variables
# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1 \
#     DEBIAN_FRONTEND=noninteractive \
#     PYTHONPATH=/app

# # Set work directory
# WORKDIR /app

# # Install system dependencies
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     gcc \
#     g++ \
#     make \
#     libffi-dev \
#     libssl-dev \
#     curl \
#     && rm -rf /var/lib/apt/lists/*

# # Copy requirements first for better caching
# COPY requirements.txt .

# # Install Python dependencies
# RUN pip install --no-cache-dir --upgrade pip && \
#     pip install --no-cache-dir -r requirements.txt

# # Create necessary directories
# RUN mkdir -p /app/cache /app/outputs/plots /app/outputs/data /app/lib /app/src /app/data /app/logs

# # Copy application code
# COPY src/ ./src/
# COPY lib/ ./lib/
# COPY data/ ./data/
# COPY docs_auth.py .
# COPY server.py .

# # Ensure directories have proper permissions
# RUN chmod -R 777 /app/logs /app/cache /app/outputs /app/data

# # Create volume mount points
# VOLUME ["/app/cache", "/app/outputs", "/app/data", "/app/logs"]

# # Expose port
# ARG DOCKER_EXPOSED_PORT=5000
# EXPOSE $DOCKER_EXPOSED_PORT

# # Health check
# HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
#     CMD curl -f http://localhost:$DOCKER_EXPOSED_PORT/api/health || exit 1

# # Run the application with uvicorn
# CMD ["python", "server.py"]


#################################################### NEW DOCKER

# ── Metadata ──────────────────────────────────────────────
ARG PYTHON_VERSION=3.12.9
ARG APP_PORT=5000

# ── Stage 1: builder ──────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Instalează build tools (rămân DOAR în builder)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip + compilează wheel-uri
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_PORT=5000

WORKDIR /app

# Instalează doar ce e necesar la runtime (curl pentru healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiază wheel-urile compilate din builder și instalează fără compilare
COPY --from=builder /wheels /wheels
COPY --from=builder /build/requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Creare root user
RUN mkdir -p /app/cache /app/outputs/plots /app/outputs/data \
             /app/lib /app/src /app/data /app/logs && \
    chmod -R 777 /app/logs /app/cache /app/outputs /app/data

COPY src/ ./src/
COPY lib/ ./lib/
COPY data/ ./data/
COPY docs_auth.py .
COPY server.py .

# Setează volume-uri (după chown!)
VOLUME ["/app/cache", "/app/outputs", "/app/data", "/app/logs"]

EXPOSE ${APP_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${APP_PORT}/api/health || exit 1

CMD ["python", "server.py"]
