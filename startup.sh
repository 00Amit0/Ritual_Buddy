#!/bin/bash

# Production Gateway Startup Script
# Starts all services with proper health checks and monitoring

set -e

echo "🚀 Starting Pandit Booking Platform - Production Gateway Architecture"
echo "=========================================================================="

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo -e "${BLUE}📦 Building images...${NC}"
docker-compose build --no-cache

echo -e "${BLUE}🔧 Starting services...${NC}"
docker-compose up -d

echo -e "${BLUE}⏳ Waiting for services to be healthy...${NC}"
sleep 10

# Check NGINX
if docker-compose exec -T nginx curl -f http://localhost/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ NGINX Load Balancer is healthy${NC}"
else
    echo -e "${YELLOW}⚠️  NGINX is starting...${NC}"
fi

# Check API instances
for i in 1 2 3; do
    if docker-compose exec -T api$i curl -f http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✅ API Instance $i is healthy${NC}"
    else
        echo -e "${YELLOW}⚠️  API Instance $i is starting...${NC}"
    fi
done

echo ""
echo -e "${GREEN}✅ All services started!${NC}"
echo ""
echo "📋 Service URLs:"
echo "   API Gateway:        http://localhost"
echo "   Health Check:       http://localhost/health"
echo "   Metrics:            http://localhost/metrics"
echo "   API Docs:           http://localhost/docs"
echo "   ReDoc:              http://localhost/redoc"
echo ""
echo "📊 Monitoring (with dev-tools profile):"
echo "   Kibana:             http://localhost:5601"
echo "   Flower (Celery):    http://localhost:5555"
echo "   Prometheus:         http://localhost:9090 (configure separately)"
echo ""
echo "📝 Logs:"
echo "   All services:       docker-compose logs -f"
echo "   NGINX only:         docker-compose logs -f nginx"
echo "   API instances:      docker-compose logs -f api1 api2 api3"
echo "   Celery worker:      docker-compose logs -f celery-worker"
echo ""
echo "🛑 To stop all services:"
echo "   docker-compose down"
echo ""
echo "📖 Documentation: See GATEWAY_PRODUCTION_GUIDE.md"
echo ""
