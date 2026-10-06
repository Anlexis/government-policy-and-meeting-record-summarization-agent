"""AgentCore Platform v1.0"""

# GOV-C2-004 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full government document summarization domain workflow:
#
#   START → detect_document_type → extract_decisions_actions
#         → summarize → format_for_public_transparency → END
#
# Called by PolicySummarizationGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   ✅ Inherits BaseGraph (fully custom topology — no forced backbone)
#   ✅ Implements all 7 BaseGraph ABC methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Does NOT register initialize / finalize (outer backbone concerns)
#   ✅ All domain nodes instantiated with NO ctor args (SDK-v1 no-arg rule)
#   ✅ get_output() designed together with PolicySummarizationGraphNode.merge_output()
#   ❌ Not placed under src/subagents/
#   ❌ No imports from the platform SDK — framework.* and shared.* only

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import read_caller_context
from src.nodes.detect_document_type_node import DetectDocumentTypeNode
from src.nodes.extract_decisions_actions_node import ExtractDecisionsActionsNode
from src.nodes.format_for_public_transparency_node import FormatForPublicTransparencyNode
from src.nodes.summarize_node import SummarizeNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for GOV-C2-004.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by PolicySummarizationGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          → detect_document_type     (DetectDocumentTypeNode)
          → extract_decisions_actions (ExtractDecisionsActionsNode)
          → summarize                (SummarizeNode)
          → format_for_public_transparency (FormatForPublicTransparencyNode)
          → END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns — not registered here.

    Caller options reach this graph through src/graph/context_bridge.py and are
    seeded into the initial state by _extra_initial_state():
      audience_mode: str     — the audience the summary is written for
      max_summary_chars: int — maximum summary length
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "gov_c2_004_domain_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        This graph has no mandatory configuration: every option it honours is a
        per-request caller option, validated by ValidateInputNode and seeded by
        _extra_initial_state() below.
        """

    def _extra_initial_state(self) -> dict[str, Any]:
        """Seed the validated caller options into the inner graph's initial state.

        The framework's GraphNode invokes a subgraph with the document text only,
        so anything else the caller asked for has to be carried across the
        boundary explicitly (src/graph/context_bridge.py). Values published there
        have already passed ValidateInputNode; an empty bridge (this graph invoked
        on its own) leaves the keys absent and each node applies its own default.
        """
        return {k: v for k, v in read_caller_context().items() if v is not None}

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 4 domain nodes with NO ctor args (SDK-v1 rule).

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().
        """
        self._nodes["detect_document_type"] = DetectDocumentTypeNode()
        self._nodes["extract_decisions_actions"] = ExtractDecisionsActionsNode()
        self._nodes["summarize"] = SummarizeNode()
        self._nodes["format_for_public_transparency"] = FormatForPublicTransparencyNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear government document summarization domain topology.

        Flow:
          detect_document_type
            → extract_decisions_actions
            → summarize
            → format_for_public_transparency
            → END
        """
        self._sg.add_edge(START, "detect_document_type")
        self._sg.add_edge("detect_document_type", "extract_decisions_actions")
        self._sg.add_edge("extract_decisions_actions", "summarize")
        self._sg.add_edge("summarize", "format_for_public_transparency")
        self._sg.add_edge("format_for_public_transparency", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        For this linear topology add_conditional_edges() is not used, so this
        method is never called at runtime. Returns END on ERROR to ensure a
        stray conditional-edge call does not re-enter a processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "format_for_public_transparency"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        Received by PolicySummarizationGraphNode.merge_output() in graph.py
        as the sub_result argument.  Field-name coupling (designed together):

            Inner get_output()  emits: formatted_summary, summary,
                                       document_type, status,
                                       trace_id, correlation_id, node_history
            Outer merge_output() reads: sub_result.get("formatted_summary"),
                                        sub_result.get("summary"),
                                        sub_result.get("document_type"),
                                        sub_result.get("status")

        Additional fields (trace_id, correlation_id, node_history) are
        surfaced for observability / downstream extension.
        """
        return {
            # the reason must leave the subgraph or the outer graph cannot report it
            "error_code": state.get("error_code"),
            "formatted_summary": state.get("formatted_summary"),
            "summary": state.get("summary"),
            "document_type": state.get("document_type"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
