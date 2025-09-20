@echo off
REM F1 Telemetry API Deployment Script for Windows

echo 🏎️  F1 Telemetry API Production Deployment
echo ==========================================

REM Create necessary directories
echo 📁 Creating necessary directories...
if not exist logs mkdir logs
if not exist ssl mkdir ssl

REM Build and deploy with Docker Compose
echo 🐳 Building and starting Docker containers...
docker-compose down --remove-orphans
docker-compose build --no-cache
docker-compose up -d

REM Wait for services to be ready
echo ⏳ Waiting for services to start...
timeout /t 10 /nobreak >nul

REM Check if API is responding
echo 🔍 Checking API health...
for /l %%i in (1,1,30) do (
    curl -f http://localhost:8000/api/health >nul 2>&1
    if not errorlevel 1 (
        echo ✅ API is healthy!
        goto :healthy
    ) else (
        echo ⏳ Waiting for API to be ready... (attempt %%i/30)
        timeout /t 5 /nobreak >nul
    )
)

echo ❌ API failed to start properly
docker-compose logs f1-telemetry-api
exit /b 1

:healthy
REM Show running containers
echo 📊 Container Status:
docker-compose ps

echo.
echo 🎉 Deployment completed successfully!
echo 📡 API is available at: http://localhost
echo 📊 Health check: http://localhost/api/health
echo 📖 API docs: http://localhost/docs (if not in production mode)
echo.
echo 📋 Useful commands:
echo   View logs: docker-compose logs -f
echo   Stop services: docker-compose down
echo   Restart services: docker-compose restart
