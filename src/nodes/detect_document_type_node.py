"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status as AgentStatus.<X>.value — the enum's string value,
#    not the enum object itself
#  - Never import from mediator/, api/, or other agents
#
# GOV-C2-004 — DetectDocumentTypeNode (inner DomainWorkflowGraph node 1).
# Classifies the government document type using a Japanese keyword taxonomy.
# Types: policy (閣議決定/政策文書), minutes (審議会議事録), consultation
# (パブリックコメント), other.
# Emits an audit event carrying the detected type.

import logging
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# ── Document type keyword patterns (Japanese government taxonomy) ─────────────

_POLICY_KEYWORDS = [
    r"閣議決定",
    r"閣議了解",
    r"閣議案件",
    r"基本方針",
    r"行動計画",
    r"大綱",
    r"基本計画",
    r"政策",
    r"施策",
    r"規制",
    r"告示",
    r"通達",
    r"通知",
    r"法律案",
    r"予算案",
    r"条例",
]

_MINUTES_KEYWORDS = [
    r"議事録",
    r"議事要旨",
    r"会議録",
    r"審議会",
    r"委員会",
    r"検討会",
    r"協議会",
    r"議事",
    r"議事次第",
    r"出席者",
    r"欠席者",
    r"議長",
    r"副議長",
    r"幹事",
]

_CONSULTATION_KEYWORDS = [
    r"パブリックコメント",
    r"パブコメ",
    r"意見公募",
    r"意見募集",
    r"意見提出",
    r"市民参加",
    r"公聴会",
    r"意見書",
    r"国民の声",
    r"市民の意見",
]

_POLICY_RE = re.compile("|".join(_POLICY_KEYWORDS))
_MINUTES_RE = re.compile("|".join(_MINUTES_KEYWORDS))
_CONSULTATION_RE = re.compile("|".join(_CONSULTATION_KEYWORDS))


def _classify_document(text: str) -> str:
    """Classify document type by keyword frequency scoring.

    Returns the type with the highest match count.
    Falls back to "other" when no known type keywords are found.
    """
    policy_count = len(_POLICY_RE.findall(text))
    minutes_count = len(_MINUTES_RE.findall(text))
    consultation_count = len(_CONSULTATION_RE.findall(text))

    if policy_count == 0 and minutes_count == 0 and consultation_count == 0:
        return "other"

    scores = {
        "policy": policy_count,
        "minutes": minutes_count,
        "consultation": consultation_count,
    }
    return max(scores, key=lambda k: scores[k])


class DetectDocumentTypeNode(FunctionNode):
    """Classify the government document type.

    Inner DomainWorkflowGraph node — runs inside PolicySummarizationGraphNode.
    Uses keyword frequency scoring over the validated document text.

    Output (partial dict — only changed keys):
        document_type, out_of_scope, status, error_log.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        doc_text = state.get("validated_input") or state.get("user_input", "")

        if not doc_text:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "DetectDocumentTypeNode: no document text found in state "
                    "(validated_input and user_input are both empty)"
                ],
            }

        document_type = _classify_document(doc_text)
        out_of_scope = document_type == "other"

        emit_trace_event(
            "gov_c2_004.detect_document_type",
            {
                "document_type": document_type,
                "out_of_scope": out_of_scope,
                "doc_length": len(doc_text),
            },
            state,
        )

        return {
            "document_type": document_type,
            "out_of_scope": out_of_scope,
            "status": AgentStatus.SUCCESS.value,
        }
