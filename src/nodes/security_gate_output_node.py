"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status as AgentStatus.<X>.value — the enum's string value,
#    not the enum object itself
#  - Never import from mediator/, api/, or other agents
#
# GOV-C2-004 — SecurityGateOutputNode (post_process backbone slot).
#
# This is the last thing that touches the text before the caller receives it, and
# it is where the template's output guarantee is ENFORCED rather than merely
# intended:
#
#   1. restricted content — a line carrying a classified-material marker is
#      replaced in full. Replacing only the marker word would leave the sentence
#      that follows it intact, which is the verbatim re-emission the gate exists
#      to prevent;
#   2. non-disclosable content, for the public audience — re-applied here even
#      though the formatting node already applied it, so the guarantee does not
#      depend on an upstream node having run;
#   3. length cap — applied at a line boundary, after redaction, and the result is
#      re-scanned. Trimming text can never introduce a marker, but re-scanning is
#      what makes that a checked property instead of an assumption.
#
# Both the caller-facing field and the generic result field are written with the
# gated text: the framework surfaces `formatted_output or result`, so leaving the
# pre-gate text in `result` would give a caller a route around the gate.
#
# The scan is a MODULE-LEVEL helper function, not an instance method. A method
# named like a gate hook on a FunctionNode subclass is auto-wrapped into the
# LangGraph chain; the wrapped hook returns None on the clean path, LangGraph
# passes None as the next node's state, and BaseNode.__call__ raises
# AttributeError.
#
# The template renders no monetary aggregates — it reproduces structured excerpts
# of a document — so there is no numeric rounding grid here and no numeric
# rewriting of any kind. Document identifiers, article numbers, fiscal years and
# figures quoted from the source pass through byte-identical; the test suite pins
# that in both directions.

import logging
import re
from typing import Any, ClassVar, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG

logger = logging.getLogger(__name__)

# Markers for classified/restricted government content that must not be
# re-emitted to any caller, whatever audience was requested.
# References: 特定秘密保護法, 防衛秘密 classification, 外務省 handling guidelines.
_CLASSIFIED_PATTERNS = [
    r"機密情報",  # classified information
    r"特定秘密",  # specially designated secrets (特定秘密保護法)
    r"防衛秘密",  # defence secrets
    r"外交上の秘密",  # diplomatic secrets
    # A character class here would have meant "内閣情報" + ONE of 調/査 + "室",
    # which never matches the actual name; the 調査 part is optional, not a choice.
    r"内閣情報(?:調査)?室",  # Cabinet Intelligence and Research Office materials
    r"未公表.*個人情報",  # unreleased personal information
]
_CLASSIFIED_RE = re.compile("|".join(_CLASSIFIED_PATTERNS))

# 非開示情報 markers — 情報公開法 Article 5 exemption categories. Applied to
# public-audience output only; the internal audience is entitled to this content.
_NON_DISCLOSURE_PATTERNS = [
    r"秘密",  # secret / classified
    r"非公開",  # non-public
    r"内部情報",  # internal information
    r"個人情報",  # personal information
    r"プライバシー",  # privacy
    r"機密",  # confidential
    r"防衛",  # defence-related
    r"外交.*秘",  # diplomatic secrets
]
_NON_DISCLOSURE_RE = re.compile("|".join(_NON_DISCLOSURE_PATTERNS))

# Maximum output length before truncation (characters).
_MAX_OUTPUT_CHARS: int = 10_000

# The markers deliberately contain none of the words either scan looks for, so
# a redacted line cannot be re-labelled by the other layer and the re-scan is
# a genuine no-op on already-clean text.
_CLASSIFIED_MARKER = "[法令上の指定により除外された記載]"
_NON_DISCLOSURE_MARKER = "[非開示情報のため省略（情報公開法第5条）]"
_TRUNCATION_NOTICE = "[出力長上限のため以降を省略]"

# The public audience only ever sees text that survived both scans.
_PUBLIC_AUDIENCE = "public"


