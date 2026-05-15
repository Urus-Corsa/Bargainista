"""WebSocket endpoint — streams analysis events to the client in real time.

Connection lifecycle:
  1. Accept the connection
  2. Check run status from DB
     - complete: send stored FinalReport, close
     - failed:   send error event, close
     - running/pending:
         a. Query completed AgentResult rows; send as synthetic agent_complete events
         b. Subscribe to Redis pub/sub channel run:{run_id}
         c. Re-check status (eliminates TOCTOU race with the Celery task)
         d. Forward pub/sub messages until complete or failed event received
  3. Clean up Redis subscription and close

The subscribe-before-recheck pattern (step b→c) eliminates the race where the Celery task
publishes the complete event between the initial status check and the subscribe call.
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.db_models import AgentResult, AnalysisRun, RunStatus

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/analyze/{run_id}")
async def ws_analyze(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()

    # ------------------------------------------------------------------
    # 1. Load run and handle terminal states before subscribing
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        run = await db.scalar(
            select(AnalysisRun).where(AnalysisRun.id == run_id)
        )

    if run is None:
        await websocket.send_json({
            "event": "error",
            "run_id": run_id,
            "payload": {"error": "run not found"},
        })
        await websocket.close(code=1008)
        return

    if run.status == RunStatus.complete and run.full_result:
        await websocket.send_json({
            "event": "complete",
            "run_id": run_id,
            "payload": {"report": run.full_result},
        })
        await websocket.close()
        return

    if run.status == RunStatus.failed:
        await websocket.send_json({
            "event": "failed",
            "run_id": run_id,
            "payload": {"error": "analysis failed"},
        })
        await websocket.close()
        return

    # ------------------------------------------------------------------
    # 2. Reconnect replay — send already-completed agent results
    # ------------------------------------------------------------------
    async with AsyncSessionLocal() as db:
        completed_agents = (
            await db.execute(
                select(AgentResult).where(
                    AgentResult.run_id == run.id,
                    AgentResult.status == RunStatus.complete,
                )
            )
        ).scalars().all()

    for agent_row in completed_agents:
        await websocket.send_json({
            "event": "agent_complete",
            "run_id": run_id,
            "payload": {
                "agent": agent_row.agent_name.value,
                "result": agent_row.result,
            },
        })

    # ------------------------------------------------------------------
    # 3. Subscribe THEN recheck (eliminates TOCTOU race with Celery task)
    # ------------------------------------------------------------------
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"run:{run_id}")

    try:
        async with AsyncSessionLocal() as db:
            run = await db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == run.id)
            )

        if run.status == RunStatus.complete and run.full_result:
            await websocket.send_json({
                "event": "complete",
                "run_id": run_id,
                "payload": {"report": run.full_result},
            })
            return

        if run.status == RunStatus.failed:
            await websocket.send_json({
                "event": "failed",
                "run_id": run_id,
                "payload": {"error": "analysis failed"},
            })
            return

        # ------------------------------------------------------------------
        # 4. Forward pub/sub messages until terminal event
        # ------------------------------------------------------------------
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            await websocket.send_json(data)
            if data.get("event") in ("complete", "failed", "cancelled"):
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: run_id=%s", run_id)

    except Exception as exc:
        logger.error("WebSocket error for run %s: %s", run_id, exc, exc_info=True)

    finally:
        await pubsub.unsubscribe(f"run:{run_id}")
        await pubsub.aclose()
        await redis.aclose()
