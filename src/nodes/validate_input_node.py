"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status as AgentStatus.<X>.value — the enum's string value,
#    not the enum object itself
#  - Read input_context via state.get("input_context", {}) — read-only
#  - Never import from mediator/, api/, or other agents
#
# GOV-C2-004 — ValidateInputNode (pre_process backbone slot).
#
# This node owns the caller contract. Everything a caller can send — the document
# text and every option in input_context — is validated here, and nothing else in
# the pipeline reads raw caller data.
#
# Rules enforced (all fail CLOSED — a value that cannot be proven safe is an
# error, never a silent fallback):
#   - document text: present, a string, and within the supported size band;
#   - the document channel is refused outright when it carries instruction-override
#     content, so text that tries to steer the pipeline never becomes an input to
#     the summary. This is enforced here, in the node that owns the contract, and
#     not delegated to the surrounding runtime;
#   - audience_mode: one of the two supported audiences. An unrecognised value is
#     REFUSED rather than defaulted, because defaulting to the internal audience
#     would answer a request for a redacted summary with an unredacted one;
#   - max_summary_chars: parsed as a finite number inside an explicit range —
#     NaN and infinity parse happily via float() and then compare False against
#     every bound, which would disable the length cap;
#   - channel: locked to an inert identifier, because it is recorded in the audit
#     trail;
#   - input_context is capped by key count, and options the template does not
#     consume are ignored — they can never influence behaviour or reach output.
#
# Rejected values are never echoed back: an error names the field, not the input.

import logging
import math
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import INPUT_REJECTED
from src.services.progress import emit_progress

logger = logging.getLogger(__name__)

# Document size gates (characters). 500,000 characters is roughly 250 pages of
# Japanese text — beyond that the caller is expected to chunk the document.
_MIN_DOCUMENT_CHARS: int = 10
_MAX_DOCUMENT_CHARS: int = 500_000

# The two audiences this template can write for.
_ACCEPTED_AUDIENCE_MODES: frozenset[str] = frozenset({"internal", "public"})
_DEFAULT_AUDIENCE_MODE: str = "internal"

# Summary length cap: bounds for the caller-supplied override, and the value used
# when the caller does not supply one.
_MIN_SUMMARY_CHARS: int = 200
_MAX_SUMMARY_CHARS: int = 20_000
_DEFAULT_SUMMARY_CHARS: int = 3_000

# The channel label is written into the audit trail, so it is locked to an inert
# identifier — free text there would be caller-controlled log content.
_CHANNEL_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_DEFAULT_CHANNEL: str = "unknown"

# Structural cap on the options mapping itself.
_MAX_CONTEXT_KEYS: int = 32

# Options this node consumes; anything else in input_context is ignored.
_CONSUMED_CONTEXT_KEYS: frozenset[str] = frozenset({"audience_mode", "channel", "max_summary_chars"})

# Instruction-override content in the document channel. A government document
# describes decisions and their owners; it never addresses the processing
# pipeline. These forms do, so the document is refused rather than summarised.
# Ordinary words that appear in these phrases (指示, 通知, "system", "ignore")
# are deliberately NOT matched on their own — only the override construction is.
_INSTRUCTION_OVERRIDE_PATTERNS = [
    r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)*(?:previous|prior|above|preceding|earlier)\s+(?:instructions?|prompts?|rules?)",
    r"(?:ignore|disregard)\s+everything\s+(?:above|before)",
    r"(?:system|developer)\s*(?:prompt|message|instruction)s?\s*[:：]",
    r"you\s+are\s+now\s+(?:a|an|the)\b",
    r"<\|\s*(?:im_start|im_end|system|endoftext)\s*\|>",
    r"\[\s*/?\s*(?:INST|SYS|SYSTEM)\s*\]",
    r"(?:これまで|上記|前|以前)の(?:指示|命令|ルール)[^\n]{0,8}無視",
    r"システム(?:プロンプト|命令)[^\n]{0,8}(?:表示|出力|開示|変更|上書き)",
]
_INSTRUCTION_OVERRIDE_RE = re.compile("|".join(_INSTRUCTION_OVERRIDE_PATTERNS), re.IGNORECASE)


class CallerContractError(ValueError):
    """A caller-supplied value could not be accepted.

    The message names the field and the bound it failed; it never contains the
    rejected value itself.
    """


