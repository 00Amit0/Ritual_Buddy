# Production Gateway Testing Guide

This guide helps you verify that your production gateway is working correctly with resilience features.

## Quick Start Tests

### 1. Health Check
```bash
# Should return 200 with status: ok
curl http://localhost/health -v

# Expected response:
# {
#   "status": "ok",
#   "version": "1.0.0",
#   "database": "ok",
#   "redis": "ok"
# }
```

### 2. API Access
```bash
# Get API documentation
curl http://localhost/docs

# Make a test API call
curl http://localhost/search/poojas
```

### 3. Metrics
```bash
# View Prometheus metrics
curl http://localhost/metrics | head -20
```

## Resilience Testing

### Test 1: Health Check on One Instance Down

```bash
# 1. Get the current container IDs
docker-compose ps

# 2. Stop one API instance
docker-compose stop api1

# 3. Make requests (should still work)
for i in {1..10}; do
  curl http://localhost/health
  sleep 1
done

# 4. Check NGINX logs to see traffic redirected
docker-compose logs -f nginx | grep api1

# 5. Restart the instance
docker-compose start api1

# 6. Verify it's back in rotation
docker-compose ps
```

**Expected**: All requests succeed, traffic diverted to api2 and api3, NGINX detects recovery.

### Test 2: Rate Limiting

```bash
# For unauthenticated requests (limit: 100 req/min)
# Rapidly make requests to exceed rate limit
for i in {1..150}; do
  curl http://localhost/health -w "\nStatus: %{http_code}\n"
done

# Should see 429 (Too Many Requests) responses
# Check headers:
curl -i http://localhost/health | grep -E "HTTP|Retry-After"
```

**Expected**: First 100 requests succeed, requests 101+ get 429 status with "Retry-After: 60" header.

### Test 3: Request Tracing

```bash
# Make a request and check for tracing headers
curl -i http://localhost/health | grep -E "X-Request-ID|X-Process-Time|X-Cache-Status"

# Expected headers:
# X-Request-ID:550e8400-e29b-41d4-a716-446655440000
# X-Process-Time: 45.23ms
# X-Cache-Status: HIT/MISS/EXPIRED
```

### Test 4: Response Time Tracking

```bash
# Check metrics for request duration
curl http://localhost/metrics | grep -E "http_request_duration_seconds|http_request_body_size_bytes"

# Sample metrics:
# http_request_duration_seconds_bucket{endpoint="/health",method="GET",status_code="200",le="0.005"} 8.0
# http_request_duration_seconds_bucket{endpoint="/health",method="GET",status_code="200",le="0.01"} 12.0
# http_request_duration_seconds_bucket{endpoint="/health",method="GET",status_code="200",le="0.05"} 95.0
```

### Test 5: NGINX Load Balancing

```bash
# Add a debug header to track which instance handles request
curl -H "X-Trace-ID: test-123" http://localhost/health -v 2>&1 | grep -i "Server\|request_id"

# Check NGINX access logs
docker-compose exec nginx tail -20 /var/log/nginx/access.log

# Look for different upstream_addr values:
# Should see all three instances (api1:8000, api2:8000, api3:8000)
```

**Expected**: Requests distributed across all 3 instances.

### Test 6: Circuit Breaker (Simulated)

```bash
# 1. Check current service health
curl http://localhost/health

# 2. Shut down database to simulate downstream failure
docker-compose stop postgres

# 3. Make requests - should get degraded response
curl http://localhost/health -v

# 4. Look for circuit breaker activation in logs
docker-compose logs api1 | grep -i "circuit\|degraded"

# 5. Restart database
docker-compose start postgres

# 6. Verify recovery
curl http://localhost/health
```

**Expected**: Initial requests succeed, after ~5 failures circuit breaker opens and returns 503, after recovery period attempts resume.

## Performance Testing

### Load Test with Apache Bench

```bash
# Install ab if not present
# On macOS: brew install httpd
# On Linux: sudo apt-get install apache2-utils
# On Windows: Download from Apache website

# Test 1000 requests with 100 concurrent
ab -n 1000 -c 100 http://localhost/health

# Expected output includes:
# Requests per second: [value]
# Time per request: [value]
# Failed requests: 0
```