def _redact_lines(text: str, pattern: "re.Pattern[str]", marker: str) -> Tuple[str, bool]:
    """Replace every line matching `pattern` with `marker`.

    Whole lines, not the matched words: the sentence around a marker is the
    content being protected. Returns (text, whether anything was replaced).
    """
    lines: List[str] = text.split("\n")
    triggered = False
    for index, line in enumerate(lines):
        if pattern.search(line):
            lines[index] = marker
            triggered = True
    return "\n".join(lines), triggered


def _truncate_at_line_boundary(text: str, limit: int) -> Tuple[str, bool]:
    """Trim `text` to at most `limit` characters without splitting a line.

    Cutting mid-line could leave half of a redaction marker in the output, which
    reads as ordinary text. Returns (text, whether it was truncated).
    """
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    boundary = head.rfind("\n")
    if boundary > 0:
        head = head[:boundary]
    return head.rstrip() + "\n" + _TRUNCATION_NOTICE, True


def _security_gate_output(text: str, audience_mode: str) -> Tuple[str, bool]:
    """Apply the output policy. Returns (sanitized_text, gate_triggered).

    Module-level function — NOT an instance method (see the header note).
    """
    sanitized, triggered = _redact_lines(text, _CLASSIFIED_RE, _CLASSIFIED_MARKER)

    if audience_mode == _PUBLIC_AUDIENCE:
        sanitized, public_triggered = _redact_lines(sanitized, _NON_DISCLOSURE_RE, _NON_DISCLOSURE_MARKER)
        triggered = triggered or public_triggered

    sanitized, _ = _truncate_at_line_boundary(sanitized, _MAX_OUTPUT_CHARS)

    # Re-scan after trimming. Redaction is idempotent — a marker line matches its
    # own pattern and is replaced by the same marker — so a second pass can only
    # remove something the first pass had not yet seen.
    sanitized, rescan_triggered = _redact_lines(sanitized, _CLASSIFIED_RE, _CLASSIFIED_MARKER)
    if audience_mode == _PUBLIC_AUDIENCE:
        sanitized, public_rescan = _redact_lines(sanitized, _NON_DISCLOSURE_RE, _NON_DISCLOSURE_MARKER)
        rescan_triggered = rescan_triggered or public_rescan

    return sanitized, triggered or rescan_triggered


# Reason code -> the sentence the caller reads. A code with no entry falls
# back to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


class SecurityGateOutputNode(FunctionNode):
    """Enforce the output policy for GOV-C2-004 (post_process backbone slot).

    Emits an audit event on every invocation recording the gate decision and the
    output length. The output text itself is never written to the audit trail.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # A run declined upstream has nothing to format. Render the reason as
        # the caller-facing body and carry the marker onward.
        marker = state.get("error_code")
        if marker:
            message = _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED)
            emit_trace_event("post_process_degraded", {"reason": marker}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
                "result": message,
                "formatted_output": message,
            }
        # Prefer formatted_summary (set by the inner workflow); fall back through
        # summary → result so a partially-completed run is still gated.
        output_text = state.get("formatted_summary") or state.get("summary") or state.get("result") or ""

        if not output_text:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "SecurityGateOutputNode: no output text to gate "
                    "(formatted_summary, summary and result are all empty)"
                ],
            }

        audience_mode = state.get("audience_mode") or "internal"
        sanitized_output, gate_triggered = _security_gate_output(output_text, audience_mode)

        emit_trace_event(
            "gov_c2_004.security_gate_output",
            {
                "gate_triggered": gate_triggered,
                "output_length": len(sanitized_output),
                "audience_mode": audience_mode,
                "document_type": state.get("document_type", "other"),
            },
            state,
        )

        return {
            # Both fields carry the gated text: the framework surfaces
            # `formatted_output or result`, so `result` must not keep the
            # pre-gate version.
            "formatted_output": sanitized_output,
            "result": sanitized_output,
            "status": AgentStatus.SUCCESS.value,
        }
