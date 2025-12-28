# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p /app/cache /app/outputs/plots /app/outputs/data /app/lib /app/src /app/data /app/logs

# Copy application code
COPY src/ ./src/
COPY lib/ ./lib/
COPY data/ ./data/
COPY server.py .

# Ensure directories have proper permissions
RUN chmod -R 777 /app/logs /app/cache /app/outputs /app/data

# Create volume mount points
VOLUME ["/app/cache", "/app/outputs", "/app/data", "/app/logs"]

# Expose port
ARG DOCKER_EXPOSED_PORT=5000
EXPOSE $DOCKER_EXPOSED_PORT

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:$DOCKER_EXPOSED_PORT/api/health || exit 1

# Run the application with uvicorn
CMD ["python", "server.py"]