def _finite_in_range(value: Any, field: str, minimum: int, maximum: int) -> int:
    """Parse a caller-supplied number, or raise CallerContractError.

    Accepts ints, floats and numeric strings. Rejects booleans (``True`` is an
    int in Python and would read as 1), non-numeric text, NaN and ±infinity, and
    anything outside ``[minimum, maximum]``. The returned value is an int, so a
    fractional override cannot reach a slice bound.
    """
    if isinstance(value, bool):
        raise CallerContractError(f"{field}: must be a number")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise CallerContractError(f"{field}: must be a number")
        try:
            number = float(text)
        except ValueError:
            raise CallerContractError(f"{field}: must be a number") from None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise CallerContractError(f"{field}: must be a number")

    if not math.isfinite(number):
        raise CallerContractError(f"{field}: must be a finite number")
    if not (minimum <= number <= maximum):
        raise CallerContractError(f"{field}: must be between {minimum} and {maximum}")
    return int(number)


def _accepted_audience_mode(value: Any) -> str:
    """Return the requested audience, or raise CallerContractError."""
    if not isinstance(value, str):
        raise CallerContractError("audience_mode: must be a string")
    mode = value.strip().lower()
    if mode not in _ACCEPTED_AUDIENCE_MODES:
        raise CallerContractError("audience_mode: must be one of " + ", ".join(sorted(_ACCEPTED_AUDIENCE_MODES)))
    return mode


def _accepted_channel(value: Any) -> str:
    """Return the channel label, or raise CallerContractError."""
    if not isinstance(value, str) or not _CHANNEL_RE.match(value):
        raise CallerContractError("channel: must match [a-z0-9_] and be at most 32 characters")
    return value


class ValidateInputNode(FunctionNode):
    """Validate the document and the caller's options (pre_process backbone slot).

    Output (partial dict — only changed keys):
        validated_input, audience_mode, max_summary_chars, request_channel,
        status, error_log.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        user_input = state.get("user_input", "")

        if not isinstance(user_input, str) or not user_input.strip():
            return self._reject("user_input: document text is empty or missing", "EMPTY_INPUT")

        doc_text = user_input.strip()

        if len(doc_text) < _MIN_DOCUMENT_CHARS:
            return self._reject(f"user_input: document is shorter than the {_MIN_DOCUMENT_CHARS}-character minimum")
        if len(doc_text) > _MAX_DOCUMENT_CHARS:
            return self._reject(
                f"user_input: document exceeds the {_MAX_DOCUMENT_CHARS}-character maximum", "QUESTION_TOO_LONG"
            )
        if _INSTRUCTION_OVERRIDE_RE.search(doc_text):
            # Refused here, in the node that owns the caller contract — the
            # document never reaches classification, extraction or the summary.
            return self._reject("user_input: document contains instruction-override content", "")

        options = state.get("input_context", {})
        if not isinstance(options, dict):
            return self._reject("input_context: must be a mapping")
        if len(options) > _MAX_CONTEXT_KEYS:
            return self._reject(f"input_context: at most {_MAX_CONTEXT_KEYS} options are accepted")

        try:
            audience_mode = (
                _accepted_audience_mode(options["audience_mode"])
                if "audience_mode" in options
                else _DEFAULT_AUDIENCE_MODE
            )
            max_summary_chars = (
                _finite_in_range(
                    options["max_summary_chars"],
                    "max_summary_chars",
                    _MIN_SUMMARY_CHARS,
                    _MAX_SUMMARY_CHARS,
                )
                if "max_summary_chars" in options
                else _DEFAULT_SUMMARY_CHARS
            )
            channel = _accepted_channel(options["channel"]) if "channel" in options else _DEFAULT_CHANNEL
        except CallerContractError as exc:
            return self._reject(str(exc))

        emit_trace_event(
            "gov_c2_004.validate_input",
            {
                "doc_length": len(doc_text),
                "audience_mode": audience_mode,
                "max_summary_chars": max_summary_chars,
                "channel": channel,
                # Count only — the template never records what an ignored option
                # was called or what it contained.
                "ignored_options": len(set(options) - _CONSUMED_CONTEXT_KEYS),
            },
            state,
        )

        return {
            "validated_input": doc_text,
            "audience_mode": audience_mode,
            "max_summary_chars": max_summary_chars,
            "request_channel": channel,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _reject(reason: str, code: str = "INVALID_REQUEST") -> dict[str, Any]:
        """Build the rejection result. `reason` names a field, never a value.

        `code` separates the two kinds of rejection. With a code, the caller can
        correct the request: the run COMPLETES carrying that reason, so the
        caller reads what to fix and can send a corrected document on the same
        conversation. Without one, the content itself is refused and the run
        terminates rather than inviting a resend.
        """
        if code:
            emit_progress(INPUT_REJECTED)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": code,
                "error_log": [f"ValidateInputNode: {reason}"],
            }
        return {
            "status": AgentStatus.ERROR.value,
            "error_log": [f"ValidateInputNode: {reason}"],
        }
