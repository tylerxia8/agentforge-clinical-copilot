"""W2 supervisor + worker graph.

Three workers, one supervisor:

- :func:`route_decision` — pure routing logic, no LangGraph
  dependency. Decides the next worker based on what's in the state
  (attachment yet to extract, evidence not yet retrieved, hop cap
  hit). Used by the supervisor node.
- :func:`intake_extractor_node` — wraps
  :mod:`copilot.extraction.vision` to turn an attached PDF into a
  validated extraction. Async.
- :func:`evidence_retriever_node` — wraps
  :mod:`copilot.rag.Retriever` to fetch top-k guideline chunks for
  the user's question. Sync.
- :func:`answer_node` — composes the existing W1 orchestrator with
  any extractions + evidence the workers produced into the final
  response. Async.
- :func:`build_graph` — assembles the four nodes into a LangGraph
  ``StateGraph`` with the supervisor at the entry point and a
  conditional edge dispatching by routing decision. The only
  function in this package that imports ``langgraph``.

State shape :class:`WorkerState`. See :doc:`W2_ARCHITECTURE.md` §2
for the design.
"""

from copilot.workers.routing import (
    HOP_LIMIT,
    EVIDENCE_TRIGGER_TOKENS,
    RouteDecision,
    route_decision,
)
from copilot.workers.state import WorkerState

__all__ = [
    "EVIDENCE_TRIGGER_TOKENS",
    "HOP_LIMIT",
    "RouteDecision",
    "WorkerState",
    "route_decision",
]
