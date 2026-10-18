# Pulseboard API

Pulseboard is a high-throughput, real-time product analytics ingestion backend built with **FastAPI**, **PostgreSQL** (via **SQLAlchemy 2.0 Async** + **asyncpg**), and **Redis Streams**.

It provides REST endpoints for single and batch event ingestion with API key authentication, buffers high-volume telemetry into Redis Streams, asynchronously persists events to PostgreSQL with minute and hourly aggregation rollups, and streams live top-event analytics to dashboards via WebSockets.

---

## Features

- **High-Throughput Ingestion**: Ingest individual (`POST /v1/events`) or batched (`POST /v1/events/batch`) events authenticated by project API keys.
- **Resilient Buffering**: Buffers incoming events to Redis Streams for background worker ingestion; gracefully falls back to direct database insertion if Redis is unavailable.
- **Rate Limiting**: Sliding-window rate limiting per API key (configured via `RATE_LIMIT_PER_MINUTE`).
- **Background Stream Worker**: Asynchronous asyncio worker (`app.worker`) consuming from Redis consumer groups, bulk-inserting raw events, and aggregating minute rollups.
- **Automated Compaction & Pruning**: Rolls up minute metrics into hourly summaries and periodically purges expired raw events according to data retention policies.
- **Live WebSocket Streams**: Real-time broadcast endpoint (`/v1/stream`) streaming top events over sliding windows.
- **REST Analytics & Query API**: Filter and paginate raw events and query aggregated rollups.

---

## Architecture

```
                  +-----------------------+
                  |  Analytics Producers  |
                  | (Web / Mobile / Apps) |
                  +-----------+-----------+
                              |
                              | POST /v1/events (API Key Auth)
                              v
                +---------------------------+
                |    Pulseboard FastAPI     |
                |   (Validation & Limits)   |
                +-------------+-------------+
                              |
               +--------------+--------------+
               | (Redis OK)                  | (Redis Unavailable Fallback)
               v                             v
     +-------------------+         +--------------------+
     |   Redis Streams   |         | PostgreSQL DB      |
     | pulseboard:events |         | (Direct insert)    |
     +---------+---------+         +--------------------+
               |                             ^
               | xreadgroup (Consumer Group) |
               v                             |
     +-------------------+                   |
     | Background Worker |-------------------+
     |   (app.worker)    |  Bulk Insert Events &
     +---------+---------+  Minute Rollups / Compaction
               |
               v
     +-------------------+
     | PostgreSQL DB     |
     | - events          |
     | - minute_rollups  |
     | - hourly_rollups  |
     +---------+---------+
               ^
               | Polls top events
               |
     +---------+---------+
     | WebSocket Client  |
     | (GET /v1/stream)  |
     +-------------------+
```

---

## Tech Stack

- **Framework**: FastAPI, Uvicorn, Starlette
- **Data Validation & Settings**: Pydantic v2, Pydantic Settings
- **Database ORM & Driver**: SQLAlchemy 2.0 (Async), asyncpg (PostgreSQL), aiosqlite (SQLite for tests)
- **Message Broker & Cache**: Redis Streams (`redis-py` async), fakeredis (for testing)
- **Security & Auth**: PyJWT, bcrypt, passlib
- **Testing & Load Testing**: pytest, pytest-asyncio, httpx, Locust

---

## Prerequisites

- Python 3.12+
- Docker & Docker Compose (optional for local deployment)
- PostgreSQL 16+ and Redis 7+ (if running outside Docker)

---

## Quickstart with Docker Compose

Spin up PostgreSQL, Redis, the API server, and the background worker with a single command:

```bash
docker compose up --build
```

Services started:
- **`api`**: FastAPI app listening on `http://localhost:8000` (interactive API documentation available at `http://localhost:8000/docs`).
- **`worker`**: Background worker consuming from Redis Streams and maintaining rollups.
- **`postgres`**: PostgreSQL 16 on port `5432`.
- **`redis`**: Redis 7 on port `6379`.

