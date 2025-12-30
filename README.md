<div align="center">

# 🏎️ T1API
## Formula 1 Telemetry & Analytics Platform

**Motorsport Data Intelligence**

[![API Status](https://img.shields.io/badge/status-production-success?style=for-the-badge)](https://api.t1f1.com)
[![Uptime](https://img.shields.io/badge/uptime-99.9%25-blue?style=for-the-badge)](https://api.t1f1.com)
[![License](https://img.shields.io/badge/license-Proprietary-red.svg?style=for-the-badge)](LICENSE)

**[Request API Access](https://turnonehub.com) • [Interactive Docs](https://docs.t1f1.com) • [Support Portal](https://turnonehub.com/contact)**

</div>

---

## 📋 Table of Contents

- [About T1API](#-about-t1api)
- [Why Choose T1API](#-why-choose-t1api)
- [Core Capabilities](#-core-capabilities)
- [Getting Started](#-getting-started)
- [API Reference](#-api-reference)
- [Authentication](#-authentication)
- [Rate Limits & Tiers](#-rate-limits--tiers)
- [Use Cases](#-use-cases)
- [Integration Examples](#-integration-examples)
- [Service Architecture](#-service-architecture)
- [Terms & Compliance](#-terms--compliance)
- [Support & Contact](#-support--contact)

---

## 🎯 About T1API

**T1API** is Turn One's flagship Formula 1 data intelligence platform, delivering production-grade telemetry analysis and visualization services to developers, analysts, and motorsport organizations worldwide.

### What We Deliver

Built on years of motorsport data engineering expertise, T1API transforms raw F1 telemetry into actionable insights through a robust, scalable RESTful architecture. Our platform processes millions of data points per race weekend, serving both real-time analysis and comprehensive historical datasets spanning multiple F1 seasons.

**Trusted by:** Race analysts, media companies, educational institutions, and F1 enthusiast developers building next-generation motorsport applications.

### Our Mission

To democratize access to professional-grade Formula 1 telemetry analysis while maintaining reliability, performance, and data accuracy standards that motorsport professionals demand.

---

##  Why Choose T1API

### **Enterprise Reliability**
- **99.9% Uptime SLA** - Production-grade infrastructure with redundancy
- **Automated Session Processing** - New race data available within minutes of session completion
- **Intelligent Caching** - Sub-500ms response times for frequently accessed data
- **Comprehensive Monitoring** - Real-time health checks and performance metrics

### **Advanced Analytics Engine**
- **Multi-Metric Telemetry** - Speed, throttle, brake, gear, RPM, and DRS data
- **Precision Timing Analysis** - Millisecond-accurate lap and sector timing
- **Spatial Intelligence** - Track-mapped telemetry with position correlation
- **Statistical Modeling** - Distribution analysis, consistency metrics, and performance benchmarking

### **Developer-First Design**
- **RESTful Architecture** - Intuitive endpoints following industry best practices
- **Dual Response Formats** - PNG visualizations and JSON data exports
- **Interactive Documentation** - Swagger UI with live testing capabilities
- **Tiered Access Control** - Flexible rate limits for different usage profiles

### **Proven Track Record**
- **Multi-Season Coverage** - Historical data from 2024 onwards
- **Real-Time Processing** - Live session support with automatic updates
- **Battle-Tested** - Powers production dashboards at t1f1.com and turnonehub.com
- **Industry Expertise** - Built by fans with deep F1 data domain knowledge

---

##  Core Capabilities

### **Telemetry Analysis Suite**

**Speed Analytics**
- Peak velocity detection across track sectors
- Speed trap comparisons and rankings
- Acceleration profile analysis
- Corner entry/exit speed differentials

**Driver Input Intelligence**
- Throttle application patterns and consistency
- Braking point analysis and force profiling
- Steering input correlation with lap performance
- Gear shift optimization insights

**Timing & Performance**
- Sector-by-sector breakdown with microsecond precision
- Qualifying session progression analysis
- Lap time distribution and consistency metrics
- Race pace evolution and degradation tracking

**Comparative Analytics**
- Head-to-head driver telemetry overlays
- Team performance benchmarking
- Multi-session trend analysis
- Historical performance comparisons


## **Use Cases**

### **Media & Broadcasting**
Build real-time race analysis dashboards, generate broadcast-ready graphics, and create data-driven race commentary tools for digital and traditional media outlets.

### **Professional Race Analysis**
Power team strategy applications, driver coaching platforms, and performance optimization tools with millisecond-accurate telemetry and comprehensive session data.

### **Fan Engagement Platforms**
Create immersive F1 companion apps, fantasy league integrations, and interactive race viewing experiences that bring fans closer to the technical aspects of racing.

### **Academic Research**
Support motorsport engineering studies, machine learning model development, and sports analytics research with extensive historical and real-time datasets.

### **Business Intelligence**
Develop predictive analytics models, performance trending systems, and competitive intelligence platforms for motorsport-related business applications.

### **Content Creation**
Generate data visualizations for social media, blog articles, video content, and podcasts with professional-quality charts and telemetry insights.


## 🛠️ Tech Stack

### Core Framework
- **[FastAPI](https://fastapi.tiangolo.com/)** - Modern, high-performance web framework
- **[Uvicorn](https://www.uvicorn.org/)** - ASGI server for production deployment
- **Python 3.9+** - Primary programming language

### Data & Analysis
- **[FastF1](https://github.com/theOehrly/Fast-F1)** - Official F1 telemetry data library
- **[Pandas](https://pandas.pydata.org/)** - Data manipulation and analysis
- **[NumPy](https://numpy.org/)** - Numerical computing
**1. Request API Access**

Visit **[turnonehub.com](https://turnonehub.com)** to request your API credentials. We offer multiple access tiers to suit different usage profiles:

- **Standard Tier** - Ideal for personal projects and development
- **Premium Tier** - High-volume access for production applications
- **Enterprise Tier** - Custom solutions with dedicated support

### **2. Obtain Your API Key**

Upon approval, you'll receive:
- Unique API key for authentication
- Access tier assignment
- Rate limit allocation
- Welcome documentation package

### **3. Make Your First Request**

```bash
curl -H "X-API-Key: your-api-key" \
  "https://api.t1f1.com/api/health"
```

**Expected Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "environment": "production",
  "checks": {
    "api": "healthy",
    "background_processor": "running"
  }
}
```

### **4. Explore Interactive Documentation**

Visit **[docs.t1f1.com](https://docs.t1f1.com)** for:
- Complete endpoint reference
- Live API testing interface
- Request/response examples
- Authentication setup guide

### **Base URL**
Reference

### **Base Endpoint**
```
https://api.t1f1.com
```

### **Interactive Documentation**
- **Swagger UI**: [api.t1f1.com/docs](https://api.t1f1.com/docs)
- **ReDoc**: [api.t1f1.com/redoc](https://api.t1f1.com/redoc)



### Core Endpoints

#### General

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| `/` | GET | Welcome message and API info | No |
| `/api/health` | GET | Health check and system status | No |
| `/api/daily-data` | GET | Daily session summary | Yes |
| `/api/dashboard` | GET | Latest session analysis dashboard | Yes |

#### Simple Analysis

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/top-speed-plot` | GET | PNG: Top speed comparison chart |
| `/api/top-speed-data` | GET | JSON: Raw top speed data |
---

## 💡 Integration Examples

### **Quick Start: Get Latest Session Data**
```bash
curl -H "X-API-Key: your-api-key" \
  "https://api.t1f1.com/api/dashboard"
```

**Response:** Complete analysis package for the most recent F1 session, including timing data, telemetry summaries, and key performance metrics.

---

### **Retrieve Qualifying Top Speeds**
```bash
curl -H "X-API-Key: your-api-key" \
  "https://api.t1f1.com/api/top-speed-data?year=2025&gp=3&session=Q"
```

**Use Case:** Build leaderboards, identify speed trap winners, analyze straight-line performance.

---

### **Generate Driver Comparison Visualization**
```bash
curl -H "X-API-Key: your-api-key" \
  "https://api.t1f1.com/api/track-comparison-2drivers-plot?year=2025&gp=3&session=Q&driver1=VER&driver2=HAM" \
  --output comparison.png
```

**Returns:** Professional PNG visualization showing telemetry overlay on track map.

---

### **Python Integration Example**
```python
import requests

API_BASE = "https://api.t1f1.com"
API_KEY = "your-api-key"
headers = {"X-API-Key": API_KEY}

# Get qualifying results data
response = requests.get(
    f"{API_BASE}/api/qualifying-results-data",
    headers=headers,
)
```
We implement intelligent rate limiting to ensure fair access and optimal service performance for all users.

### **Access Tiers**

| Tier | Rate Limit | Burst Capacity | Use Case | Pricing |
|------|-----------|----------------|----------|---------|
| **Public** | 30/min<br>500/hour | Low | API exploration, testing | Free |
| **Standard** | 100/min<br>2,000/hour | Medium | Personal projects, development | Contact Sales |
| **Premium** | 300/min<br>10,000/hour | High | Production applications | Contact Sales |
| **Enterprise** | Custom | Custom | Mission-critical systems | Contact Sales |

**Data-Intensive Endpoints:** Telemetry data exports and bulk analysis operations have separate rate limits (60/min) across all tiers to ensure consistent performance.

### **Rate Limit Response Headers**

Every API response includes rate limit information:

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1735516800
```

### **Handling Rate Limits**

**HTTP 429 Response:**
```json
{
  "detail": "Rate limit exceeded",
  "retry_after": 42
}
```

**Best Practices:**
- Implement exponential backoff for retries
- Cache responses when appropriate
- Monitor your usage via response headers
- Contact us for tier upgrades if needed


### **High-Availability Infrastructure**

T1API is built on a modern, cloud-native architecture designed for reliability and performance:

**Core Components:**
- **FastAPI Application Layer** - Async request handling with sub-second response times
- **MongoDB Atlas** - Globally distributed session metadata and tracking
- **Redis Cache Layer** - Intelligent caching for frequently accessed datasets
- **Background Processing Engine** - Automated post-session analysis and data preparation

**Performance Characteristics:**
- **< 100ms** - Cached data response time (95th percentile)
- **< 2s** - Cold data generation time for standard queries
- **99.9%** - Service uptime SLA
- **Auto-scaling** - Dynamic resource allocation based on demand

### **Data Pipeline**

```
F1 Live Timing → T1API Processing Engine → 
→ Data Validation & Cleaning → Cache Storage → API Response
```

**Key Features:**
- Real-time session monitoring
- Automatic anomaly detection
- Multi-layer data validation
- Comprehensive error handling
- Audit logging and traceability

### **Security Architecture**

- **API Key Authentication** - Secure, revocable access tokens
- **Rate Limiting** - Multi-tier protection against abuse
- **CORS Protection** - Configurable origin restrictions
- **TLS/SSL Encryption** - All data in transit encrypted
- **Request Validation** - Pydantic-based input sanitization
- **Audit Logging** - Complete request/response tracking  

### Production Considerations

- **SSL/TLS**: Use Let's Encrypt or commercial certificates
- **Monitoring**: Integrate Sentry for error tracking
- **Logging**: Configure log rotation and aggregation
- **Backups**: Regular MongoDB backups
- **Scaling**: Use multiple workers with load balancing

---

## ⚖️ Legal & Licensing

### License

**Proprietary - Internal Use Only**

This software is proprietary and confidential. Unauthorized copying, distribution, modification, or use of this software, via any medium, is strictly prohibited without explicit written permission from the copyright holder.

### Copyright

© 2023-2026 Turn One. All rights reserved.

### Data Sources

This API uses Formula 1 timing and telemetry data. All F1-related data is subject to Formula One Management's terms and conditions.

**Important**: This API is intended for:
- Personal use
- Educational purposes
- Non-commercial applications
- Internal analytics


### Disclaimer

This software is provided "as is" without warranty of any kind, express or implied. The authors are not responsible for any damages or liabilities associated with the use of this software.


---

## 🤝 Support

### Documentation

- **API Docs**: `/docs` (Swagger UI)
- **ReDoc**: `/redoc` (Alternative documentation)


### Contact

- **Website**: [https://turnonehub.com](https://turnonehub.com)
- **Support**: [https://turnonehub.com/contact](https://turnonehub.com/contact)
- **Email**: contact@t1f1.com

### Community

- Report issues through the proper channels
- Contribute improvements (authorized users only)
- Share feedback and feature requests

---

<div align="center">

**Built with ❤️ for the F1 community**

[⬆ Back to Top](#️-t1api---formula-1-telemetry-api)

</div>