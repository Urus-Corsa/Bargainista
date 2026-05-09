from app.workers.celery_app import celery_app


@celery_app.task
def run_analysis(run_id: str) -> dict:
    """Placeholder — Phase 5 replaces this body with the LangGraph orchestrator call."""
    return {"run_id": run_id, "status": "pending"}
