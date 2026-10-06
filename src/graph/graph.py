"""AgentCore Platform v1.0"""

# GOV-C2-004 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (PolicySummarizationGraphNode) that
#   delegates the full domain workflow to DomainWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (linear 4-node topology)
#
# Rules enforced:
#   ✅ GovernmentPolicyMeetingRecordSummarizationAgent inherits AgentBaseGraph
#   ✅ super().register_nodes() called first (fills initialize + finalize)
#   ✅ PolicySummarizationGraphNode assigned to self._nodes["main"]
#   ✅ merge_output() returns only changed keys
#   ✅ Class name matches config/agent.yaml `class:` and src/api/server.py import
#   ❌ add_edges() NOT overridden on the outer graph
#   ❌ No imports from the platform SDK — framework.* and shared.* only

from typing import Any, ClassVar

from framework.schemas.agent_status import AgentStatus
from framework.graph.agent_base_graph import AgentBaseGraph
from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from src.graph.context_bridge import publish_caller_context
from src.nodes.security_gate_output_node import SecurityGateOutputNode
from src.nodes.validate_input_node import ValidateInputNode
from src.schemas.state import State


class PolicySummarizationGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph) for the government
    policy document summarization workflow.

    The caller's validated options are published for the inner graph here — see
    src/graph/context_bridge.py for why that step is necessary.

    Contracts:
      get_subgraph()   — instantiate and return DomainWorkflowGraph
      extract_input()  — pass validated document text into inner graph.invoke()
      merge_output()   — map sub_result fields into outer state delta (changed keys only)
      error_strategy   — "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    # "propagate": re-raise inner graph exceptions as SubgraphError (default — fail fast).
    # "handle": call on_subgraph_error() instead — use for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: HITL interrupts are handled inside the inner graph only.
    # True:  surface inner HITL interrupt to the outer caller.
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> BaseGraph:
        """Instantiate and return DomainWorkflowGraph (inner graph).

        Imported lazily to avoid circular-import risk at module load time.
        No ctor args — the inner graph inherits config from the outer graph via
        the framework InvocationContext.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph()

    def execute(self, state: AgentState) -> dict[str, Any]:
        """Skip the inner graph when the request was already found unacceptable.

        A request declined by pre_process has no validated input to act on, so
        running the inner graph would only produce a second, vaguer reason for
        the same rejection - and overwrite the specific one already settled.
        """
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        result: dict[str, Any] = super().execute(state)
        return result

    def extract_input(self, state: AgentState) -> str:
        """Return the document text to pass into inner_graph.invoke().

        Also publishes the caller options that ValidateInputNode accepted, so the
        inner graph can seed them into its own initial state. The framework hands
        the subgraph only the document text, so without this step the audience the
        summary is written for would never cross the boundary — see
        src/graph/context_bridge.py.

        Only validated values travel: `state` at this point is the output of
        ValidateInputNode, which is the sole reader of raw caller input.
        """
        publish_caller_context(
            {
                "audience_mode": state.get("audience_mode"),
                "max_summary_chars": state.get("max_summary_chars"),
            }
        )
        text: str = state.get("validated_input") or state.get("user_input", "")
        return text

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output()   emits: formatted_summary, summary,
                                      document_type, status
          This merge_output() reads: sub_result.get("formatted_summary"),
                                     sub_result.get("summary"),
                                     sub_result.get("document_type"),
                                     sub_result.get("status")

        The outer post_process (SecurityGateOutputNode) reads state["formatted_summary"]
        and overwrites both formatted_output and result with the gated text.
        """
        return {
            # Outer reason wins: a reason settled before the inner run is the real
            # one, and a plain sub_result.get() would erase it.
            "error_code": state.get("error_code") or sub_result.get("error_code", ""),
            "result": sub_result.get("formatted_summary") or sub_result.get("summary"),
            "formatted_summary": sub_result.get("formatted_summary"),
            "summary": sub_result.get("summary"),
            "document_type": sub_result.get("document_type"),
            "status": sub_result.get("status"),
        }


class GovernmentPolicyMeetingRecordSummarizationAgent(AgentBaseGraph):
    """Outer graph for GOV-C2-004 (Cat 2).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in PolicySummarizationGraphNode (main slot), which delegates
    to DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START → initialize → pre_process → main → post_process → finalize → END

    register_nodes() is the ONLY override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process: ValidateInputNode (the caller contract: document + options)
      - main:        PolicySummarizationGraphNode (delegates to DomainWorkflowGraph)
      - post_process: SecurityGateOutputNode (the output gate)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "GovernmentPolicyMeetingRecordSummarizationAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = ValidateInputNode()
        self._nodes["main"] = PolicySummarizationGraphNode()
        self._nodes["post_process"] = SecurityGateOutputNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Module-level alias — server.py and agent.yaml `class:` both resolve to this name.
# The alias ensures back-compat for callers using `from src.graph.graph import Graph`.
Graph = GovernmentPolicyMeetingRecordSummarizationAgent
