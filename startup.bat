@echo off
REM Production Gateway Startup Script for Windows
REM Starts all services with proper health checks and monitoring

echo.
echo ============================================================
echo   Pandit Booking Platform - Production Gateway Architecture
echo ============================================================
echo.

REM Check Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not installed. Please install Docker first.
    exit /b 1
)

docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose is not installed. Please install Docker Compose first.
    exit /b 1
)

echo [INFO] Building images...
docker-compose build --no-cache
if errorlevel 1 (
    echo [ERROR] Failed to build images
    exit /b 1
)

echo.
echo [INFO] Starting services...
docker-compose up -d
if errorlevel 1 (
    echo [ERROR] Failed to start services
    exit /b 1
)

echo.
echo [INFO] Waiting for services to be healthy...
timeout /t 15 /nobreak

echo.
echo ============================================================
echo   Service Status
echo ============================================================
echo.

echo [INFO] Checking NGINX Load Balancer...
docker-compose exec -T nginx curl -f http://localhost/health >nul 2>&1
if errorlevel 0 (
    echo [OK] NGINX Load Balancer is healthy
) else (
    echo [WARN] NGINX is starting, please wait...
)

echo.
echo [INFO] Checking API Instances...
for /L %%i in (1,1,3) do (
    docker-compose exec -T api%%i curl -f http://localhost:8000/health >nul 2>&1
    if errorlevel 0 (
        echo [OK] API Instance %%i is healthy
    ) else (
        echo [WARN] API Instance %%i is starting...
    )
)

echo.
echo ============================================================
echo   All Services Started!
echo ============================================================
echo.
echo Service URLs:
echo   API Gateway:        http://localhost
echo   Health Check:       http://localhost/health
echo   Metrics:            http://localhost/metrics
echo   API Docs:           http://localhost/docs
echo   ReDoc:              http://localhost/redoc
echo.
echo Monitoring (with dev-tools profile):
echo   Kibana:             http://localhost:5601
echo   Flower (Celery):    http://localhost:5555
echo   Prometheus:         http://localhost:9090 (configure separately)
echo.
echo Commands:
echo   View logs:          docker-compose logs -f
echo   Stop services:      docker-compose down
echo   View service status: docker-compose ps
echo.
echo Documentation: See GATEWAY_PRODUCTION_GUIDE.md
echo.
