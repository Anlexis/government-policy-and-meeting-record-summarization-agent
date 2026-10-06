"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status as AgentStatus.<X>.value — the enum's string value,
#    not the enum object itself
#  - Never import from mediator/, api/, or other agents
#
# GOV-C2-004 — SummarizeNode (inner DomainWorkflowGraph node 3).
# Builds a structured text summary from the extracted content and the document
# text. The summary is assembled deterministically from the extracted lists — no
# model call — so it can only ever contain text that was in the input document.
# The length cap comes from state: it is a caller option, validated by
# ValidateInputNode and carried into this graph by the caller-context bridge.
# Emits an audit event.

import json
import logging
import textwrap
from typing import Any, ClassVar, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Default maximum summary length (characters)
_DEFAULT_MAX_SUMMARY_CHARS: int = 3_000

# Document type labels for the summary header
_TYPE_LABELS = {
    "policy": "政策文書",
    "minutes": "会議議事録",
    "consultation": "パブリックコメント",
    "other": "文書",
}


def _build_summary(
    doc_text: str,
    extracted: dict[str, Any],
    document_type: str,
    max_chars: int,
) -> str:
    """Assemble a structured Markdown summary from extracted content.

    Falls back to a truncated excerpt of doc_text when no items were extracted.
    """
    agenda_items: List[str] = extracted.get("agenda_items", [])
    decisions: List[str] = extracted.get("decisions", [])
    action_items: List[str] = extracted.get("action_items", [])

    type_label = _TYPE_LABELS.get(document_type, "文書")
    lines = [f"## {type_label} 要約", ""]

    if agenda_items:
        lines.append("### 議事・議題")
        for i, item in enumerate(agenda_items, 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    if decisions:
        lines.append("### 決定事項")
        for i, dec in enumerate(decisions, 1):
            lines.append(f"{i}. {dec}")
        lines.append("")

    if action_items:
        lines.append("### アクションアイテム（担当者）")
        for i, action in enumerate(action_items, 1):
            lines.append(f"{i}. {action}")
        lines.append("")

    if not (agenda_items or decisions or action_items):
        # Fallback: truncated excerpt from the document
        excerpt = textwrap.shorten(doc_text, width=500, placeholder="…")
        lines.append("### 概要（抜粋）")
        lines.append(excerpt)

    summary = "\n".join(lines)

    if len(summary) > max_chars:
        summary = summary[: max_chars - 1] + "…"

    return summary


class SummarizeNode(FunctionNode):
    """Build a structured text summary from extracted content.

    Inner DomainWorkflowGraph node — runs after ExtractDecisionsActionsNode.

    Reads:
      extracted_content: JSON str — agenda_items, decisions, action_items
      document_type: str — used for the summary header label
      validated_input: str — fallback for excerpt when nothing was extracted

    Length cap:
      max_summary_chars from state — a validated caller option seeded by
      DomainWorkflowGraph._extra_initial_state(). Falls back to the documented
      default when this node is invoked outside the graph.

    Output (partial dict — only changed keys):
        summary, status, error_log.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        doc_text = state.get("validated_input") or state.get("user_input", "")
        document_type = state.get("document_type", "other")
        extracted_json = state.get("extracted_content")

        # Already validated as a finite, bounded integer by ValidateInputNode.
        max_chars = state.get("max_summary_chars") or _DEFAULT_MAX_SUMMARY_CHARS

        # extracted_content travels as a JSON string to keep State msgpack-safe.
        extracted: dict[str, Any] = {}
        if extracted_json:
            try:
                extracted = json.loads(extracted_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("SummarizeNode: failed to parse extracted_content JSON")

        if not doc_text and not extracted:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SummarizeNode: no content available for summarization"],
            }

        summary = _build_summary(doc_text, extracted, document_type, max_chars)

        emit_trace_event(
            "gov_c2_004.summarize",
            {
                "document_type": document_type,
                "summary_length": len(summary),
                "max_chars": max_chars,
                "extracted_agenda": len(extracted.get("agenda_items", [])),
                "extracted_decisions": len(extracted.get("decisions", [])),
                "extracted_actions": len(extracted.get("action_items", [])),
            },
            state,
        )

        return {
            "summary": summary,
            "status": AgentStatus.SUCCESS.value,
        }
