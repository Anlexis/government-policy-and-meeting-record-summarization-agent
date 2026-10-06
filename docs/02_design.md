# docs/02_design.md — GOV-C2-004 Government Policy & Meeting Record Summarization Agent

## Template Metadata

| Field | Value |
|---|---|
| Template ID | GOV-C2-004 |
| Category | Cat 2 |
| Industry | GOV (Government) |
| L1 Base (framework base class) | AgentBaseGraph — direct framework inheritance |
| Pattern | Document summarization pipeline |
| Generation mode | deterministic (no model call) |

## Architecture Overview

GOV-C2-004 uses the Cat 2 two-layer nested architecture:

- **Outer graph** (`src/graph/graph.py`): `GovernmentPolicyMeetingRecordSummarizationAgent(AgentBaseGraph)` — the fixed 5-slot backbone, with the domain workflow encapsulated in the `main` slot.
- **Inner graph** (`src/graph/domain_workflow_graph.py`): `DomainWorkflowGraph(BaseGraph)` — a linear 4-node domain workflow.

```
Outer backbone (fixed):
  START → initialize → pre_process → main → post_process → finalize → END

Inner domain workflow (linear):
  detect_document_type → extract_decisions_actions → summarize → format_for_public_transparency
```

## Node Responsibilities

### Outer Backbone Nodes

| Slot | Class | File | Responsibility |
|------|-------|------|----------------|
| initialize | InitializeNode (default) | framework | Sets schema_version, session_id, trust level |
| pre_process | ValidateInputNode | validate_input_node.py | The caller contract: document text and request options |
| main | PolicySummarizationGraphNode (GraphNode) | graph.py | Delegates to DomainWorkflowGraph via get_subgraph() |
| post_process | SecurityGateOutputNode | security_gate_output_node.py | The output gate: restricted-content and audience rules |
| finalize | FinalizeNode (default) | framework | Builds response_metadata, total_time_ms |

### Inner Domain Nodes

| Node Key | Class | File | Responsibility |
|----------|-------|------|----------------|
| detect_document_type | DetectDocumentTypeNode | detect_document_type_node.py | Classifies the document via a Japanese keyword taxonomy |
| extract_decisions_actions | ExtractDecisionsActionsNode | extract_decisions_actions_node.py | Extracts agenda items, decisions, action items |
| summarize | SummarizeNode | summarize_node.py | Builds the structured Markdown summary |
| format_for_public_transparency | FormatForPublicTransparencyNode | format_for_public_transparency_node.py | Renders for the internal or public audience |

## The caller contract

`POST /invoke` accepts the document as `input` and the request options alongside it.
The adapter forwards the options verbatim; `ValidateInputNode` is the only place that
decides what is acceptable, so a given mistake produces the same error however the agent
was invoked.

| Option | Accepted values | Absent |
|---|---|---|
| `audience_mode` | `internal` \| `public` (case-insensitive) | `internal` |
| `max_summary_chars` | a finite number in [200, 20000] | 3000 |
| `channel` | an inert identifier `[a-z0-9_]{1,32}` | `unknown` |

Rules that apply to all of them:

- **Fail closed.** An unrecognised `audience_mode` is refused, not defaulted — defaulting
  would answer a request for a redacted summary with an unredacted one.
- **Numbers must be finite.** `NaN` and `±Infinity` survive `float()` and then compare
  `False` against every bound, so an unchecked length cap silently stops capping. Both
  the string forms and the bare JSON literals are rejected.
- **No option carries free text.** Each one is an enumerated value, a bounded number or an
  inert identifier, so there is nothing on the option channel for a sanitizer to have to
  clean — the framework's own masking covers only the document field, and this is what
  closes the gap. `channel` in particular is recorded in the audit trail, so free text
  there would be caller-controlled log content.
- **Rejected values are never echoed.** An error names the field, never the input.
- **Structural limits.** The options mapping is capped by key count; options the template
  does not implement are ignored and can never influence behaviour.
- **The document channel refuses instruction-override content** — enforced in the node
  that owns the contract, not delegated to the surrounding runtime, and proven by calling
  `execute()` directly in `tests/unit/test_caller_contract.py`.

## Carrying caller options into the inner graph

The framework's `GraphNode` invokes a subgraph with the document text only — the caller's
options are not forwarded. Without a bridge, the inner graph would start with empty
options and a caller asking for a redacted public summary would silently receive the full
internal one.

`src/graph/context_bridge.py` closes that gap with a ContextVar:

```
outer PolicySummarizationGraphNode.extract_input()  → publish_caller_context()
inner DomainWorkflowGraph._extra_initial_state()    → read_caller_context()
```

Both run inside one node invocation, so each concurrent request sees its own values. Only
values that already passed `ValidateInputNode` are published. The end-to-end tests assert
the effect, not the mechanism: the same document returns different output for the two
audiences.

## State Schema (`src/schemas/state.py`)

A flat TypedDict extending AgentState — no Pydantic, no nested objects, because LangGraph
checkpoints State with msgpack.

