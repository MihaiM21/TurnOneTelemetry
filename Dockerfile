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

# Install build tools (only in builder)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip + compile wheels for all dependencies (including transitive)
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

# Install only runtime dependencies (no build tools) using precompiled wheels.
# ``mongodb-database-tools`` provides ``mongodump``/``mongorestore`` for the
# backup subsystem.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    && curl -fsSL https://pgp.mongodb.com/server-7.0.asc | gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor \
    && echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
        > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update && apt-get install -y --no-install-recommends mongodb-database-tools \
    && apt-get purge -y --auto-remove gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy the precompiled wheels and install dependencies without recompilation
COPY --from=builder /wheels /wheels
COPY --from=builder /build/requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Create root user
RUN mkdir -p /app/cache /app/outputs/plots /app/outputs/data \
             /app/assets /app/src /app/data /app/logs && \
    chmod -R 777 /app/logs /app/cache /app/outputs /app/data

COPY src/ ./src/
COPY assets/ ./assets/
COPY server.py .

# Set volume directories (after chown!)
VOLUME ["/app/cache", "/app/outputs", "/app/data", "/app/logs"]

EXPOSE ${APP_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${APP_PORT}/api/health || exit 1

CMD ["python", "server.py"]
