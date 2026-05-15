"""LangGraph orchestrator — wires the three agents into a staged fan-out graph.

Graph topology (staged fan-out, Decision 1 in .design/phase4_langgraph_orchestration.md):

    START ──► vision_node ─────────────────────────────────┐
          ──► history_node ──────────────────────────────► finance_dependent_node ──► synthesizer_node
          ──► finance_independent_node ───────────────────┘

Vision, History, and Finance-independent run in parallel. Finance-dependent
joins after all three, aggregates repair costs, and computes the finance score.
Synthesizer runs last and produces the FinalReport.

Each node catches its own exceptions — the graph never sees an unhandled error.
Failed nodes write to state["errors"] and leave their result field as None.
Downstream nodes handle None gracefully (degraded mode, not crash).

Entry point:
    run_analysis(listing, images, db) -> FinalReport
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

from app.agents import finance, history, vision
from app.agents.finance import FinancePrecomputed
from app.agents.synthesizer import synthesize
from app.models.schemas import (
    FinanceAgentResult,
    FinalReport,
    HistoryAgentResult,
    ListingInput,
    VisionAgentResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

def _merge_errors(a: dict[str, str], b: dict[str, str]) -> dict[str, str]:
    """Reducer for the errors dict — merges entries from concurrent nodes."""
    return {**a, **b}


class AnalysisState(TypedDict):
    """Shared state for the analysis graph. All nodes read from and write to this.

    Inputs (set once at START, never modified):
        listing: Enriched ListingInput from the ingestion pipeline.

    images are passed via RunnableConfig configurable["images"] rather than state,
    so LangSmith does not capture the base64 payloads in node traces.

    Parallel phase results (None = not yet complete OR agent failed):
        Downstream nodes must handle None explicitly — None is meaningful, not missing.

    errors: agent_name → error message for every node that caught an exception.
        Annotated with a merge reducer so parallel nodes can each write their
        own error without overwriting each other's entries.
    """
    listing: ListingInput

    vision_result: Optional[VisionAgentResult]
    history_result: Optional[HistoryAgentResult]
    finance_precomputed: Optional[FinancePrecomputed]

    finance_result: Optional[FinanceAgentResult]

    errors: Annotated[dict[str, str], _merge_errors]

    final_report: Optional[FinalReport]


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


async def vision_node(state: AnalysisState, config: RunnableConfig) -> dict:
    images: list[str] = config["configurable"].get("images", [])
    try:
        result = await vision.run(state["listing"], images)
        logger.info("vision_node: condition_score=%d", result.condition_score)
        return {"vision_result": result}
    except Exception as exc:
        logger.error("vision_node failed: %s", exc, exc_info=True)
        return {"vision_result": None, "errors": {"vision": str(exc)}}


async def history_node(state: AnalysisState) -> dict:
    try:
        result = await history.run(state["listing"])
        logger.info("history_node: risk_score=%d", result.risk_score)
        return {"history_result": result}
    except Exception as exc:
        logger.error("history_node failed: %s", exc, exc_info=True)
        return {"history_result": None, "errors": {"history": str(exc)}}


async def finance_independent_node(state: AnalysisState, config: RunnableConfig) -> dict:
    db: AsyncSession = config["configurable"]["db"]
    try:
        precomputed = await finance.run_independent(state["listing"], db)
        logger.info(
            "finance_independent_node: market_value=$%d",
            precomputed["estimated_market_value"],
        )
        return {"finance_precomputed": precomputed}
    except Exception as exc:
        logger.error("finance_independent_node failed: %s", exc, exc_info=True)
        return {"finance_precomputed": None, "errors": {"finance_independent": str(exc)}}


async def finance_dependent_node(state: AnalysisState) -> dict:
    try:
        result = await finance.run_dependent(
            listing=state["listing"],
            precomputed=state["finance_precomputed"],
            vision_result=state["vision_result"],
            history_result=state["history_result"],
        )
        score_str = str(result.finance_score) if result.finance_score is not None else "N/A"
        logger.info("finance_dependent_node: finance_score=%s", score_str)
        return {"finance_result": result}
    except Exception as exc:
        logger.error("finance_dependent_node failed: %s", exc, exc_info=True)
        return {"finance_result": None, "errors": {"finance_dependent": str(exc)}}


async def synthesizer_node(state: AnalysisState) -> dict:
    try:
        report = await synthesize(
            listing=state["listing"],
            vision_result=state["vision_result"],
            history_result=state["history_result"],
            finance_result=state["finance_result"],
            finance_precomputed=state["finance_precomputed"],
            errors=state["errors"],
        )
        logger.info(
            "synthesizer_node: overall_score=%.1f recommendation=%s",
            report.overall_score, report.recommendation.value,
        )
        return {"final_report": report}
    except Exception as exc:
        logger.error("synthesizer_node failed: %s", exc, exc_info=True)
        return {"errors": {"synthesizer": str(exc)}}


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    """Construct the StateGraph. Called once at module load."""
    graph = StateGraph(AnalysisState)

    graph.add_node("vision_node", vision_node)
    graph.add_node("history_node", history_node)
    graph.add_node("finance_independent_node", finance_independent_node)
    graph.add_node("finance_dependent_node", finance_dependent_node)
    graph.add_node("synthesizer_node", synthesizer_node)

    # Parallel fan-out from START
    graph.add_edge(START, "vision_node")
    graph.add_edge(START, "history_node")
    graph.add_edge(START, "finance_independent_node")

    # Three-way join into Finance-dependent
    graph.add_edge("vision_node", "finance_dependent_node")
    graph.add_edge("history_node", "finance_dependent_node")
    graph.add_edge("finance_independent_node", "finance_dependent_node")

    # Finance-dependent → Synthesizer → END
    graph.add_edge("finance_dependent_node", "synthesizer_node")
    graph.add_edge("synthesizer_node", END)

    return graph


# Module-level compiled graph — compiled once, invoked many times.
# finance_independent_node needs a db session injected per-invocation;
# LangGraph passes extra kwargs through to nodes that declare them.
_graph = _build_graph().compile()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def stream_analysis(
    listing: ListingInput,
    images: list[str],
    db: AsyncSession,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Async generator that yields (node_name, node_update) as each node completes.

    node_update is the dict returned by the node (pre-reducer). The caller inspects
    node_name to decide what to persist and publish. finance_independent_node and
    synthesizer_node are included — the caller decides whether to act on them.
    """
    initial_state: AnalysisState = {
        "listing": listing,
        "vision_result": None,
        "history_result": None,
        "finance_precomputed": None,
        "finance_result": None,
        "errors": {},
        "final_report": None,
    }
    async for update in _graph.astream(
        initial_state,
        config={"configurable": {"db": db, "images": images}},
        stream_mode="updates",
    ):
        for node_name, node_update in update.items():
            yield node_name, node_update