| Field | Type | Written by | Read by | Notes |
|-------|------|------------|---------|-------|
| validated_input | Optional[str] | ValidateInputNode | DetectDocumentTypeNode, ExtractDecisionsActionsNode, SummarizeNode | Sanitized document text |
| audience_mode | Optional[str] | ValidateInputNode | FormatForPublicTransparencyNode, SecurityGateOutputNode | "internal" \| "public" |
| max_summary_chars | Optional[int] | ValidateInputNode | SummarizeNode | Validated length cap |
| request_channel | Optional[str] | ValidateInputNode | — | Inert channel label for the audit trail |
| document_type | Optional[str] | DetectDocumentTypeNode | SummarizeNode, FormatForPublicTransparencyNode, SecurityGateOutputNode | "policy" \| "minutes" \| "consultation" \| "other" |
| out_of_scope | bool | DetectDocumentTypeNode | — | True when document_type = "other" |
| extracted_content | Optional[str] | ExtractDecisionsActionsNode | SummarizeNode | JSON string: {agenda_items, decisions, action_items} |
| summary | Optional[str] | SummarizeNode | FormatForPublicTransparencyNode | Structured Markdown summary |
| formatted_summary | Optional[str] | FormatForPublicTransparencyNode | SecurityGateOutputNode, merge_output() | Audience-formatted summary |
| formatted_output | Any (inherited) | SecurityGateOutputNode | the caller | The gated text — what the caller receives |
| status | Optional[str] | All nodes | route(), merge_output() | AgentStatus string value |
| trace_id | Optional[str] | InitializeNode | audit events | Audit log correlation |

## Data Flow

```
user_input (str — raw government document) + request options
    ↓
ValidateInputNode
    validated_input, audience_mode, max_summary_chars, request_channel
    ↓
PolicySummarizationGraphNode.extract_input()
    publishes the validated options on the caller-context bridge
    → inner_graph.invoke(validated_input)
        ↓  _extra_initial_state() seeds the options
        DetectDocumentTypeNode
            document_type = keyword_classify(validated_input)
            ↓
        ExtractDecisionsActionsNode
            extracted_content = json.dumps({agenda_items, decisions, action_items})
            ↓
        SummarizeNode
            summary = build_summary(extracted_content, document_type, max_summary_chars)
            ↓
        FormatForPublicTransparencyNode
            formatted_summary = format(summary, audience_mode)
    ← inner_graph.get_output()
    ↓
PolicySummarizationGraphNode.merge_output()
    ↓
SecurityGateOutputNode
    formatted_output = result = gate(formatted_summary, audience_mode)
```

## The output boundary

`SecurityGateOutputNode` is the last thing to touch the text. It enforces the promise the
template makes, rather than assuming the upstream nodes kept it.

1. **Restricted material** — a line carrying a classified-material marker
   (機密情報, 特定秘密, 防衛秘密, 外交上の秘密, 内閣情報(調査)室, 未公表…個人情報) is replaced
   **in full**, for either audience. Replacing only the marker word would leave the
   sentence that carries the content, which is the verbatim re-emission the gate exists to
   prevent.
2. **Non-disclosable material, public audience** — 情報公開法 Article 5 categories are
   re-applied here even though `FormatForPublicTransparencyNode` already applied them, so
   the guarantee does not depend on an upstream node having run. Two independent layers,
   each with its own marker.
3. **Length cap** — 10,000 characters, applied at a line boundary so a redaction marker is
   never split in half, and applied *after* redaction. The result is re-scanned; the
   markers deliberately contain none of the words either scan looks for, so a redacted
   line cannot be re-labelled by the other layer and the re-scan is a genuine no-op on
   clean text.
4. **Both output fields carry the gated text.** The framework surfaces
   `formatted_output or result`, so leaving the pre-gate text in `result` would hand a
   caller a route past the boundary.

**No numeric rounding grid.** This template reproduces structured excerpts of a document;
it computes and renders no monetary aggregates, so there is nothing to round and it
rewrites no number. Document numbers, article references, fiscal years and figures quoted
from the source pass through byte-identical. `tests/proof_of_boundary/test_output_boundary.py`
pins both directions: every restricted form is removed, and ordinary lines carrying
identifiers, amounts and dates are returned unchanged.

## Audit trail

All nodes emit `emit_trace_event(event, payload, state)` via the free function imported
from `shared.utils.audit_logger`. Payloads contain metadata only — counts, type labels,
lengths, the gate decision — never document content, extracted text or the output. The
extraction node in particular records counts alone, since the extracted text can carry
non-public deliberation content.

## Compliance notes

- **情報公開法 Article 5** — the public audience output; the formatting node and the output
  gate apply the rule independently.
- **特定秘密保護法** — the restricted-content rule at the output boundary.
- **公文書管理法 / 行政文書管理ガイドライン** — every summarization action is recorded in the
  audit trail.

## Runtime parameters

`config/agent.yaml` is the static manifest read by the registry; `config/config.yaml`
holds the runtime parameters and is passed to the graph constructor by `src/api/server.py`.

| File | Key | Effect |
|---|---|---|
| config/config.yaml | `max_retry` | Backbone retry limit, validated by the framework at compile time |
| config/config.yaml | `timeout_s` | Per-request budget applied by the HTTP entry point |

## Import Isolation

- Framework imports are `framework.*` only; shared utilities are
  `shared.utils.audit_logger.emit_trace_event` and `shared.secrets.factory`.
- No imports from the platform SDK underneath the framework.
- No imports from `mediator/`, from `api/` inside nodes, or from other templates.

## Test Coverage

| Area | File |
|---|---|
| Domain nodes and graph compilation | `tests/unit/test_agent.py` |
| The caller contract, field by field | `tests/unit/test_caller_contract.py` |
| Framework gate non-bypassability | `tests/unit/test_framework_compliance_tc06_tc07.py` |
| End-to-end through the HTTP entry point | `tests/integration/test_invoke_e2e.py` |
| Output boundary, both directions | `tests/proof_of_boundary/test_output_boundary.py` |
| Import isolation, invoke order, state safety, HITL contract | `tests/proof_of_boundary/` |
