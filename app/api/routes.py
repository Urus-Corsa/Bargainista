import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.db_models import AnalysisRun, RunStatus
from app.models.schemas import ListingInput
from app.workers.tasks import run_analysis_task

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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
