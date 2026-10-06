# docs/03_test_spec.md — GOV-C2-004 Government Policy & Meeting Record Summarization Agent

## Test Scope

The suite covers four layers: the caller contract, the domain nodes, the output boundary,
and the whole agent driven through its HTTP entry point.

| File | Layer |
|---|---|
| `tests/unit/test_caller_contract.py` | Every value a caller can send, checked by calling `execute()` directly |
| `tests/unit/test_agent.py` | Domain nodes and graph compilation |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | The framework gates cannot be replaced by a subclass |
| `tests/proof_of_boundary/test_output_boundary.py` | The output promise, in both directions |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | Gate ordering around `execute()` |
| `tests/proof_of_boundary/test_import_isolation.py` | Import isolation |
| `tests/proof_of_boundary/test_state_safety.py` | State is msgpack-safe and carries no credentials |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | The non-HITL contract |
| `tests/integration/test_invoke_e2e.py` | End to end through `POST /invoke` |

## TC-001 — The caller contract (`test_caller_contract.py`)

The node is called directly, with no framework wrapper in front of it: a guarantee that
holds only because the surrounding runtime happens to enforce it would fail here.

| ID | Input | Expected |
|----|-------|----------|
| TC-001-1 | valid document | SUCCESS; `validated_input` is the stripped text |
| TC-001-2 | `""`, whitespace only | ERROR naming `user_input`; no `validated_input` |
| TC-001-3 | shorter than 10 characters | ERROR naming the minimum |
| TC-001-4 | longer than 500,000 characters | ERROR naming the maximum |
| TC-001-5 | no options supplied | audience `internal`, cap 3000, channel `unknown` |
| TC-001-6 | `max_summary_chars` ∈ {`"NaN"`, `"Infinity"`, `"-Infinity"`, `float("nan")`, `float("inf")`, `float("-inf")`, …} | ERROR naming the field (parametrized matrix) |
| TC-001-7 | `max_summary_chars` out of range (0, −1, 199, 20001, 10⁹) | ERROR naming the field |
| TC-001-8 | `max_summary_chars` non-numeric (`True`, `None`, `[]`, `"abc"`) | ERROR naming the field |
| TC-001-9 | `max_summary_chars` in range, incl. `"1500"` and `1500.9` | SUCCESS; value is an int |
| TC-001-10 | `audience_mode` unrecognised (`"publik"`, `7`, `""`, …) | ERROR — refused, **not** defaulted |
| TC-001-11 | `audience_mode` recognised, any case or padding | SUCCESS; normalised |
| TC-001-12 | `channel` free text, too long, uppercase, non-string | ERROR naming the field |
| TC-001-13 | `channel` inert identifier | SUCCESS; recorded verbatim |
| TC-001-14 | any rejected value | the value never appears in `error_log` |
| TC-001-15 | more than 32 options; options not a mapping | ERROR naming `input_context` |
| TC-001-16 | an option the template does not implement | ignored; behaviour unchanged |
| TC-001-17 | documents carrying instruction-override content (8 forms, EN + JA) | ERROR; no `validated_input` |
| TC-001-18 | ordinary documents using 指示 / "system" in normal prose (5 forms) | SUCCESS — the vocabulary is not what is refused |

## TC-002 — DetectDocumentTypeNode

| ID | Input | Expected |
|----|-------|----------|
| TC-002-1 | 閣議決定 / 基本方針 text | `document_type` = "policy", `out_of_scope` = False |
| TC-002-2 | 審議会 / 議事録 text | `document_type` = "minutes" |
| TC-002-3 | パブリックコメント text | `document_type` = "consultation" |
| TC-002-4 | text with no taxonomy keyword | `document_type` = "other", `out_of_scope` = True |
| TC-002-5 | empty text | ERROR |
| TC-002-6 | policy document | audit event carries type and scope flag |

## TC-003 — ExtractDecisionsActionsNode

| ID | Input | Expected |
|----|-------|----------|
| TC-003-1 | any valid text | `extracted_content` parses as JSON with the three lists |
| TC-003-2 | "……を決定した。" | the **whole sentence** is the decision item |
| TC-003-3 | any valid text | no item is punctuation-only and no item spans a line break |
| TC-003-4 | "担当：デジタル庁" | the owner is an action item |
| TC-003-5 | 50 decision sentences | at most 20 items retained |
| TC-003-6 | empty text | ERROR |
| TC-003-7 | policy document | audit payload is counts only; no extracted text |

## TC-004 — SummarizeNode

