"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status as AgentStatus.<X>.value — the enum's string value,
#    not the enum object itself
#  - Never import from mediator/, api/, or other agents
#
# GOV-C2-004 — ExtractDecisionsActionsNode (inner DomainWorkflowGraph node 2).
# Extracts agenda items, decisions (決定事項) and action items (担当者) from
# Japanese government document text. The result travels as a JSON string in
# state.extracted_content, so State stays flat and msgpack-safe.
# The audit event records extraction counts only — never the extracted text,
# which can carry non-public deliberation content.

import json
import logging
import re
from typing import Any, ClassVar, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# ── Extraction patterns ────────────────────────────────────────────────────────

# Agenda item markers (numbered items following 議事/議題/議案/etc.)
_AGENDA_RE = re.compile(
    r"(?:^|\n)\s*(?:議題|議事|議案|案件|報告事項|審議事項)\s*[:\uff1a]?\s*" r"(?:\d+[.\u3001\uff0e]?\s*)?(.+?)(?=\n|$)",
    re.MULTILINE,
)

# Decision markers. Japanese puts the decision verb at the END of the sentence
# ("……を決定した。"), so the content is the SENTENCE that carries the marker, not
# the fragment after it. Matching the fragment produced items like "。" followed
# by the next line, which is why sentences are the unit here.
_DECISION_MARKER_RE = re.compile(r"決定|決議|了承|了解|承認|採択|合意|確認")

# Sentence boundaries in Japanese administrative prose.
_SENTENCE_SPLIT_RE = re.compile(r"[。\n]+")

# Action item / owner markers
_ACTION_RE = re.compile(
    r"(?:担当|担当者|担当省庁|担当部署|対応|措置|実施|フォロー)[：:\uff1a]\s*(.+?)(?:\n|$)",
    re.MULTILINE,
)

_MAX_ITEMS_PER_TYPE: int = 20
_MAX_ITEM_CHARS: int = 200
_MIN_ITEM_CHARS: int = 5

# Characters that carry no content on their own — an "item" made only of these is
# punctuation the splitter left behind, not an extracted decision.
_PUNCTUATION_ONLY_RE = re.compile(r"^[\s。、,.\u30fb\uff1a:;\-—–ー_（）()\[\]「」『』]*$")


def _decision_sentences(text: str) -> List[str]:
    """Return the sentences that record a decision.

    A sentence qualifies when it carries a decision marker and has enough content
    to be readable on its own; longer sentences are truncated to the item cap so a
    single unpunctuated block cannot dominate the summary.
    """
    found: List[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        candidate = sentence.strip()
        if len(candidate) < _MIN_ITEM_CHARS or _PUNCTUATION_ONLY_RE.match(candidate):
            continue
        if _DECISION_MARKER_RE.search(candidate):
            found.append(candidate[:_MAX_ITEM_CHARS])
    return found


def _extract_items(text: str) -> dict[str, list[str]]:
    """Extract structured content lists from document text.

    Returns a dict with agenda_items, decisions and action_items, each stripped,
    deduplicated, length-capped and limited to _MAX_ITEMS_PER_TYPE entries.
    """

    def _unique_stripped(matches: List[str]) -> List[str]:
        seen = set()
        out = []
        for m in matches:
            s = m.strip()[:_MAX_ITEM_CHARS]
            if s and not _PUNCTUATION_ONLY_RE.match(s) and s not in seen:
                seen.add(s)
                out.append(s)
        return out[:_MAX_ITEMS_PER_TYPE]

    agenda = _unique_stripped([m.group(1) for m in _AGENDA_RE.finditer(text)])
    decisions = _unique_stripped(_decision_sentences(text))
    actions = _unique_stripped([m.group(1) for m in _ACTION_RE.finditer(text)])

    return {
        "agenda_items": agenda,
        "decisions": decisions,
        "action_items": actions,
    }


class ExtractDecisionsActionsNode(FunctionNode):
    """Extract agenda items, decisions, and action items from the document.

    Inner DomainWorkflowGraph node — runs after DetectDocumentTypeNode.
    Extraction uses regex pattern matching (no LLM dependency in v1).

    The result dict is JSON-serialized into state.extracted_content
    (Optional[str]) to keep State flat and msgpack-safe.

    Output (partial dict — only changed keys):
        extracted_content (JSON str), status, error_log.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        doc_text = state.get("validated_input") or state.get("user_input", "")

        if not doc_text:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ExtractDecisionsActionsNode: no document text in state"],
            }

        extracted = _extract_items(doc_text)

        # Counts only — the extracted text may carry non-public government
        # deliberation content and must never reach the audit trail.
        emit_trace_event(
            "gov_c2_004.extract_decisions_actions",
            {
                "agenda_count": len(extracted["agenda_items"]),
                "decision_count": len(extracted["decisions"]),
                "action_count": len(extracted["action_items"]),
            },
            state,
        )

        return {
            "extracted_content": json.dumps(extracted, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
