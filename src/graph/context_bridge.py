"""AgentCore Platform v1.0"""

# GOV-C2-004 — caller-context bridge between the outer and the inner graph.
#
# The framework's GraphNode calls
#     subgraph.invoke(user_input, session_id=..., ctx=...)
# which carries the document text but NOT the caller's `input_context` mapping.
# The inner graph therefore starts with an empty input_context, and any caller
# option that the inner nodes need (the audience the summary is written for, the
# summary length cap) would silently fall back to its default — the caller would
# ask for a public, redacted summary and receive the full internal one.
#
# This module closes that gap with a ContextVar:
#
#   outer PolicySummarizationGraphNode.extract_input()  → publish_caller_context()
#   inner DomainWorkflowGraph._extra_initial_state()    → read_caller_context()
#
# Both run on the same call stack inside one GraphNode invocation, so the value
# published by the outer node is the value the inner graph reads, and concurrent
# invocations (threads, async tasks) each see their own value.
#
# Only ALREADY-VALIDATED values are published: ValidateInputNode is the single
# owner of the caller contract, and the bridge transports its output — it never
# carries raw caller data.

from contextvars import ContextVar
from typing import Any

_CALLER_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("gov_c2_004_caller_context", default={})


def publish_caller_context(values: dict[str, Any]) -> None:
    """Publish the validated caller options for the inner graph to pick up.

    A defensive copy is stored so a later mutation of the caller's dict cannot
    change what the inner graph already read.
    """
    _CALLER_CONTEXT.set(dict(values))


def read_caller_context() -> dict[str, Any]:
    """Return the validated caller options published for this invocation.

    Returns an empty dict when the inner graph is invoked directly (unit tests,
    or any caller that bypasses the outer graph) — every inner node applies its
    own documented default in that case.
    """
    return dict(_CALLER_CONTEXT.get())


def clear_caller_context() -> None:
    """Reset the bridge to empty. Used by tests to prove isolation."""
    _CALLER_CONTEXT.set({})
