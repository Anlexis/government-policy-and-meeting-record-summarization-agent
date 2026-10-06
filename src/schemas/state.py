"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic BaseModel. LangGraph
# checkpoints use msgpack serialization, and Pydantic objects cause silent
# corruption there. Extend AgentState with agent-specific fields only, and never
# add credentials or secrets.
#
# GOV-C2-004 — Government Policy & Meeting Record Summarization Agent.
# Cat 2 two-layer nested graph: outer backbone (AgentBaseGraph) + inner domain
# workflow (BaseGraph). The fields below are shared by both layers.
#
# Document flow:
#   user_input (raw) → ValidateInputNode → validated_input
#   validated_input → inner domain workflow → formatted_summary
#   formatted_summary → SecurityGateOutputNode → formatted_output
#
# The output the caller receives is formatted_output, i.e. the text that passed
# the output gate. Every node emits an audit event; the gate additionally records
# its decision.

from typing import Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Flat TypedDict for GOV-C2-004.

    All shared fields (user_input, status, session_id, node_history, error_log,
    formatted_output, hitl_*, etc.) are inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Caller contract — written by ValidateInputNode (pre_process slot)
    # ------------------------------------------------------------------

    # Sanitized document text. Raw user_input is not read past this node.
    validated_input: Optional[str]

    # The audience the summary is written for: "internal" (full detail) or
    # "public" (non-disclosable passages redacted). Validated from the caller's
    # options; an unrecognised value is refused, never defaulted.
    audience_mode: Optional[str]

    # Upper bound on summary length in characters. Validated from the caller's
    # options as a finite number inside an explicit range.
    max_summary_chars: Optional[int]

    # Inert channel label recorded in the audit trail.
    request_channel: Optional[str]

    # ------------------------------------------------------------------
    # Inner domain workflow — DetectDocumentTypeNode
    # ------------------------------------------------------------------

    # Government document type detected by keyword pattern matching.
    # Values: "policy" (閣議決定 etc.) | "minutes" (議事録 etc.)
    #       | "consultation" (パブリックコメント) | "other"
    document_type: Optional[str]

    # True when document_type is "other" — outside the supported taxonomy.
    # Out-of-scope does NOT set status=ERROR; the pipeline proceeds as "other".
    out_of_scope: bool

    # ------------------------------------------------------------------
    # Inner domain workflow — ExtractDecisionsActionsNode
    # ------------------------------------------------------------------

    # Structured extraction result stored as a JSON string to keep State flat and
    # msgpack-safe.
    # Schema: {"agenda_items": [str, ...], "decisions": [str, ...], "action_items": [str, ...]}
    # Written by ExtractDecisionsActionsNode; read by SummarizeNode.
    extracted_content: Optional[str]

    # ------------------------------------------------------------------
    # Inner domain workflow — SummarizeNode
    # ------------------------------------------------------------------

    # Structured text summary. Plain text; section headers use Markdown (#, ##).
    # Read by FormatForPublicTransparencyNode.
    summary: Optional[str]

    # ------------------------------------------------------------------
    # Inner domain workflow — FormatForPublicTransparencyNode
    # ------------------------------------------------------------------

    # Audience-appropriate formatted summary.
    # Internal audience: full detail + type header.
    # Public audience: 非開示情報 redacted per 情報公開法.
    # Must pass SecurityGateOutputNode before it reaches the caller.
    formatted_summary: Optional[str]

    # ------------------------------------------------------------------
    # Status / tracing — populated by all nodes
    # ------------------------------------------------------------------

    # AgentStatus string value ("success" or "error").
    status: Optional[str]

    # Correlation ID injected by InitializeNode for audit log correlation.
    trace_id: Optional[str]
    error_code: Optional[str]
