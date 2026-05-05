"""LangGraph wiring for the W2 supervisor + worker graph.

This is the only module in :mod:`copilot.workers` that imports
``langgraph``. Everything else (state shape, routing logic, node
bodies) is plain Python and unit-testable without the LangGraph
runtime.

Graph shape:

::

                ┌──────────────┐
                │  supervisor  │  (entry)
                └──────┬───────┘
                       │ state["next"] dispatches
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   intake_extractor  evidence_retriever  answer
        │              │              │
        └──────┬───────┘              ▼
               ▼                    END
           supervisor
               │
               (loop until "answer", capped at HOP_LIMIT hops)

Construction:

    graph = build_graph(retriever=my_retriever)
    final_state = await graph.ainvoke({
        "message": "What does USPSTF say about HTN screening?",
        "patient_uuid": "...",
        "user_id": 1,
        "attachment": None,
        "extractions": [],
        "evidence": [],
        "hops": 0,
    })
    response = final_state["final_response"]
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from copilot.rag import Retriever
from copilot.workers.nodes import (
    answer_node,
    intake_extractor_node,
    make_evidence_retriever_node,
    supervisor_node,
)
from copilot.workers.state import (
    AttachmentInput,
    EvidenceRecord,
    ExtractionRecord,
)


class _GraphState(TypedDict, total=False):
    """LangGraph-flavored variant of WorkerState.

    Identical to :class:`copilot.workers.state.WorkerState` except
    for the LangGraph reducers on the list-typed fields, which let
    worker nodes append rather than overwrite. ``operator.add`` is
    the standard LangGraph reducer for lists.
    """

    message: str
    patient_uuid: str
    user_id: int
    attachment: AttachmentInput | None
    extractions: Annotated[list[ExtractionRecord], operator.add]
    evidence: Annotated[list[EvidenceRecord], operator.add]
    hops: int
    next: str
    final_response: dict[str, Any] | None


def build_graph(*, retriever: Retriever):
    """Compile the supervisor + worker graph.

    The retriever is injected so production wires Voyage + Cohere
    while tests pass a BM25-only :class:`copilot.rag.Retriever`.
    """

    from langgraph.graph import END, StateGraph  # type: ignore[import-untyped]

    builder = StateGraph(_GraphState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("intake_extractor", intake_extractor_node)
    builder.add_node(
        "evidence_retriever", make_evidence_retriever_node(retriever)
    )
    builder.add_node("answer", answer_node)

    builder.set_entry_point("supervisor")

    builder.add_conditional_edges(
        "supervisor",
        _route,
        {
            "intake_extractor": "intake_extractor",
            "evidence_retriever": "evidence_retriever",
            "answer": "answer",
        },
    )
    builder.add_edge("intake_extractor", "supervisor")
    builder.add_edge("evidence_retriever", "supervisor")
    builder.add_edge("answer", END)

    return builder.compile()


def _route(state: _GraphState) -> str:
    """Conditional-edge dispatcher. Reads ``state["next"]`` written
    by the supervisor node."""
    return state.get("next", "answer")
