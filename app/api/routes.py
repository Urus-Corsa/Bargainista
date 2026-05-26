import json
import os

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.mcp.client import call_tool
from app.models.db_models import AnalysisRun, RunStatus
from app.models.schemas import ListingInput
from app.utils.document_processing import process_document
from app.workers.tasks import run_analysis_task

_ACCEPTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/api/process-document")
async def process_document_upload(file: UploadFile) -> dict:
    """Extract text from an uploaded PDF or image file.

    Returns the extracted text immediately so the frontend can display per-file
    status before the user submits the main analysis form. Binary is never
    forwarded to the analysis pipeline — only the extracted text string is kept.
    """
    filename = file.filename or ""
    ext = os.path.splitext(filename.lower())[1]
    if ext not in _ACCEPTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext or 'none'}'. Accepted: .pdf, .jpg, .jpeg, .png, .webp",
        )

    content = await file.read()
    result = await process_document(filename, content)

    return {
        "success": result["success"],
        "extracted_text": result["text"] if result["success"] else None,
        "filename": result["filename"],
        "error": result["error"],
    }


@router.get("/api/vin/{vin}")
async def decode_vin(vin: str) -> dict:
    """Decode a VIN via the MCP get_vehicle_specs tool.

    Returns year, make, model, trim from NHTSA vPIC. Returns 404 if
    the VIN is unrecognised or the MCP server is unavailable.
    """
    specs = await call_tool("get_vehicle_specs", {"vin": vin})
    if not specs:
        raise HTTPException(status_code=404, detail="VIN not found")
    return {
        "year": specs.get("year"),
        "make": specs.get("make"),
        "model": specs.get("model"),
        "trim": specs.get("trim"),
    }


@router.post("/api/analyze", status_code=202)
async def analyze(
    listing: ListingInput,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Submit a vehicle listing for analysis.

    Returns a run_id immediately. The client should open a WebSocket connection
    to /ws/analyze/{run_id} to receive real-time progress events.
    """
    run = AnalysisRun(
        status=RunStatus.pending,
        listing_input=listing.model_dump(mode="json"),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    run_analysis_task.delay(str(run.id))

    return {"run_id": str(run.id)}


@router.delete("/api/analyze/{run_id}", status_code=200)
async def cancel_analysis(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Signal the client to stop following this run.

    The Celery task continues to completion and persists all results — data
    is kept for future use. Only the WebSocket connection is closed via
    a pub/sub event. No DB state is changed.
    """
    run = await db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.publish(
            f"run:{run_id}",
            json.dumps({"event": "cancelled", "run_id": run_id, "payload": {}}),
        )
    finally:
        await redis.aclose()  # type: ignore[attr-defined]

    return {"status": "cancelled"}
