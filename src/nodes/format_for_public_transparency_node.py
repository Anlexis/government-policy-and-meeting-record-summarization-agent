"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status as AgentStatus.<X>.value — the enum's string value,
#    not the enum object itself
#  - Never import from mediator/, api/, or other agents
#
# GOV-C2-004 — FormatForPublicTransparencyNode (inner DomainWorkflowGraph node 4).
# Formats the structured summary for the requested audience.
#   internal: full detail + document type header (civil servant briefing).
#   public:   lines matching 非開示情報 patterns are replaced, per 情報公開法.
# The output gate re-applies the same public-audience rule at the boundary, so
# this node's redaction is the first of two independent layers, not the only one.
# Emits an audit event carrying the output length — never the formatted text.

import logging
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# 非開示情報 patterns — 情報公開法 Article 5 exemption categories.
# Lines matching these patterns are redacted in public-mode output.
_NON_DISCLOSURE_PATTERNS = [
    r"秘密",  # secret / classified
    r"非公開",  # non-public
    r"内部情報",  # internal information
    r"個人情報",  # personal information (PII)
    r"プライバシー",  # privacy
    r"機密",  # confidential
    r"防衛",  # defence-related
    r"外交.*秘",  # diplomatic secrets
]
_NON_DISCLOSURE_RE = re.compile("|".join(_NON_DISCLOSURE_PATTERNS))

_REDACTION_MARKER = "[非開示情報のため省略（情報公開法第5条）]"

# Document type labels for the formatted output header
_INTERNAL_LABELS = {
    "policy": "政策文書",
    "minutes": "会議議事録",
    "consultation": "パブリックコメント",
    "other": "その他の文書",
}
_PUBLIC_LABELS = {
    "policy": "政策",
    "minutes": "会議",
    "consultation": "意見募集",
    "other": "文書",
}


def _format_internal(summary: str, document_type: str) -> str:
    """Format for internal civil servant briefing.

    Full detail — no redaction. Adds an internal-only header.
    """
    label = _INTERNAL_LABELS.get(document_type, "文書")
    header = f"【内部配布用】{label} 要約\n" + "=" * 40
    return f"{header}\n\n{summary}"


def _format_public(summary: str, document_type: str) -> str:
    """Format for public-facing output.

    Redacts any line containing 非開示情報 markers (情報公開法 Article 5 compliance).
    Adds a public-facing header.
    """
    label = _PUBLIC_LABELS.get(document_type, "文書")
    header = f"【公開用】{label} 要約\n" + "=" * 40

    redacted_lines = []
    for line in summary.split("\n"):
        if _NON_DISCLOSURE_RE.search(line):
            redacted_lines.append(_REDACTION_MARKER)
        else:
            redacted_lines.append(line)

    return f"{header}\n\n" + "\n".join(redacted_lines)


class FormatForPublicTransparencyNode(FunctionNode):
    """Format the summary for the target audience (internal or public).

    Inner DomainWorkflowGraph node — last step before the inner graph returns
    its output to the outer backbone's merge_output().

    Reads:
      summary: str — structured text summary from SummarizeNode
      document_type: str — used for the formatted header label
      audience_mode: str — "internal" | "public"; the validated caller option,
        seeded into this graph by DomainWorkflowGraph._extra_initial_state().
        Defaults to the internal briefing form when the node is invoked directly.

    Output (partial dict — only changed keys):
        formatted_summary, status, error_log.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        summary = state.get("summary", "")
        document_type = state.get("document_type", "other")
        audience_mode = state.get("audience_mode", "internal")

        if not summary:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "FormatForPublicTransparencyNode: no summary to format " "(SummarizeNode may have failed)"
                ],
            }

        if audience_mode == "public":
            formatted = _format_public(summary, document_type)
        else:
            formatted = _format_internal(summary, document_type)

        emit_trace_event(
            "gov_c2_004.format_for_public_transparency",
            {
                "audience_mode": audience_mode,
                "document_type": document_type,
                "output_length": len(formatted),
            },
            state,
        )

        return {
            "formatted_summary": formatted,
            "status": AgentStatus.SUCCESS.value,
        }
