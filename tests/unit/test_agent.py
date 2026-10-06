"""GOV-C2-004 — unit tests for the domain nodes and graph compilation.

The autouse fixture patches emit_trace_event at each NODE MODULE, never by
stubbing `shared.*` in sys.modules: the framework imports shared.security at load
time, so replacing the package would break the import it needs.
"""

import importlib
import json
import pathlib
from unittest.mock import MagicMock

import pytest

from framework.schemas.agent_status import AgentStatus

_AUDIT_MODULES = [
    "src.nodes.validate_input_node",
    "src.nodes.detect_document_type_node",
    "src.nodes.extract_decisions_actions_node",
    "src.nodes.summarize_node",
    "src.nodes.format_for_public_transparency_node",
    "src.nodes.security_gate_output_node",
]


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch):
    """Silence emit_trace_event across all domain node modules."""
    for mod_name in _AUDIT_MODULES:
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, "emit_trace_event", lambda *a, **kw: None)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_state(**kwargs):
    base = {
        "node_history": [],
        "error_log": [],
        "correlation_id": "unit-test",
    }
    base.update(kwargs)
    return base


_POLICY_DOC = (
    "令和6年度\n"
    "閣議決定\n"
    "基本方針について以下のとおり決定した。\n"
    "一、デジタル化推進\n"
    "二、行政効率化\n"
    "担当：デジタル庁\n"
    "審議の結果、原案を了承した。"
)

_MINUTES_DOC = (
    "令和6年第3回 審議会\n"
    "議事録\n"
    "出席者：委員長 田中、副委員長 鈴木\n"
    "議事次第：1. 開会  2. 議題審議  3. 閉会\n"
    "議題：予算配分について議事を進めた。"
)

_CONSULTATION_DOC = (
    "パブリックコメント 意見募集\n" "標記案件について、広く意見を公募します。\n" "意見提出期限：令和6年9月30日"
)


# ── ValidateInputNode ─────────────────────────────────────────────────────────


class TestValidateInputNode:
    """The caller contract: document text plus the request options."""

    @pytest.fixture(autouse=True)
    def node(self):
        from src.nodes.validate_input_node import ValidateInputNode

        self.node = ValidateInputNode()

    def test_valid_input_success(self):
        result = self.node.execute(_make_state(user_input=_POLICY_DOC))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] == _POLICY_DOC.strip()

    def test_empty_input_refused(self):
        result = self.node.execute(_make_state(user_input=""))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert any("user_input" in msg for msg in result["error_log"])

    def test_whitespace_only_refused(self):
        result = self.node.execute(_make_state(user_input="   \n\t"))
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_too_short_refused(self):
        result = self.node.execute(_make_state(user_input="短"))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert any("minimum" in msg for msg in result["error_log"])

    def test_too_long_refused(self):
        result = self.node.execute(_make_state(user_input="あ" * 600_000))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert any("maximum" in msg for msg in result["error_log"])

    def test_defaults_when_no_options_supplied(self):
        result = self.node.execute(_make_state(user_input=_POLICY_DOC, input_context={}))
        assert result["audience_mode"] == "internal"
        assert result["max_summary_chars"] == 3_000
        assert result["request_channel"] == "unknown"

    def test_public_audience_accepted(self):
        result = self.node.execute(_make_state(user_input=_POLICY_DOC, input_context={"audience_mode": "PUBLIC"}))
        assert result["audience_mode"] == "public"

    def test_audit_event_carries_metadata_only(self, monkeypatch):
        mod = importlib.import_module("src.nodes.validate_input_node")
        spy = MagicMock()
        monkeypatch.setattr(mod, "emit_trace_event", spy)

        self.node.execute(_make_state(user_input=_POLICY_DOC, input_context={}))

        assert spy.called
        assert spy.call_args[0][0] == "gov_c2_004.validate_input"
        payload = spy.call_args[0][1]
        assert set(payload) == {
            "doc_length",
            "audience_mode",
            "max_summary_chars",
            "channel",
            "ignored_options",
        }
        assert "閣議決定" not in repr(payload)


# ── DetectDocumentTypeNode ────────────────────────────────────────────────────