### Load Test with wrk

```bash
# Install wrk
# https://github.com/wg/wrk

# Run for 30 seconds with 100 connections
wrk -t 4 -c 100 -d 30s http://localhost/health

# Expected: High throughput, low latency (< 50ms p99)
```

## Monitoring & Logs

### View Structured Logs

```bash
# See logs from all instances with timestamps
docker-compose logs -f --tail=100

# Filter for specific instance
docker-compose logs -f api1 | grep "ERROR\|WARN"

# Filter for rate limit events
docker-compose logs api1 api2 api3 | grep "rate_limit\|429"
```

### Check Metrics in Prometheus Format

```bash
# Get all metrics
curl http://localhost/metrics

# Filter specific metrics
curl http://localhost/metrics | grep "^http_request"

# Count requests to specific endpoint
curl http://localhost/metrics | grep 'endpoint="/health"' | head -5
```

### NGINX Access Logs

```bash
# Follow NGINX logs in real-time
docker-compose logs -f nginx

# Parse specific fields from access logs
docker-compose exec nginx sh -c 'tail -f /var/log/nginx/access.log | grep -E "502|503|429"'

# Count requests by status code
docker-compose exec nginx sh -c 'tail -1000 /var/log/nginx/access.log | awk "{print \$9}" | sort | uniq -c'
```

## Security Testing

### Rate Limiting by IP

```bash
# From one IP address
curl http://localhost/health

# Simulate different IPs (if reverse proxy configured)
curl -H "X-Forwarded-For: 192.168.1.100" http://localhost/health

# Should get separate rate limit for each IP
```

### Protected Endpoints

```bash
# Without auth token - should be rate limited at 100 req/min
for i in {1..5}; do
  curl http://localhost/user/profile
  sleep 1
done

# With auth token - should be rate limited at 600 req/min (on NGINX side)
curl -H "Authorization: Bearer your_token" http://localhost/user/profile
```

## Troubleshooting Tests

### Test NGINX Connectivity

```bash
# Check if NGINX can reach API instances
docker-compose exec nginx curl -f http://api1:8000/health
docker-compose exec nginx curl -f http://api2:8000/health
docker-compose exec nginx curl -f http://api3:8000/health
```

### Check Service Health

```bash
# List all running containers
docker-compose ps

# Check specific service logs
docker-compose logs api1 --tail=50

# Restart a service
docker-compose restart api1
```

### Verify Network

```bash
# Check if services can resolve each other
docker-compose exec api1 ping nginx
docker-compose exec nginx ping redis

# Check if Redis is accessible
docker-compose exec api1 redis-cli -h redis ping
```

## Automated Health Monitoring

Create a simple monitoring script:

```bash
#!/bin/bash
# monitor.sh - Check gateway health every 30 seconds

while true; do
    echo "=== $(date) ==="
    echo "NGINX: $(docker-compose exec -T nginx curl -s http://localhost/health | jq .status)"
    echo "API1: $(docker-compose exec -T api1 curl -s http://localhost:8000/health | jq .status)"
    echo "API2: $(docker-compose exec -T api2 curl -s http://localhost:8000/health | jq .status)"
    echo "API3: $(docker-compose exec -T api3 curl -s http://localhost:8000/health | jq .status)"
    sleep 30
done
```

Run it:
```bash
chmod +x monitor.sh
./monitor.sh
```

## Success Criteria

✅ **All tests pass if:**
- Health checks return 200 OK consistently
- One instance failure doesn't affect other requests
- Rate limiting triggers at expected thresholds
- Response times are under 100ms p99
- Requests are distributed across all 3 instances
- No requests are lost during instance failures
- Metrics are accurate and reflect request patterns
- Logs are formatted as JSON with proper structure

## Next Steps

1. **Set up monitoring dashboard** with Prometheus + Grafana
2. **Configure alerting** for critical metrics
3. **Implement distributed tracing** with Jaeger
4. **Set up log aggregation** with ELK stack
5. **Add load testing** to CI/CD pipeline

---

For more details, see `GATEWAY_PRODUCTION_GUIDE.md`
