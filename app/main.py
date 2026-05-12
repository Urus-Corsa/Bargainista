import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.routes import router
from app.core.logging import configure_logging
from app.db.init_db import init_db

# Configure logging immediately so all startup messages are formatted correctly
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting up — initialising database")
    await init_db()
    logger.info("Database ready")
    yield
    logger.info("Shutting down")


app = FastAPI(title="Vehicle Analysis Platform", lifespan=lifespan)

app.include_router(router)
app.include_router(admin_router)
