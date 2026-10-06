# PB-6: Invoke Execution Order Verification
# Verifies BaseNode.__call__() enforces, for every concrete node under src/nodes/:
#   trust gate -> node_start event -> _security_gate_input() -> execute()
#   -> _security_gate_output() -> node_complete event.
# The order is the enforcement: a node whose execute() ran before the input gate,
# or whose result skipped the output gate, would be unprotected.

import importlib
import inspect
import pkgutil

import pytest

from framework.schemas.agent_status import AgentStatus

# ── GOV-C2-004 main-slot GraphNode constants ──────────────────────────────────
# PolicySummarizationGraphNode lives in src/graph/graph.py (not src/nodes/)
# so it is NOT auto-discovered by _discover_node_classes().
# It is tested explicitly by test_main_slot_node_invoke_order() below.

_MAIN_SLOT_NODE = "PolicySummarizationGraphNode"

# A SUCCESS-yielding state payload for PolicySummarizationGraphNode.
# Provides enough validated_input for the inner DomainWorkflowGraph to run.
# Kept in step with deploy/invoke_payload.json — the same document is used for
# the deployment smoke check, so a document that stops working here stops working
# there too.
_VALID_PAYLOAD = {
    "user_input": (
        "令和6年度 閣議決定\n"
        "デジタル社会推進基本方針について以下のとおり決定した。\n"
        "一、行政DX推進\n二、市民サービス向上\n"
        "審議の結果、原案を了承した。\n"
        "担当：デジタル庁"
    ),
    "validated_input": (
        "令和6年度 閣議決定\n"
        "デジタル社会推進基本方針について以下のとおり決定した。\n"
        "一、行政DX推進\n二、市民サービス向上\n"
        "審議の結果、原案を了承した。\n"
        "担当：デジタル庁"
    ),
    "audience_mode": "internal",
    "node_history": [],
    "error_log": [],
    "correlation_id": "pb6-invoke-order-test",
}
# ─────────────────────────────────────────────────────────────────────────────


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete BaseNode subclasses."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


class TestInvokeOrder:
    """PB-6: __call__ runs the gates around execute(), in order, every time."""

    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes/")

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            # VERIFIED_EXTERNAL is the real external-caller path, and the highest
            # trust level any node here requires, so every node's trust gate passes
            # and the ordering assertion is what actually gets exercised.
            state = {
                "caller_trust_level": "VERIFIED_EXTERNAL",
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)

    def test_main_slot_node_invoke_order(self, monkeypatch):
        """PB-6 explicit: verify invoke order for the main-slot GraphNode.

        PolicySummarizationGraphNode (GraphNode subclass) lives in src/graph/graph.py,
        not in src/nodes/, so it is NOT discovered by _discover_node_classes().

        execute() is replaced with a mock (not called through) because GraphNode.execute()
        invokes the inner DomainWorkflowGraph which requires framework infrastructure not
        needed for the hook-order assertion. PB-6 tests that BaseNode.__call__()
        enforces the hook sequence; the inner graph running for real is covered by
        the end-to-end tests under tests/integration/.
        """
        from src.graph.graph import PolicySummarizationGraphNode
        import framework.nodes.base_node as base_node_module
        from framework.schemas.trust_level import TrustLevel

        node_cls = PolicySummarizationGraphNode
        order: list[str] = []

        monkeypatch.setattr(
            base_node_module,
            "emit_trace_event",
            lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
        )

        # Replace execute() with a mock that records the call but does NOT invoke the
        # inner DomainWorkflowGraph.  This isolates the hook-order test from inner-graph
        # execution; the mock returns a valid partial-state dict so that _security_gate_output
        # receives a non-empty merged state.
        _order_ref = order  # explicit cell reference for the closure

        def _mock_execute(self, state):  # noqa: N802
            _order_ref.append("execute")
            return {
                "formatted_summary": "【内部配布用】テスト要約",
                "summary": "テスト要約",
                "document_type": "policy",
                "status": AgentStatus.SUCCESS.value,
            }

        monkeypatch.setattr(node_cls, "execute", _mock_execute)

        # Spy on the two gate hooks (call-through so they run their real logic)
        for method_name, label in (
            ("_security_gate_input", "security_gate_input"),
            ("_security_gate_output", "security_gate_output"),
        ):
            original = getattr(node_cls, method_name)

            def spy(self, arg, _o=order, _label=label, _orig=original):
                _o.append(_label)
                return _orig(self, arg)

            monkeypatch.setattr(node_cls, method_name, spy)

        instance = node_cls()
        state = dict(_VALID_PAYLOAD)
        # The real external-caller path; this node inherits the default trust
        # requirement, which VERIFIED_EXTERNAL satisfies.
        state["caller_trust_level"] = TrustLevel.VERIFIED_EXTERNAL.value

        instance(state)

        expected = [
            "event:node_start",
            "security_gate_input",
            "execute",
            "security_gate_output",
            "event:node_complete",
        ]
        assert order == expected, (
            f"{_MAIN_SLOT_NODE}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
        )