class TestDetectDocumentTypeNode:
    """Japanese government document type classification."""

    @pytest.fixture(autouse=True)
    def node(self):
        from src.nodes.detect_document_type_node import DetectDocumentTypeNode

        self.node = DetectDocumentTypeNode()

    def test_policy_document_detected(self):
        result = self.node.execute(_make_state(validated_input=_POLICY_DOC))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["document_type"] == "policy"
        assert result["out_of_scope"] is False

    def test_minutes_document_detected(self):
        result = self.node.execute(_make_state(validated_input=_MINUTES_DOC))
        assert result["document_type"] == "minutes"
        assert result["out_of_scope"] is False

    def test_consultation_document_detected(self):
        result = self.node.execute(_make_state(validated_input=_CONSULTATION_DOC))
        assert result["document_type"] == "consultation"

    def test_unknown_document_is_out_of_scope(self):
        result = self.node.execute(_make_state(validated_input="Hello world this is a general document"))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["document_type"] == "other"
        assert result["out_of_scope"] is True

    def test_empty_input_error(self):
        result = self.node.execute(_make_state(validated_input=""))
        assert result["status"] == AgentStatus.ERROR.value

    def test_audit_event_emitted(self, monkeypatch):
        mod = importlib.import_module("src.nodes.detect_document_type_node")
        spy = MagicMock()
        monkeypatch.setattr(mod, "emit_trace_event", spy)

        self.node.execute(_make_state(validated_input=_POLICY_DOC))

        assert spy.call_args[0][0] == "gov_c2_004.detect_document_type"
        payload = spy.call_args[0][1]
        assert "document_type" in payload
        assert "out_of_scope" in payload


# ── ExtractDecisionsActionsNode ───────────────────────────────────────────────


class TestExtractDecisionsActionsNode:
    """Extraction of agenda items, decisions and action items."""

    @pytest.fixture(autouse=True)
    def node(self):
        from src.nodes.extract_decisions_actions_node import ExtractDecisionsActionsNode

        self.node = ExtractDecisionsActionsNode()

    def test_result_is_json_string(self):
        result = self.node.execute(_make_state(validated_input=_POLICY_DOC))
        assert result["status"] == AgentStatus.SUCCESS.value
        extracted = json.loads(result["extracted_content"])
        assert set(extracted) == {"agenda_items", "decisions", "action_items"}

    def test_decision_sentence_is_captured_whole(self):
        doc = "審議の結果、デジタル化推進計画を決定した。本計画は速やかに実施する。"
        result = self.node.execute(_make_state(validated_input=doc))
        decisions = json.loads(result["extracted_content"])["decisions"]
        assert "審議の結果、デジタル化推進計画を決定した" in decisions

    def test_punctuation_is_never_extracted_as_an_item(self):
        """A sentence splitter leaves punctuation behind; it is not an item."""
        result = self.node.execute(_make_state(validated_input=_POLICY_DOC))
        extracted = json.loads(result["extracted_content"])
        for items in extracted.values():
            for item in items:
                assert item.strip("。、 \n"), f"punctuation-only item extracted: {item!r}"
                assert "\n" not in item, f"item spans a line break: {item!r}"

    def test_action_owner_is_captured(self):
        result = self.node.execute(_make_state(validated_input=_POLICY_DOC))
        actions = json.loads(result["extracted_content"])["action_items"]
        assert "デジタル庁" in actions

    def test_items_are_capped(self):
        doc = "".join(f"{i}番目の事項を決定した。\n" for i in range(50))
        result = self.node.execute(_make_state(validated_input=doc))
        assert len(json.loads(result["extracted_content"])["decisions"]) <= 20

    def test_empty_input_error(self):
        result = self.node.execute(_make_state(validated_input=""))
        assert result["status"] == AgentStatus.ERROR.value

    def test_audit_logs_counts_not_raw_content(self, monkeypatch):
        mod = importlib.import_module("src.nodes.extract_decisions_actions_node")
        spy = MagicMock()
        monkeypatch.setattr(mod, "emit_trace_event", spy)

        self.node.execute(_make_state(validated_input=_POLICY_DOC))

        payload = spy.call_args[0][1]
        assert set(payload) == {"agenda_count", "decision_count", "action_count"}
        assert "閣議決定" not in repr(payload)


# ── SummarizeNode ─────────────────────────────────────────────────────────────