---

## Configuration & Environment Variables

Settings are configured using environment variables or a `.env` file via `app/config.py`:

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROJECT_NAME` | `str` | `"Pulseboard API"` | Application display name |
| `VERSION` | `str` | `"1.0.0"` | Application version |
| `API_V1_PREFIX` | `str` | `"/v1"` | URL prefix for API v1 endpoints |
| `ENVIRONMENT` | `str` | `"production"` | Runtime environment (`production`, `development`, `testing`) |
| `DATABASE_URL` | `str` | `"postgresql+asyncpg://postgres:postgres@localhost:5432/pulseboard"` | SQLAlchemy async database connection URI |
| `DB_ECHO` | `bool` | `False` | Enable SQLAlchemy SQL query echo logging |
| `DB_POOL_SIZE` | `int` | `20` | Database connection pool size |
| `DB_MAX_OVERFLOW` | `int` | `10` | Database connection pool max overflow |
| `REDIS_URL` | `Optional[str]` | `"redis://localhost:6379/0"` | Redis connection URI (set empty to force direct DB fallback) |
| `REDIS_STREAM_KEY` | `str` | `"pulseboard:events"` | Stream key used for buffering events |
| `REDIS_CONSUMER_GROUP` | `str` | `"pulseboard_workers"` | Consumer group name for stream processing |
| `REDIS_CONSUMER_NAME` | `str` | `"worker-1"` | Unique identifier for consumer instance |
| `WORKER_BATCH_SIZE` | `int` | `500` | Maximum messages to fetch per consumer loop read |
| `WORKER_POLL_INTERVAL_MS` | `int` | `1000` | Milliseconds to block waiting for new stream messages |
| `WORKER_COMPACTION_INTERVAL_SECONDS` | `int` | `60` | Frequency in seconds to compact minute rollups into hourly rollups |
| `WORKER_RETENTION_INTERVAL_SECONDS` | `int` | `3600` | Frequency in seconds to prune expired raw events |
| `JWT_SECRET_KEY` | `str` | `"pulseboard-super-secret-key-change-in-production-1234567890"` | Secret key used to sign and verify JWT tokens |
| `JWT_ALGORITHM` | `str` | `"HS256"` | JWT signing algorithm |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `int` | `1440` | JWT token expiration time in minutes (24 hours) |
| `RATE_LIMIT_PER_MINUTE` | `int` | `1000` | Ingestion requests allowed per minute per API key |
| `RETENTION_DAYS` | `int` | `30` | Number of days to retain raw events before pruning |

---

## Local Development (Without Docker)

### 1. Setup Virtual Environment

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the API Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Run the Background Worker

In a separate terminal:

```bash
python -m app.worker
```

Database tables are verified and initialized automatically on startup by `init_db()` during application lifespan and worker startup.

---

## Running Tests

The test suite runs entirely offline without requiring external PostgreSQL or Redis services. Tests run against an isolated in-memory SQLite database (`sqlite+aiosqlite:///:memory:`) and in-memory mock Redis (`fakeredis`):

```bash
pytest -v
```

To run a specific test suite:

```bash
pytest tests/test_websocket.py -v
pytest tests/test_events.py -v
```

---

## API Endpoints Summary

All API routes (except root health check) are mounted under `/v1`:

### System Health
- `GET /health`: Health check reporting database and Redis connectivity status.

### Authentication (`/v1/auth`)
- `POST /v1/auth/register`: Create a new user account (`email`, `password`).
- `POST /v1/auth/login`: Authenticate and obtain JWT bearer token.
- `GET /v1/auth/me`: Retrieve current user profile (requires Bearer token).