| ID | Input | Expected |
|----|-------|----------|
| TC-004-1 | extracted content + `document_type` = "policy" | summary carries the 政策文書 header and the decision text |
| TC-004-2 | `document_type` = "minutes" | summary carries the 会議議事録 header |
| TC-004-3 | nothing extracted | summary falls back to a document excerpt |
| TC-004-4 | `max_summary_chars` = 50 **in state** | summary ≤ 51 characters |
| TC-004-5 | no cap in state | the documented default (3000) applies |
| TC-004-6 | no content at all | ERROR |

## TC-005 — FormatForPublicTransparencyNode

| ID | Input | Expected |
|----|-------|----------|
| TC-005-1 | audience `internal` | 【内部配布用】 header |
| TC-005-2 | audience `public` | 【公開用】 header |
| TC-005-3 | audience `public`, summary carries 非開示情報 markers | the line is replaced; other lines survive |
| TC-005-4 | audience `internal`, same summary | the detail is retained |
| TC-005-5 | empty summary | ERROR |

## TC-006 — The output boundary (`test_output_boundary.py`)

| ID | Input | Expected |
|----|-------|----------|
| TC-006-1 | each of 6 restricted-marker lines × both audiences | the whole line is removed, with the identifying detail inside it; surrounding lines survive |
| TC-006-2 | a restricted marker word alone | removed; gate reports triggered |
| TC-006-3 | each of 5 非開示情報 lines, audience `public` | removed at the boundary even though the formatting node applies the same rule |
| TC-006-4 | the same lines, audience `internal` | retained — the internal briefing is entitled to them |
| TC-006-5 | each redaction marker, both audiences | inert to both scans; output byte-identical, gate not triggered |
| TC-006-6 | gate applied twice | idempotent |
| TC-006-7 | restricted line beyond the 10,000-character cap | never reappears; length stays within the cap |
| TC-006-8 | 1,200-line document | truncation lands on a line boundary and ends with the notice |
| TC-006-9 | 8 ordinary lines (amounts, `GOV-2026-004`, 令和6年度, 情報公開法第5条, `90d`, `JPY 1,000`, `STAR 2026`, phone numbers) × both audiences | **byte-identical**, gate not triggered |
| TC-006-10 | pre-gate text present in `result` | both `formatted_output` and `result` carry the gated text |
| TC-006-11 | no output text at all | ERROR; no `formatted_output` |
| TC-006-12 | clean and restricted text | audit payload records the decision and length, never the text |

## TC-007 — End to end (`test_invoke_e2e.py`)

Driven against the shipped ASGI app with the real compiled graph.

| ID | Request | Expected |
|----|---------|----------|
| TC-007-1 | `GET /health` | 200, `status` = "ok" |
| TC-007-2 | no bearer, empty bearer, wrong bearer | `status` = "error", no output |
| TC-007-3 | valid bearer | `status` = "success"; output derived from **this** document, not a fixed baseline |
| TC-007-4 | audience `internal` vs `public` | different rendered output; the caller's choice reaches the inner graph |
| TC-007-5 | audience `public` | 非開示情報 content removed, marker present |
| TC-007-6 | either audience | restricted content never present in the response |
| TC-007-7 | `max_summary_chars` = 200 | shorter output than the default |
| TC-007-8 | `1,234,567円`, `GOV-2026-004`, `令和6年度` | present unchanged in the response |
| TC-007-9 | 9 invalid requests (audience, non-finite and out-of-range cap, free-text channel, short/empty document) | `status` = "error", no output |
| TC-007-10 | bare `NaN` in the raw JSON body | refused — the wire form is reachable without client cooperation |
| TC-007-11 | instruction-override document | refused; nothing derived from it is published |

## Proof-of-Boundary Tests

### PB-2 / PB-5 (`test_state_safety.py`)
State carries no credential-shaped field names and no types that fail msgpack round-trip.

### PB-4 (`test_import_isolation.py`)
No module under `src/` imports the platform SDK directly.

### PB-6 (`test_pb_invoke_order.py`)
Every concrete node under `src/nodes/`, plus the main-slot GraphNode, runs
`node_start → input gate → execute() → output gate → node_complete` in that order.
`_VALID_PAYLOAD` is kept in step with `deploy/invoke_payload.json`.

### PB-7 (`test_pb7_hitl_interrupt_propagation.py`)
This template has no human-review step, so PB-7 is a negative contract: the runtime
parameters enable no checkpointer, no node suspends the graph, the nested graph node does
not surface an inner suspension, and an ordinary invocation never returns the
awaiting-human status.

## Graph Compile

`GovernmentPolicyMeetingRecordSummarizationAgent().compile()` must not raise, both with
framework defaults and with the shipped `config/config.yaml`.
