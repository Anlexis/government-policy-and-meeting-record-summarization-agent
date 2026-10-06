# PB-7: human-in-the-loop interrupt propagation.
#
# GOV-C2-004 does not use human-in-the-loop review: summarising a document is a
# single deterministic pass with no point at which a person is asked to approve
# an intermediate result. PB-7 is therefore a NEGATIVE contract here, and these
# tests pin it rather than skipping:
#
#   - no node suspends the graph, so no checkpointer is required;
#   - config/config.yaml does not enable the checkpointer, which is consistent
#     with the above — a graph that suspends without one cannot be resumed;
#   - the nested graph node does not surface an inner suspension to the caller;
#   - an ordinary invocation never returns the awaiting-human status.
#
# If a future revision introduces a review step, these tests fail and say what
# has to change with it.

import ast
import pathlib

import yaml

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config" / "config.yaml"

_DOCUMENT = (
    "令和6年度 閣議決定\n"
    "基本方針について以下のとおり決定した。\n"
    "審議の結果、原案を了承した。\n"
    "担当：デジタル庁"
)


def _hitl_enabled() -> bool:
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    hitl = data.get("hitl", {})
    return bool(hitl.get("enabled", False)) if isinstance(hitl, dict) else False


def test_config_does_not_enable_human_review():
    """The declared runtime parameters match the implementation below."""
    assert _CONFIG_PATH.exists(), "config/config.yaml must ship with the template"
    assert _hitl_enabled() is False


def test_no_node_suspends_the_graph():
    """A node calling interrupt() without a checkpointer raises at runtime, so
    the absence of both must be checked together, not assumed."""
    offenders = []
    for py_file in sorted((_REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "interrupt":
                    offenders.append(f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno}")
    assert offenders == [], "interrupt() is called but config/config.yaml enables no checkpointer: " + ", ".join(
        offenders
    )


def test_nested_graph_node_does_not_propagate_suspension():
    from src.graph.graph import PolicySummarizationGraphNode

    assert PolicySummarizationGraphNode.propagate_hitl is False


def test_invocation_completes_without_awaiting_a_human():
    from src.graph.graph import GovernmentPolicyMeetingRecordSummarizationAgent

    agent = GovernmentPolicyMeetingRecordSummarizationAgent()
    agent.compile()
    result = agent.invoke(
        _DOCUMENT,
        ctx=InvocationContext(session_id="pb7", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        input_context={"audience_mode": "internal"},
    )
    assert result["status"] != AgentStatus.AWAITING_HUMAN.value
    assert result["status"] == AgentStatus.SUCCESS.value
    assert result["output"]
