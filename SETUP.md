# 🚀 Setup & Implementation Checklist

## ✅ Completed Implementation

### Configuration Files
- **✅ .env** - Environment variables configured with development defaults
- **✅ pytest.ini** - Test configuration with coverage reporting
- **✅ .dockerignore** - Docker build optimization
- **✅ .gitignore** - Enhanced Git ignore patterns
- **✅ scripts/init_db.sql** - Database initialization script with PostGIS setup

### Project Structure
- **✅ main.py** - FastAPI application with full middleware, exception handling, and route registration
- **✅ config/settings.py** - Pydantic Settings with all required environment variables
- **✅ config/database.py** - SQLAlchemy async engine and session factory
- **✅ config/redis_client.py** - Redis client with caching helpers
- **✅ shared/models/models.py** - Complete ORM models (User, Pandit, Booking, Payment, etc.)
- **✅ shared/schemas/schemas.py** - Pydantic response schemas
- **✅ requirements.txt** - All dependencies listed
- **✅ docker-compose.yml** - Complete multicontainer setup with PostgreSQL, Redis, Elasticsearch
- **✅ Dockerfile** - Production-ready containerization
- **✅ All 9 service routers** - Auth, User, Pandit, Booking, Search, Payment, Notification, Review, Admin

---

## 📋 Pre-Deployment Checklist

### 1. **Local Development Setup**
```bash
# Clone repository
git clone <repo>
cd pandit-booking

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Verify .env file exists with your credentials
cat .env
```

### 2. **Database Setup**
```bash
# Start PostgreSQL + Redis + Elasticsearch
docker-compose up -d postgres redis elasticsearch

# Apply Alembic migrations
alembic upgrade head

# Verify database connection
python -c "from config.database import AsyncSessionLocal; print('✅ Database connected')"
```

### 3. **Required API Credentials** (Update in `.env`)

| Service | Variable | Status |
|---------|----------|--------|
| Google OAuth | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | ⚠️ **ACTION NEEDED** |
| JWT | `JWT_SECRET_KEY`, `SECRET_KEY` | ✅ Default provided (change in production) |
| Razorpay | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | ⚠️ **ACTION NEEDED** |
| Firebase | `FIREBASE_CREDENTIALS_PATH` | ⚠️ **ACTION NEEDED** |
| Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | ⚠️ **ACTION NEEDED** |
| Resend Email | `RESEND_API_KEY` | ⚠️ **ACTION NEEDED** |
| AWS S3/R2 | `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | ⚠️ **ACTION NEEDED** |

### 4. **Firebase Setup**
```bash
# Download Firebase credentials from Google Cloud Console
# Place at: config/firebase-credentials.json

# Verify it's properly formatted
python -c "import json; json.load(open('config/firebase-credentials.json'))"
```

### 5. **Run Tests**
```bash
# Run all tests with coverage
pytest

# Run specific test module
pytest tests/test_auth.py -v

# Run with coverage report
pytest --cov=services --cov=shared --cov-report=html
```

### 6. **Start Development Server**
```bash
# Option 1: Direct with uvicorn
uvicorn main:app --reload --port 8000

# Option 2: Using Docker Compose
docker-compose up -d
docker-compose logs -f api

# Option 3: Python entry point
python main.py
```

### 7. **Verify API is Running**
```bash
# Health check
curl http://localhost:8000/health

# API documentation
open http://localhost:8000/docs

# Expected response:
# {
#   "status": "ok",
#   "database": "ok",
#   "redis": "ok",
#   "version": "1.0.0"
# }
```

---

## 🔧 Additional Configuration Needed

### Elasticsearch Indexing
The search service requires Elasticsearch indices to be created:
```python
# Run once to create indices
from services.search.utils import create_es_indices
asyncio.run(create_es_indices())
```

### Celery Worker (for background tasks)
```bash
# Terminal 1: Start Celery worker
celery -A tasks.celery_app worker --loglevel=info

# Terminal 2: Optional - Monitor with Flower
celery -A tasks.celery_app flower
# Then visit http://localhost:5555
```

### Initial Data Seeding
Poojas are auto-seeded on first run in development mode. For production:
```python
# Manual seeding script
python -c "from main import seed_initial_data; asyncio.run(seed_initial_data())"
```

---

## 📝 Environment Variables Summary

### Development Defaults (Already in .env)
- `APP_ENV=development` - Enables debugging, auto-reload, data seeding
- `DEBUG=true` - Detailed error messages, SQL logging
- Database: `postgresql+asyncpg://postgres:password@localhost:5432/pandit_db`
- Redis: `redis://localhost:6379/0`
- Elasticsearch: `http://localhost:9200`

### Production Requirements (Must Change)
```
APP_ENV=production
DEBUG=false
SECRET_KEY=<generate-new-32-char-key>
JWT_SECRET_KEY=<generate-new-key>
DATABASE_URL=<production-postgres-url>
REDIS_URL=<production-redis-url>
ALLOWED_ORIGINS=<production-domain>
FRONTEND_URL=<production-frontend-url>
```

---

## 🐳 Docker Deployment

### Full Stack with Docker Compose
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop all services
docker-compose down

# Clean up volumes
docker-compose down -v
```

### Services Running
- **API**: http://localhost:8000 (FastAPI)
- **PostgreSQL**: localhost:5432 (Database)
- **Redis**: localhost:6379 (Cache/Queue)
- **Elasticsearch**: http://localhost:9200 (Search)
- **Kibana**: http://localhost:5601 (ES UI - optional)
- **Celery Flower**: http://localhost:5555 (Task monitoring - optional)

---

## 🔐 Security Checklist

- [ ] Change `SECRET_KEY` and `JWT_SECRET_KEY` in production
- [ ] Add Firebase credentials file
- [ ] Configure Google OAuth redirect URIs
- [ ] Set `HTTPS_ONLY=true` for production cookies
- [ ] Use environment-specific `.env` files (never commit real credentials)
- [ ] Enable database SSL connections in production
- [ ] Configure WAF and rate limiting (Kong, Cloudflare)
- [ ] Set up proper logging and monitoring
- [ ] Enable CORS only for trusted origins

---

## 🚨 Common Issues & Fixes

### "PostgreSQL connection refused"
```bash
# Ensure PostgreSQL is running
docker-compose up -d postgres
docker-compose exec postgres psql -U postgres -c "SELECT 1"
```

### "Redis connection refused"
```bash
# Ensure Redis is running
docker-compose up -d redis
docker-compose exec redis redis-cli ping
```

### "Elasticsearch not responding"
```bash
# Check Elasticsearch status
curl http://localhost:9200/_cluster/health
# If not running:
docker-compose up -d elasticsearch
```

### "Import errors in tests"
```bash
# Ensure pytest has correct Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest tests/
```

---

## 📚 Documentation References

- **FastAPI**: https://fastapi.tiangolo.com/
- **SQLAlchemy**: https://docs.sqlalchemy.org/
- **PostGIS**: https://postgis.net/documentation/
- **Pydantic**: https://docs.pydantic.dev/
- **Celery**: https://docs.celeryproject.io/

---

## ✨ Ready to Deploy?

Once you've completed all the checklist items above, run:

```bash
# Final verification
pytest --cov=services --cov=shared
# All tests should pass ✅

# Start production build
docker build -t pandit-booking:latest .
docker run -p 8000:8000 --env-file .env pandit-booking:latest
```

**Happy coding! 🎉**
