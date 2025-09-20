# F1 Telemetry API - Production Deployment Guide

This guide explains how to deploy the F1 Telemetry API using Docker for production environments.

## 🚀 Quick Deployment

### Prerequisites
- Docker and Docker Compose installed
- At least 2GB RAM available
- Port 80 and 8000 available

### Deploy on Linux/macOS
```bash
chmod +x deploy.sh
./deploy.sh
```

### Deploy on Windows
```cmd
deploy.bat
```

## 📋 Manual Deployment Steps

### 1. Prepare Environment
```bash
# Create necessary directories
mkdir -p logs ssl

# Set environment
export ENVIRONMENT=production
```

### 2. Build and Start Services
```bash
# Build the Docker image
docker-compose build

# Start services
docker-compose up -d
```

### 3. Verify Deployment
```bash
# Check health
curl http://localhost/api/health

# View logs
docker-compose logs -f f1-telemetry-api
```

## 🌐 Production Configuration

### Environment Variables
Create `.env.production` file with:
```env
ENVIRONMENT=production
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4
CORS_ORIGINS=https://your-domain.com
LOG_LEVEL=INFO
```

### SSL/HTTPS Setup
1. Place SSL certificates in `./ssl/` directory:
   - `cert.pem` - SSL certificate
   - `private.key` - Private key

2. Uncomment HTTPS section in `nginx.conf`

3. Update your domain in the configuration

### CORS Configuration
Update the CORS origins in `server.py`:
```python
cors_origins.extend([
    "https://your-frontend-domain.com",
    "https://www.your-frontend-domain.com"
])
```

## 📊 Monitoring and Maintenance

### View Logs
```bash
# All services
docker-compose logs -f

# API only
docker-compose logs -f f1-telemetry-api

# Nginx only
docker-compose logs -f nginx
```

### Container Management
```bash
# Restart services
docker-compose restart

# Stop services
docker-compose down

# Update and redeploy
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Database Backup
```bash
# Backup SQLite database
cp session_analytics.db session_analytics_backup_$(date +%Y%m%d).db
```

## 🔧 Performance Tuning

### For High Traffic
1. Increase worker count in Dockerfile:
   ```dockerfile
   CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "8"]
   ```

2. Add Redis for caching (optional):
   ```yaml
   redis:
     image: redis:alpine
     restart: unless-stopped
   ```

### Resource Limits
Add to docker-compose.yml:
```yaml
deploy:
  resources:
    limits:
      memory: 2G
      cpus: '1.0'
```

## 🐛 Troubleshooting

### Common Issues

**API not responding:**
```bash
docker-compose logs f1-telemetry-api
docker-compose restart f1-telemetry-api
```

**Permission errors:**
```bash
sudo chown -R $USER:$USER cache outputs logs
chmod 755 cache outputs logs
```

**Port conflicts:**
```bash
# Check what's using port 80
sudo netstat -tulpn | grep :80
```

### Health Checks
- API Health: `http://localhost/api/health`
- Container Status: `docker-compose ps`
- Resource Usage: `docker stats`

## 📈 Scaling for Production

### Horizontal Scaling
```yaml
f1-telemetry-api:
  deploy:
    replicas: 3
  scale: 3
```

### Load Balancer Configuration
Update nginx.conf upstream:
```nginx
upstream f1_api {
    server f1-telemetry-api_1:8000;
    server f1-telemetry-api_2:8000;
    server f1-telemetry-api_3:8000;
}
```

## 🔐 Security Considerations

1. **Firewall Rules**: Only expose ports 80/443
2. **SSL/TLS**: Use valid certificates
3. **Rate Limiting**: Configured in nginx.conf
4. **Updates**: Regularly update base images
5. **Monitoring**: Set up log monitoring

## 📞 Support

For issues or questions:
- Check logs: `docker-compose logs -f`
- Monitor resources: `docker stats`
- API documentation: `http://localhost/docs` (development only)
