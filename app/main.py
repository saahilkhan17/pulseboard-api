"""FastAPI application entry point."""

from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.deps import get_redis_client
from app.api.v1 import api_router
from app.config import get_settings
from app.database import async_session_factory, engine, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pulseboard")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and graceful shutdown."""
    settings = get_settings()
    logger.info("Starting %s v%s in %s mode", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)

    # Initialize tables if needed (e.g. for development / testing)
    try:
        await init_db(engine)
        logger.info("Database schema verified")
    except Exception as e:
        logger.warning("Could not auto-verify database schema on startup: %s", e)

    # Pre-warm Redis connection
    try:
        redis_client = await get_redis_client()
        if redis_client:
            logger.info("Connected to Redis successfully")
        else:
            logger.warning("Redis is unavailable; API will operate in direct DB fallback mode")
    except Exception as e:
        logger.warning("Redis pre-connection error: %s", e)

    yield

    logger.info("Shutting down %s", settings.PROJECT_NAME)
    # Cleanup Redis
    redis_client = await get_redis_client()
    if redis_client:
        await redis_client.aclose()

    # Dispose DB engine pool
    await engine.dispose()
    logger.info("Shutdown complete")


def create_application() -> FastAPI:
    """FastAPI application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="High-throughput product analytics ingestion and real-time rollups backend.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Root health check
    @app.get("/health", tags=["Health"], status_code=status.HTTP_200_OK)
    async def health_check():
        db_ok = False
        redis_ok = False
        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
                db_ok = True
        except Exception:
            db_ok = False

        try:
            redis_client = await get_redis_client()
            if redis_client and await redis_client.ping():
                redis_ok = True
        except Exception:
            redis_ok = False

        return {
            "status": "healthy" if db_ok else "degraded",
            "database": "connected" if db_ok else "disconnected",
            "redis": "connected" if redis_ok else "disconnected_or_fallback",
        }

    # Mount API routers
    app.include_router(api_router)

    return app


app = create_application()
