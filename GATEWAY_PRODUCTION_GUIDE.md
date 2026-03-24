# Production-Ready Gateway Architecture

## Overview

Your application now has a **resilient, highly-available API gateway** architecture suitable for production deployments. This document explains the changes and how to use them.

## Architecture Diagram

```
         NGINX Load Balancer (Port 80)
              (Reverse Proxy)
         ↙        ↓        ↘
      API1      API2      API3    (FastAPI Instances)
         ↘        ↓        ↙
    PostgreSQL, Redis, Elasticsearch
```

## Key Components

### 1. **NGINX Reverse Proxy & Load Balancer** (`nginx.conf`)
- **Single Entry Point**: All traffic flows through NGINX on port 80
- **Load Balancing**: Distributes requests across 3 API instances using least-conn algorithm
- **Circuit Breaking**: Marks instances as down after 3 failed health checks (30s timeout)
- **Rate Limiting**: 
  - Public endpoints: 100 req/min per IP
  - Authenticated endpoints: 600 req/min per user token
- **Caching**: Caches GET requests to public endpoints (5 min TTL)
- **Timeouts**: 10s connection, 30s read, prevents hanging requests
- **Gzip Compression**: Automatic response compression
- **Enhanced Logging**: JSON-formatted logs with timing info

### 2. **Multiple FastAPI Instances** (docker-compose.yml)
- **3 Instances**: api1, api2, api3 for redundancy
- **Health Checks**: Every 10s with 20s startup grace period
- **Auto-Restart**: Automatically restarts on failure
- **Structured Logging**: JSON logs with instance name for easy debugging
- **Individual Failure Isolation**: One instance failure doesn't affect others

### 3. **Enhanced Gateway Features** (main.py)

#### a. **Circuit Breaker Pattern**
```python
CircuitBreakerManager  # Manages failures for downstream services
- Fails open after 5 failures
- Attempts recovery after 60 seconds
- Prevents cascading failures
```

#### b. **Advanced Rate Limiting**
```
Skip paths (unlimited):
- /health          (health checks)
- /payments/webhook (payment webhooks)
- /metrics         (Prometheus metrics)
- /docs, /redoc    (API documentation)

Protected by NGINX:
- Authenticated users: 600 req/min per token
- Unauthenticated users: 100 req/min per IP
- Burst allowance: 50-100 requests

Behavior at limit:
- Returns 429 with "Retry-After: 60" header
- Logs rate limit violations
```

#### c. **Request Tracking**
```
Headers added to every response:
- X-Request-ID        (UUID for distributed tracing)
- X-Process-Time      (Response time in milliseconds)
- X-Cache-Status      (From NGINX: HIT/MISS/EXPIRED)
```

#### d. **Prometheus Metrics**
```
Available at: http://localhost/metrics

Tracks:
- Request count by endpoint
- Response time percentiles (p50, p95, p99)
- Error rates by status code
- Request size and response size
```

#### e. **Structured Logging**
```json
{
  "timestamp": "2026-02-28 10:30:45",
  "level": "ERROR",
  "logger": "main",
  "message": "Rate limit exceeded for IP 192.168.1.1",
  "instance": "api1"
}
```

## How to Deploy

### 1. **Update Dependencies**
```bash
pip install -r requirements.txt
```

New packages added:
- `pybreaker==1.4.0` - Circuit breaker pattern
- `tenacity==8.2.3` - Retry mechanism with exponential backoff
- `prometheus-client==0.19.0` - Prometheus metrics export

### 2. **Start All Services**
```bash
# Start with NGINX load balancer
docker-compose up --build

# Or with monitoring tools (Kibana, Flower)
docker-compose --profile dev-tools up --build
```

### 3. **Verify Setup**
```bash
# API is available at
curl http://localhost/health

# Metrics available at
curl http://localhost/metrics

# Logs from all instances
docker-compose logs -f api1 api2 api3

# NGINX logs
docker-compose logs -f nginx
```

## Configuration Files

### nginx.conf
- Upstream servers with fail_max=3, fail_timeout=30s
- Rate limiting zones
- Cache settings
- Proxy timeouts
- Error handling

### Dockerfile.nginx
- NGINX Alpine image
- Custom configuration
- Health checks

### docker-compose.yml
- 3 FastAPI instances with health checks
- NGINX service depending on all API instances
- Structured logging (10MB max per file, 3 file rotation)
- Network isolation: all services on pandit_network

### main.py
- CircuitBreakerManager for service resilience
- JSONFormatter for structured logging
- Advanced rate limiting middleware
- Enhanced exception handling
- Prometheus instrumentation

## Monitoring & Observability

### Health Checks (Every 10 seconds)
```bash
# Manual health check
curl http://localhost/health

# Response example:
{
  "status": "ok",
  "version": "1.0.0",
  "database": "ok",
  "redis": "ok"
}
```

### Metrics (Prometheus)
```bash
curl http://localhost/metrics | head -20

# Parse for Grafana or other tools
```

### Logs
```bash
# All instances
docker-compose logs -f

# Specific instance
docker-compose logs -f api1

# NGINX access logs
docker-compose exec nginx cat /var/log/nginx/access.log
```

