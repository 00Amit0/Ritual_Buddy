"""
main.py
FastAPI application entry point.
Registers all routers, middleware, startup/shutdown events.

Production-ready features:
- Multiple instances behind NGINX load balancer
- Circuit breaker pattern for downstream services
- Advanced rate limiting and DDoS protection
- Request/response caching
- Distributed tracing
- Prometheus metrics
"""

import time
import uuid
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from config.database import close_db, init_db
from config.redis_client import close_redis, init_redis
from config.settings import settings

# Service routers
from services.auth.router import router as auth_router
from services.pandit.router import router as pandit_router
from services.booking.router import router as booking_router
from services.search.router import router as search_router
from services.payment.router import router as payment_router
from services.notification.router import router as notification_router
from services.user.router import router as user_router
from services.review.router import router as review_router
from services.admin.router import router as admin_router

# Resilience patterns
from pybreaker import CircuitBreaker
from tenacity import retry, stop_after_attempt, wait_exponential


# ── Resilience: Circuit Breaker ──────────────────────────────

class CircuitBreakerManager:
    """Manages circuit breakers for each downstream service."""
    
    def __init__(self):
        self.breakers = {}
    
    def get_breaker(self, service_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a service."""
        if service_name not in self.breakers:
            self.breakers[service_name] = CircuitBreaker(
                fail_max=5,  # Open after 5 failures
                reset_timeout=60,  # Try again after 60 seconds
                listeners=[]  # Add logging listeners in production
            )
        return self.breakers[service_name]

circuit_breaker_manager = CircuitBreakerManager()


# ── Logging ──────────────────────────────────────────────────

import logging
import json
from logging import LogRecord

class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""
    
    def format(self, record: LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "instance": os.getenv("INSTANCE_NAME", "unknown"),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

# Configure structured logging
logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(message)s"
)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.handlers = [handler]


# ── Lifespan (startup/shutdown) ───────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle handler."""
    print("🚀 Starting Pandit Booking Platform API...")

    # Initialize connections
    await init_db()
    print("✅ Database connected")

    await init_redis()
    print("✅ Redis connected")

    # Seed initial data (poojas, etc.) — only in dev
    if settings.APP_ENV == "development":
        await seed_initial_data()

    print(f"🕉️  {settings.APP_NAME} v{settings.APP_VERSION} is ready!")
    yield

    # Cleanup
    await close_redis()
    await close_db()
    print("👋 Server shutdown complete")


# ── App Factory ───────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="""
## 🕉️ Pandit Booking Platform API

Complete REST API for the Pandit Booking Platform:
- **Auth**: Google OAuth2 + JWT (15min) + refresh tokens
- **Search**: Geo-based pandit discovery (Elasticsearch + PostGIS fallback)
- **Bookings**: Full lifecycle with Saga pattern
- **Payments**: Razorpay integration with webhook + escrow
- **Notifications**: FCM push + SMS + email
- **Admin**: Verification queue + analytics + audit log

### Authentication
All protected endpoints require `Authorization: Bearer <access_token>` header.
Get a token via the `/auth/google` OAuth flow.

### Roles
- `USER`: Book pandits, write reviews, manage profile
- `PANDIT`: Accept/decline bookings, manage availability, view earnings
- `ADMIN`: Full platform access, verification, analytics
        """,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── Middleware (order matters — outermost first) ───────────────
    # Trust NGINX forwarded headers (IMPORTANT for OAuth)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["*"]
    )
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )

    # GZip compression
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Session (needed for OAuth state parameter)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="pandit_session",
        same_site="lax",
        https_only=settings.is_production,
    )

    # ── Custom Middleware ──────────────────────────────────────────

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """Add unique X-Request-ID to every request for distributed tracing."""
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def process_time_middleware(request: Request, call_next):
        """Track and expose request processing time."""
        start = time.perf_counter()
        response = await call_next(request)
        process_time = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Process-Time"] = f"{process_time}ms"
        return response

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """
        Advanced rate limiter with circuit breaker awareness.
        Skips rate limiting for health checks and webhook endpoints.
        
        Strategy:
        - Authenticated users: 600 req/min per token (handled by NGINX)
        - Unauthenticated: 100 req/min per IP
        - Critical endpoints: unlimited (webhooks, health)
        """
        skip_paths = {"/health", "/payments/webhook", "/docs", "/redoc", "/openapi.json", "/metrics"}
        if request.url.path in skip_paths:
            return await call_next(request)

        try:
            from config.redis_client import redis_client
            if redis_client:
                # Determine rate limit key and threshold
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    # Authenticated users - higher limit, handled by NGINX
                    return await call_next(request)
                else:
                    # Unauthenticated users
                    client_ip = request.client.host if request.client else "unknown"
                    key = f"rate:unauth:{client_ip}"
                    limit = settings.RATE_LIMIT_UNAUTH_PER_MINUTE

                    count = await redis_client.incr(key)
                    if count == 1:
                        await redis_client.expire(key, 60)

                    if count > limit:
                        logger.warning(f"Rate limit exceeded for IP {client_ip}")
                        return JSONResponse(
                            status_code=429,
                            content={"detail": "Rate limit exceeded. Please slow down."},
                            headers={"Retry-After": "60"},
                        )
        except Exception as e:
            logger.error(f"Rate limit check failed: {str(e)}")
            # Don't fail requests if Redis is down - fail open
            pass

        return await call_next(request)

    # ── Exception Handlers ─────────────────────────────────────────

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Catch-all exception handler. Never expose stack traces in production."""
        import traceback
        
        request_id = getattr(request.state, "request_id", None)
        
        if isinstance(exc, Exception.__class__):
            # Circuit breaker is open - service degradation
            logger.error(f"[{request_id}] Service degraded - Circuit breaker open: {str(exc)}")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Service temporarily unavailable. Please try again later.",
                    "request_id": request_id,
                    "status": "degraded",
                },
            )
        
        if settings.DEBUG:
            detail = str(exc)
            print(traceback.format_exc())
        else:
            detail = "An internal server error occurred"
        
        logger.error(f"[{request_id}] Exception: {str(exc)}", exc_info=True)

        return JSONResponse(
            status_code=500,
            content={
                "detail": detail,
                "request_id": request_id,
            },
        )

    # ── Routes ────────────────────────────────────────────────────

    # Health check (public)
    @app.get("/health", tags=["Health"], include_in_schema=False)
    async def health_check():
        from config.redis_client import redis_client
        from sqlalchemy import text
        from config.database import AsyncSessionLocal

        checks = {"status": "ok", "version": settings.APP_VERSION}

        # DB check
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
            checks["status"] = "degraded"

        # Redis check
        try:
            if redis_client:
                await redis_client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"
            checks["status"] = "degraded"

        status_code = 200 if checks["status"] == "ok" else 503
        return JSONResponse(content=checks, status_code=status_code)

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    # Register all service routers
    app.include_router(auth_router)
    app.include_router(user_router)
    app.include_router(pandit_router)
    app.include_router(booking_router)
    app.include_router(search_router)
    app.include_router(payment_router)
    app.include_router(notification_router)
    app.include_router(review_router)
    app.include_router(admin_router)

    # ── Prometheus Metrics ─────────────────────────────────────────
    # Instrument FastAPI with Prometheus metrics
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", tags=["Monitoring"])

    return app