class TestSummarizeNode:
    """Structured text summary generation."""

    @pytest.fixture(autouse=True)
    def node(self):
        from src.nodes.summarize_node import SummarizeNode

        self.node = SummarizeNode()

    @staticmethod
    def _extracted_json(agenda=None, decisions=None, actions=None):
        return json.dumps(
            {
                "agenda_items": agenda or [],
                "decisions": decisions or [],
                "action_items": actions or [],
            },
            ensure_ascii=False,
        )

    def test_summarizes_with_extracted_content(self):
        result = self.node.execute(
            _make_state(
                validated_input=_POLICY_DOC,
                document_type="policy",
                extracted_content=self._extracted_json(agenda=["デジタル化推進"], decisions=["計画を決定した"]),
            )
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "政策文書" in result["summary"]
        assert "計画を決定した" in result["summary"]

    def test_minutes_header_in_summary(self):
        result = self.node.execute(
            _make_state(
                validated_input=_MINUTES_DOC,
                document_type="minutes",
                extracted_content=self._extracted_json(agenda=["予算配分審議"]),
            )
        )
        assert "会議議事録" in result["summary"]

    def test_fallback_excerpt_when_no_extraction(self):
        result = self.node.execute(
            _make_state(
                validated_input="これはテスト文書です。",
                document_type="other",
                extracted_content=self._extracted_json(),
            )
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["summary"]

    def test_length_cap_comes_from_state(self):
        """The cap is a validated caller option carried in state, not node config."""
        result = self.node.execute(
            _make_state(
                validated_input="あ" * 100,
                document_type="policy",
                max_summary_chars=50,
                extracted_content=self._extracted_json(agenda=["テスト" * 40]),
            )
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert len(result["summary"]) <= 51

    def test_default_cap_when_state_carries_none(self):
        result = self.node.execute(
            _make_state(
                validated_input="あ" * 100,
                document_type="policy",
                extracted_content=self._extracted_json(agenda=["テスト" * 2000]),
            )
        )
        assert len(result["summary"]) <= 3_000

    def test_all_empty_content_error(self):
        result = self.node.execute(_make_state(validated_input="", extracted_content=None))
        assert result["status"] == AgentStatus.ERROR.value


# ── FormatForPublicTransparencyNode ───────────────────────────────────────────


class TestFormatForPublicTransparencyNode:
    """Audience-appropriate output formatting."""

    @pytest.fixture(autouse=True)
    def node(self):
        from src.nodes.format_for_public_transparency_node import FormatForPublicTransparencyNode

        self.node = FormatForPublicTransparencyNode()

    def test_internal_format_header(self):
        result = self.node.execute(
            _make_state(
                summary="## 政策文書 要約\n\n### 決定事項\n1. デジタル化推進",
                document_type="policy",
                audience_mode="internal",
            )
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "【内部配布用】" in result["formatted_summary"]

    def test_public_format_header(self):
        result = self.node.execute(
            _make_state(
                summary="## 政策文書 要約\n\n### 決定事項\n1. デジタル化推進",
                document_type="policy",
                audience_mode="public",
            )
        )
        assert "【公開用】" in result["formatted_summary"]

    def test_public_redacts_non_disclosure_line(self):
        result = self.node.execute(
            _make_state(
                summary="### 決定事項\n1. 予算配分決定\n2. 内部情報について非公開\n3. 完了事項",
                document_type="policy",
                audience_mode="public",
            )
        )
        formatted = result["formatted_summary"]
        assert "非開示情報のため省略" in formatted
        assert "内部情報について非公開" not in formatted
        assert "予算配分決定" in formatted

    def test_internal_audience_keeps_the_detail(self):
        result = self.node.execute(
            _make_state(
                summary="### 決定事項\n1. 取扱注意事項について秘密保持を確認",
                document_type="policy",
                audience_mode="internal",
            )
        )
        assert "秘密保持" in result["formatted_summary"]

    def test_empty_summary_error(self):
        result = self.node.execute(_make_state(summary="", document_type="policy", audience_mode="internal"))
        assert result["status"] == AgentStatus.ERROR.value


# ── Graph compilation ─────────────────────────────────────────────────────────


class TestGraphCompile:
    """The outer graph compiles with the shipped runtime parameters."""

    def test_graph_compiles_successfully(self):
        from src.graph.graph import GovernmentPolicyMeetingRecordSummarizationAgent

        GovernmentPolicyMeetingRecordSummarizationAgent().compile()

    def test_graph_compiles_with_shipped_config(self):
        import pathlib

        import yaml

        from src.graph.graph import GovernmentPolicyMeetingRecordSummarizationAgent

        config_path = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        agent = GovernmentPolicyMeetingRecordSummarizationAgent(config=config)
        agent.compile()
        assert agent.config["max_retry"] == config["max_retry"]


class TestStatusLeavesEveryNodeAsAString:
    """`status` leaves a node as the enum's string value, never the enum member.

    `AgentStatus` inherits from `str`, so `result["status"] == AgentStatus.SUCCESS.value`
    holds for the bare member as well as for the string. Every equality assertion in
    this suite is satisfied by both, and a node returning the member passes all of them
    while rendering as `AgentStatus.SUCCESS` anywhere the value is formatted into text
    rather than compared.

    Two assertions, because neither alone is enough: the type check drives each node
    this MR touched down both its success and its refusal path, and the source scan
    covers the nodes no test drives — and any node added later.
    """

    def _cases(self):
        from src.nodes.detect_document_type_node import DetectDocumentTypeNode
        from src.nodes.extract_decisions_actions_node import ExtractDecisionsActionsNode
        from src.nodes.format_for_public_transparency_node import FormatForPublicTransparencyNode
        from src.nodes.security_gate_output_node import SecurityGateOutputNode
        from src.nodes.summarize_node import SummarizeNode
        from src.nodes.validate_input_node import ValidateInputNode

        return [
            ("ValidateInputNode accepted", ValidateInputNode(), _make_state(user_input=_POLICY_DOC)),
            ("ValidateInputNode declined", ValidateInputNode(), _make_state(user_input="")),
            ("DetectDocumentTypeNode accepted", DetectDocumentTypeNode(), _make_state(validated_input=_POLICY_DOC)),
            ("DetectDocumentTypeNode refused", DetectDocumentTypeNode(), _make_state(validated_input="")),
            ("ExtractDecisionsActionsNode accepted", ExtractDecisionsActionsNode(), _make_state(validated_input=_POLICY_DOC)),
            ("ExtractDecisionsActionsNode refused", ExtractDecisionsActionsNode(), _make_state(validated_input="")),
            ("SummarizeNode accepted", SummarizeNode(), _make_state(validated_input=_POLICY_DOC, document_type="policy")),
            ("SummarizeNode refused", SummarizeNode(), _make_state(validated_input="", document_type="policy")),
            ("FormatForPublicTransparencyNode accepted", FormatForPublicTransparencyNode(),
             _make_state(summary="## 政策文書 要約\n\n### 決定事項\n1. デジタル化推進",
                         document_type="policy", audience_mode="internal")),
            ("FormatForPublicTransparencyNode refused", FormatForPublicTransparencyNode(),
             _make_state(summary="", document_type="policy", audience_mode="internal")),
            ("SecurityGateOutputNode published", SecurityGateOutputNode(),
             _make_state(result="決定の要旨", formatted_output="決定の要旨", validated_input=_POLICY_DOC)),
            ("SecurityGateOutputNode withheld", SecurityGateOutputNode(),
             _make_state(formatted_summary="", summary="", result="")),
        ]

    def test_the_value_a_node_returns_is_exactly_str(self):
        checked = 0
        for label, node, state in self._cases():
            result = node.execute(state)
            if "status" not in result:
                continue
            checked += 1
            assert type(result["status"]) is str, (
                f"{label}: status is {type(result['status']).__name__}, not str — "
                "an enum member here compares equal but formats as its own name"
            )
        assert checked, "no case produced a status — the probe itself is broken"

    def test_no_module_writes_a_bare_enum_member_into_status(self):
        """The scan the equality assertions cannot perform."""
        import re

        pattern = re.compile(r'"status"\s*:\s*AgentStatus\.[A-Z_]+(?![A-Z_])(?!\s*\.value)')
        offenders = []
        for path in sorted(pathlib.Path("src").rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if pattern.search(line):
                    offenders.append(f"{path}:{number}: {line.strip()}")

        assert not offenders, "status must carry AgentStatus.<X>.value:\n" + "\n".join(offenders)
