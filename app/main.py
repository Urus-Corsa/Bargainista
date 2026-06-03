import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import init_jwks_client
from app.api.routes import router
from app.api.webhooks import router as webhooks_router
from app.api.websocket import router as ws_router
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
    init_jwks_client()
    yield
    logger.info("Shutting down")


app = FastAPI(title="Vehicle Analysis Platform", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(router)
app.include_router(admin_router)
app.include_router(ws_router)
app.include_router(webhooks_router)


@app.get("/")
async def root() -> FileResponse:
    return FileResponse("app/static/index.html")
