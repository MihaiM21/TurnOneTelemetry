#!/bin/bash

# F1 Telemetry API Deployment Script

set -e

echo "🏎️  F1 Telemetry API Production Deployment"
echo "=========================================="

# Create necessary directories
echo "📁 Creating necessary directories..."
mkdir -p logs ssl

# Set correct permissions for logs directory
chmod 755 logs

# Build and deploy with Docker Compose
echo "🐳 Building and starting Docker containers..."
docker-compose down --remove-orphans
docker-compose build --no-cache
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 10

# Check if API is responding
echo "🔍 Checking API health..."
for i in {1..30}; do
    if curl -f http://localhost:8000/api/health > /dev/null 2>&1; then
        echo "✅ API is healthy!"
        break
    else
        echo "⏳ Waiting for API to be ready... (attempt $i/30)"
        sleep 5
    fi

    if [ $i -eq 30 ]; then
        echo "❌ API failed to start properly"
        docker-compose logs f1-telemetry-api
        exit 1
    fi
done

# Show running containers
echo "📊 Container Status:"
docker-compose ps

echo ""
echo "🎉 Deployment completed successfully!"
echo "📡 API is available at: http://localhost"
echo "📊 Health check: http://localhost/api/health"
echo "📖 API docs: http://localhost/docs (if not in production mode)"
echo ""
echo "📋 Useful commands:"
echo "  View logs: docker-compose logs -f"
echo "  Stop services: docker-compose down"
echo "  Restart services: docker-compose restart"
