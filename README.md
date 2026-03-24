# 🕉️ Ritual Buddy — FastAPI Backend

A production-ready, microservices-based FastAPI backend for the Pandit Booking Platform.

## Architecture

```
pandit-backend/
├── services/
│   ├── auth/          # OAuth2 (Google), JWT, refresh tokens
│   ├── user/          # User profiles, addresses, saved pandits
│   ├── pandit/        # Pandit profiles, verification, geolocation
│   ├── booking/       # Booking lifecycle (Saga pattern)
│   ├── search/        # Elasticsearch-powered discovery + geo
│   ├── calendar/      # Availability slots, slot locking
│   ├── payment/       # Razorpay, escrow, payouts
│   ├── notification/  # FCM, SMS, email, WebSocket
│   ├── review/        # Ratings, reviews, moderation
│   └── admin/         # Verification queue, analytics, moderation
├── shared/
│   ├── models/        # SQLAlchemy ORM models
│   ├── schemas/       # Pydantic request/response schemas
│   ├── utils/         # JWT, security, pagination helpers
│   └── middleware/    # Auth, logging, tracing middleware
├── config/            # Settings, database, Redis, Kafka config
├── migrations/        # Alembic database migrations
└── tests/             # Unit & integration tests
```

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ with PostGIS extension
- Redis 7+
- Docker (optional)

### 1. Clone & Setup
```bash
git clone <repo>
cd pandit-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Variables
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Database Setup
```bash
# Create PostgreSQL DB with PostGIS
createdb pandit_db
psql pandit_db -c "CREATE EXTENSION IF NOT EXISTS postgis;"
psql pandit_db -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"

# Run migrations
alembic upgrade head
```

### Migration Workflow (Production)
```bash
# Create a migration from model changes
alembic revision --autogenerate -m "describe_change"

# Review generated file in migrations/versions/

# Apply pending migrations
alembic upgrade head

# Roll back one revision (if needed)
alembic downgrade -1
```

For Docker deployments, migrations run as a one-shot `migrate` service before API containers start.

If your database already has tables created before Alembic was introduced, run this once before enabling automated migration runs:
```bash
alembic stamp head
```

### 4. Run Services

#### Option A: All-in-one (development)
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

#### Option B: Docker Compose
```bash
docker-compose up --build
```

### 5. API Documentation
- Swagger UI: http://localhost:8000/docs
- ReDoc:       http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## API Endpoints Summary

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /auth/google | Initiate Google OAuth |
| GET | /auth/google/callback | OAuth callback |
| POST | /auth/refresh | Refresh access token |
| POST | /auth/logout | Logout + revoke tokens |
| GET | /auth/me | Get current user |

### Users
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /users/me | Get own profile |
| PUT | /users/me | Update profile |
| GET | /users/me/bookings | My booking history |
| POST | /users/me/saved-pandits/{id} | Save a pandit |

### Pandits
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /pandits/{id} | Get pandit profile |
| PUT | /pandits/me | Update own profile |
| PUT | /pandits/me/availability | Set availability |
| GET | /pandits/me/calendar | View own calendar |
| GET | /pandits/me/earnings | Earnings summary |

### Search
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /search/pandits | Search with geo + filters |
| GET | /search/poojas | Search pooja types |

### Bookings
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /bookings | Create booking |
| GET | /bookings/{id} | Get booking details |
| POST | /bookings/{id}/accept | Pandit accepts |
| POST | /bookings/{id}/decline | Pandit declines |
| POST | /bookings/{id}/complete | Mark completed |
| POST | /bookings/{id}/cancel | Cancel booking |

### Payments
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /payments/initiate | Start payment |
| POST | /payments/webhook | Razorpay webhook |
| GET | /payments/me/history | Payment history |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /admin/pandits/pending | Verification queue |
| POST | /admin/pandits/{id}/verify | Approve pandit |
| POST | /admin/pandits/{id}/suspend | Suspend pandit |
| GET | /admin/analytics | Platform analytics |

## Tech Stack
- **Framework**: FastAPI 0.110+
- **ORM**: SQLAlchemy 2.0 (async)
- **DB**: PostgreSQL + PostGIS
- **Cache**: Redis
- **Auth**: OAuth2 (Google) + JWT
- **Search**: Elasticsearch
- **Payments**: Razorpay
- **Notifications**: FCM + Twilio + Resend
- **Queue**: Celery + Redis
- **Migrations**: Alembic
- **Testing**: pytest + httpx

## Running Tests
```bash
pytest tests/ -v --asyncio-mode=auto
```