## Failure Scenarios & Recovery

### Scenario 1: One API Instance Crashes
- NGINX detects failure (health check timeout)
- Marks instance as "down"
- Routes all traffic to remaining 2 instances
- Attempts recovery every 30 seconds
- User impact: **Zero** (others handle load)

### Scenario 2: All Instances Down
- NGINX returns 503 Service Unavailable
- Response: `{"detail": "Service temporarily unavailable", "status": "degraded"}`
- Clients should retry with exponential backoff

### Scenario 3: Downstream Service (DB/Redis) Down
- Circuit breaker opens after 5 failed requests
- Returns 503 to new requests
- Attempts recovery every 60 seconds
- No cascading failures to other services

### Scenario 4: Rate Limit Hit
- Returns 429 Too Many Requests
- Includes "Retry-After: 60" header
- Client should wait 1 minute before retrying

## Performance Tuning

### For High Traffic (>1000 req/sec)
```yaml
# docker-compose.yml
api1/api2/api3:
  command: uvicorn main:app --workers 4 --port 8000

# nginx.conf
worker_processes auto;  # Uses CPU count
worker_connections 4096;  # Increase per worker
```

### For Low Latency
```yaml
# Enable read while cache-miss (streaming)
proxy_cache_use_stale updating;

# Reduce buffer sizes
proxy_buffer_size 2k;
proxy_buffers 4 2k;
```

### For Memory Optimization
```yaml
# Reduce cache size
proxy_cache_path ... max_size=100m;

# Reduce connection pooling
keepalive 16;  # Default 32
```

## Security Best Practices

1. **CORS**: Configured in main.py - restrict to your domain
2. **HTTPS**: Configure certificates in NGINX (outside scope)
3. **Rate Limiting**: Prevents DDoS attacks
4. **Circuit Breaker**: Prevents cascading failures
5. **Structured Logging**: Audit trail for security events
6. **Health Checks**: Only /health returns full status
7. **Metrics Endpoint**: Restrict /metrics to internal networks

## Troubleshooting

### **Issue: "502 Bad Gateway"**
```bash
# Check if API instances are healthy
docker-compose ps

# View API instance logs
docker-compose logs api1

# Check NGINX logs
docker-compose logs nginx
```

### **Issue: "429 Rate Limited"**
- With auth token: Add token to request header
- Without auth: Wait 60 seconds before retrying
- Check NGINX logs: `docker-compose logs nginx | grep rate_limit`

### **Issue: High Response Times**
```bash
# Check /metrics for p99 latency
curl http://localhost/metrics | grep http_request_duration_seconds_bucket

# Check NGINX upstream response times
docker-compose exec nginx tail -f /var/log/nginx/access.log

# Check database query performance
# Enable slow query log in PostgreSQL
```

### **Issue: Cache Hitting Issues**
```bash
# Check X-Cache-Status header
curl -i http://localhost/search/... | grep X-Cache-Status

# Clear cache (stop and restart NGINX)
docker-compose restart nginx
```

## Next Steps

1. **Configure HTTPS/TLS**:
   - Add SSL certificates to NGINX
   - Update docker-compose.yml to map certificates
   - Change `listen 80` to `listen 443 ssl`

2. **Set Up Monitoring**:
   - Configure Prometheus to scrape `/metrics`
   - Create Grafana dashboards
   - Set up alerting rules

3. **Enable Distributed Tracing**:
   - OpenTelemetry is already configured in requirements.txt
   - Configure OTLP exporter endpoint in main.py
   - Use `request_id` for tracing across services

4. **Database Optimization**:
   - Add connection pooling (PgBouncer)
   - Enable query caching
   - Monitor slow queries

5. **Production Hardening**:
   - Enable WAF (Web Application Firewall)
   - Add DDoS protection
   - Implement bot detection
   - Set up comprehensive logging and alerting

## Architecture Benefits

✅ **High Availability**: 3 instances, NGINX load balancing  
✅ **Resilience**: Circuit breaker, health checks, health failover  
✅ **Scalability**: Horizontal scaling (add more API instances)  
✅ **Observability**: Structured logs, metrics, request tracking  
✅ **Security**: Rate limiting, DDoS protection, isolation  
✅ **Performance**: Caching, compression, connection reuse  
✅ **Debugging**: Request IDs, process timing, detailed metrics  

## Summary of Changes

| Component | Before | After |
|-----------|--------|-------|
| Entry Point | Single FastAPI (8000) | NGINX LB (80) |
| Instances | 1 | 3 |
| Load Balancing | None | NGINX (least-conn) |
| Rate Limiting | Basic | Advanced (NGINX + FastAPI) |
| Circuit Breaking | None | Implemented |
| Caching | None | NGINX 5-min TTL |
| Monitoring | Basic logging | Prometheus + structured logs |
| Health Checks | Manual | Automatic (10s interval) |
| Failure Handling | Single point | Distributed |
| Request Tracking | None | UUID + timing |

---

**Ready for production! 🚀**