### Projects & API Keys (`/v1/projects`)
- `POST /v1/projects`: Create a new analytics project workspace.
- `GET /v1/projects`: List user's projects with pagination (`limit`, `offset`).
- `GET /v1/projects/{project_id}`: Get project details.
- `POST /v1/projects/{project_id}/api-keys`: Generate a new API key (prefix `pb_live_...`).
- `GET /v1/projects/{project_id}/api-keys`: List API keys for a project.
- `DELETE /v1/projects/{project_id}/api-keys/{key_id}`: Revoke an API key.

### Event Ingestion & Queries (`/v1`)
- `POST /v1/events`: Ingest a single event. Authenticated by `X-API-Key` header.
- `POST /v1/events/batch`: Ingest a batch of events (supports list or `{"events": [...]}`).
- `GET /v1/projects/{project_id}/events`: Query raw events with filtering (`event_name`, `start_time`, `end_time`, `limit`, `offset`).

### Aggregation & Rollups (`/v1/projects`)
- `GET /v1/projects/{project_id}/rollups`: Retrieve aggregated minute or hourly rollups (`granularity=minute|hourly`).
- `GET /v1/projects/{project_id}/stats`: Get top event counts in sliding window (default `window_minutes=5`).

### Real-Time WebSocket (`/v1`)
- `WebSocket /v1/stream`: Stream live top events over the last 5 minutes. Query params: `token` (JWT token), optional `project_id`, `interval_seconds` (default `5.0`).

---

## Load Testing

A Locust load test is provided in `scripts/load_test.py` simulating ~1,000 events/sec against the batch ingestion endpoint:

```bash
export API_KEY="pb_live_your_actual_key_here"
locust -f scripts/load_test.py --host http://localhost:8000 --headless -u 25 -r 5 -t 1m
```

---

## Project Structure

```
pulseboard-api/
├── app/
│   ├── api/
│   │   ├── deps.py            # FastAPI dependencies (auth, db, redis, rate limiting)
│   │   └── v1/
│   │       ├── auth.py        # Authentication endpoints
│   │       ├── events.py      # Event ingestion and raw query endpoints
│   │       ├── projects.py    # Project and API key endpoints
│   │       ├── rollups.py     # Aggregated rollups and stats endpoints
│   │       └── stream.py      # Real-time WebSocket streaming
│   ├── config.py              # Settings via pydantic-settings
│   ├── database.py            # SQLAlchemy async engine, session factory, init_db
│   ├── main.py                # FastAPI application setup and lifespan
│   ├── models/                # SQLAlchemy ORM models
│   │   ├── api_key.py         # ApiKey entity
│   │   ├── event.py           # Raw Event entity
│   │   ├── project.py         # Project entity
│   │   ├── rollups.py         # MinuteRollup and HourlyRollup entities
│   │   └── user.py            # User entity
│   ├── schemas/               # Pydantic validation schemas
│   ├── services/              # Business logic services
│   │   ├── aggregation.py     # Rollups, compaction, event pruning
│   │   ├── api_key.py         # API key generation and hashing
│   │   ├── auth.py            # Password hashing and JWT generation
│   │   ├── ingestion.py       # Ingestion orchestration (stream vs fallback)
│   │   ├── rate_limiter.py    # Sliding-window rate limiter
│   │   └── redis_stream.py    # Redis Streams publish and deserialize
│   └── worker.py              # Background consumer worker process
├── scripts/
│   └── load_test.py           # Locust benchmark script
├── tests/                     # Pytest suite (27 tests)
│   ├── conftest.py            # Fixtures (SQLite in-memory, fakeredis)
│   ├── test_aggregation.py
│   ├── test_api_keys.py
│   ├── test_auth.py
│   ├── test_events.py
│   ├── test_projects.py
│   ├── test_rate_limiter.py
│   ├── test_websocket.py
│   └── test_worker.py
├── .github/
│   └── workflows/
│       └── ci.yml             # GitHub Actions CI workflow
├── docker-compose.yml         # Compose configuration (postgres, redis, api, worker)
├── Dockerfile                 # Production container image
├── requirements.txt           # Python dependencies
└── README.md                  # Project documentation
```