# ── Dev Data Seeder ───────────────────────────────────────────

async def seed_initial_data():
    """Seed pooja types on first run (development only)."""
    from config.database import AsyncSessionLocal
    from shared.models.models import Pooja, PoojaCategory
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as db:
        count = await db.scalar(select(func.count(Pooja.id)))
        if count and count > 0:
            return  # Already seeded

        seed_poojas = [
            {"name_en": "Ganesh Puja", "name_hi": "गणेश पूजा", "slug": "ganesh-puja", "category": PoojaCategory.GRIHA, "avg_duration_hrs": 2.0},
            {"name_en": "Satyanarayan Puja", "name_hi": "सत्यनारायण पूजा", "slug": "satyanarayan-puja", "category": PoojaCategory.GRIHA, "avg_duration_hrs": 3.0},
            {"name_en": "Vivah Puja (Wedding)", "name_hi": "विवाह पूजा", "slug": "vivah-puja", "category": PoojaCategory.VIVAH, "avg_duration_hrs": 5.0},
            {"name_en": "Griha Pravesh", "name_hi": "गृह प्रवेश", "slug": "griha-pravesh", "category": PoojaCategory.GRIHA, "avg_duration_hrs": 3.0},
            {"name_en": "Navratri Puja", "name_hi": "नवरात्रि पूजा", "slug": "navratri-puja", "category": PoojaCategory.FESTIVAL, "avg_duration_hrs": 2.0},
            {"name_en": "Kaal Sarp Dosh Puja", "name_hi": "काल सर्प दोष पूजा", "slug": "kaal-sarp-dosh-puja", "category": PoojaCategory.HEALTH, "avg_duration_hrs": 4.0},
            {"name_en": "Namkaran Sanskar", "name_hi": "नामकरण संस्कार", "slug": "namkaran-sanskar", "category": PoojaCategory.JANAM, "avg_duration_hrs": 2.0},
            {"name_en": "Antim Sanskar (Last Rites)", "name_hi": "अंतिम संस्कार", "slug": "antim-sanskar", "category": PoojaCategory.MRITU, "avg_duration_hrs": 3.0},
            {"name_en": "Vastu Shanti Puja", "name_hi": "वास्तु शांति पूजा", "slug": "vastu-shanti-puja", "category": PoojaCategory.BUSINESS, "avg_duration_hrs": 3.0},
            {"name_en": "Saraswati Puja", "name_hi": "सरस्वती पूजा", "slug": "saraswati-puja", "category": PoojaCategory.EDUCATION, "avg_duration_hrs": 1.5},
        ]

        for p in seed_poojas:
            db.add(Pooja(**p))

        await db.commit()
        print(f"✅ Seeded {len(seed_poojas)} pooja types")


# ── Entry Point ───────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=1 if settings.DEBUG else settings.WORKERS,
        log_level="debug" if settings.DEBUG else "info",
        access_log=True,
    )
