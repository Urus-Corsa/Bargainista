"""Celery task — runs the full analysis pipeline and streams events to Redis pub/sub.

Execution flow:
  1. Update AnalysisRun status to running; publish run_started
  2. Deserialize ListingInput from DB; run ingestion (VIN decode + image normalisation)
  3. Stream the LangGraph graph via stream_analysis()
  4. For each user-facing agent node: persist AgentResult row + publish agent_complete/agent_failed
  5. For synthesizer_node: persist FinalReport + full_result on AnalysisRun + publish complete
  6. On any unhandled exception: mark run failed + publish failed

DB sessions use NullPool (one connection per task) to avoid asyncpg event-loop binding issues
when the worker calls asyncio.run() per task invocation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.orchestrator import stream_analysis
from app.core.config import settings
from app.models.db_models import (
    AgentName,
    AgentResult,
    AnalysisRun,
    RecommendationEnum,
    RunStatus,
)
from app.models.db_models import (
    FinalReport as FinalReportORM,
)
from app.models.schemas import ListingInput
from app.utils.ingestion import prepare_listing
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Maps graph node names to (agent_name_str, result_field_key)
# finance_independent_node is intentionally absent — internal phase, no user-facing event
_AGENT_NODES: dict[str, tuple[str, str]] = {
    "vision_node": ("vision", "vision_result"),
    "history_node": ("history", "history_result"),
    "finance_dependent_node": ("finance", "finance_result"),
}


def _extract_score(agent_name: str, result) -> int | None:
    if agent_name == "vision":
        return result.condition_score
    if agent_name == "history":
        return result.risk_score
    if agent_name == "finance":
        return result.finance_score  # may be None when independent phase failed
    return None


async def _async_run(run_id: str) -> None:
    # Per-task engine with NullPool — fresh connection, no pool reuse across event loops
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
    channel = f"run:{run_id}"
    task_start = datetime.now(timezone.utc)

    async def publish(event: str, payload: dict) -> None:
        await redis.publish(channel, json.dumps({
            "event": event,
            "run_id": run_id,
            "payload": payload,
        }))

    try:
        async with SessionLocal() as db:
            run = await db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(run_id))
            )
            if run is None:
                logger.error("run_analysis_task: run %s not found in DB", run_id)
                return

            run.status = RunStatus.running
            await db.commit()

        await publish("run_started", {"status": "running"})

        # Deserialize ListingInput from stored JSONB
        async with SessionLocal() as db:
            run = await db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(run_id))
            )
            listing_raw = run.listing_input

        listing = ListingInput(**listing_raw)
        listing, images = await prepare_listing(listing)

        # Stream the graph — one yield per completed node
        async with SessionLocal() as db:
            async for node_name, node_update in stream_analysis(listing, images, db):
                if node_name in _AGENT_NODES:
                    agent_str, result_key = _AGENT_NODES[node_name]
                    result = node_update.get(result_key)
                    errors = node_update.get("errors", {})

                    if result is not None:
                        score = _extract_score(agent_str, result)
                        result_json = result.model_dump(mode="json")

                        async with SessionLocal() as write_db:
                            write_db.add(AgentResult(
                                run_id=uuid.UUID(run_id),
                                agent_name=AgentName[agent_str],
                                status=RunStatus.complete,
                                score=score,
                                result=result_json,
                                started_at=task_start,
                                completed_at=datetime.now(timezone.utc),
                            ))
                            await write_db.commit()

                        await publish("agent_complete", {
                            "agent": agent_str,
                            "result": result_json,
                        })
                        logger.info("agent_complete: %s score=%s", agent_str, score)

                    else:
                        # Node failed — find the error message
                        error_msg = (
                            errors.get(agent_str)
                            or errors.get(f"{agent_str}_independent")
                            or errors.get(f"{agent_str}_dependent")
                            or "unknown error"
                        )
                        async with SessionLocal() as write_db:
                            write_db.add(AgentResult(
                                run_id=uuid.UUID(run_id),
                                agent_name=AgentName[agent_str],
                                status=RunStatus.failed,
                                error=error_msg,
                                started_at=task_start,
                                completed_at=datetime.now(timezone.utc),
                            ))
                            await write_db.commit()

                        await publish("agent_failed", {
                            "agent": agent_str,
                            "error": error_msg,
                        })
                        logger.warning("agent_failed: %s — %s", agent_str, error_msg)

                elif node_name == "synthesizer_node":
                    final_report = node_update.get("final_report")

                    if final_report is not None:
                        report_json = final_report.model_dump(mode="json")

                        async with SessionLocal() as write_db:
                            write_db.add(FinalReportORM(
                                run_id=uuid.UUID(run_id),
                                recommendation=RecommendationEnum(
                                    final_report.recommendation.value
                                ),
                                overall_score=final_report.overall_score,
                                vision_score=final_report.vision_score,
                                history_score=final_report.history_score,
                                finance_score=final_report.finance_score,
                                key_reasons=final_report.key_reasons,
                                summary=final_report.summary,
                            ))
                            run_row = await write_db.scalar(
                                select(AnalysisRun).where(
                                    AnalysisRun.id == uuid.UUID(run_id)
                                )
                            )
                            if run_row is None:
                                raise RuntimeError(f"AnalysisRun {run_id} not found")
                            run_row.full_result = report_json
                            run_row.status = RunStatus.complete
                            await write_db.commit()

                        await publish("complete", {"report": report_json})
                        logger.info("run complete: %s overall_score=%.1f", run_id, final_report.overall_score)

                    else:
                        error_msg = node_update.get("errors", {}).get(
                            "synthesizer", "synthesis failed"
                        )
                        async with SessionLocal() as write_db:
                            run_row = await write_db.scalar(
                                select(AnalysisRun).where(
                                    AnalysisRun.id == uuid.UUID(run_id)
                                )
                            )
                            run_row.status = RunStatus.failed
                            await write_db.commit()

                        await publish("failed", {"error": error_msg})
                        logger.error("synthesizer failed: %s", error_msg)

    except Exception as exc:
        logger.error("run_analysis_task unhandled exception: %s", exc, exc_info=True)
        try:
            async with SessionLocal() as db:
                run_row = await db.scalar(
                    select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(run_id))
                )
                if run_row:
                    run_row.status = RunStatus.failed
                    await db.commit()
            await publish("failed", {"error": str(exc)})
        except Exception:
            logger.exception("Failed to mark run %s as failed after exception", run_id)

    finally:
        await redis.aclose()  # type: ignore[attr-defined]
        await engine.dispose()


@celery_app.task(name="run_analysis_task")
def run_analysis_task(run_id: str) -> None:
    """Celery entry point — synchronous wrapper around the async pipeline."""
    asyncio.run(_async_run(run_id))