async def run_analysis(
    listing: ListingInput,
    images: list[str],
    db: AsyncSession,
) -> FinalReport:
    """Run the full analysis pipeline and return the FinalReport.

    Args:
        listing: Enriched ListingInput (post-ingestion, with VIN specs resolved).
        images:  Flat list of base64 image strings from ingestion.normalise_images().
        db:      Async database session for Finance agent DB queries.

    Returns:
        FinalReport — always returned, never raises. If all agents fail,
        returns a neutral FinalReport (score 5.0) with error notes in key_reasons.

    Raises:
        RuntimeError: Only if the graph itself fails to execute (graph build error,
                      not an agent failure). Agent failures are handled internally.
    """
    initial_state: AnalysisState = {
        "listing": listing,
        "vision_result": None,
        "history_result": None,
        "finance_precomputed": None,
        "finance_result": None,
        "errors": {},
        "final_report": None,
    }

    final_state = await _graph.ainvoke(
        initial_state,
        config={"configurable": {"db": db, "images": images}},
    )

    if final_state.get("final_report") is None:
        errors = final_state.get("errors", {})
        logger.error("run_analysis: synthesizer produced no report. Errors: %s", errors)
        raise RuntimeError(
            f"Analysis pipeline failed to produce a report. Agent errors: {errors}"
        )

    return final_state["final_report"]
