# Output boundary: what the gate promises, it enforces for every representation.
#
# The documented promise is two-part:
#   - restricted government material is never re-emitted verbatim, whichever
#     audience was requested;
#   - for the public audience, non-disclosable passages are removed.
#
# Each test below states one representation of that promise and checks it
# against the real gate. The reverse direction is checked too: text the promise
# does not cover must come through byte-identical, because a boundary that
# rewrites ordinary content is a different kind of failure.

import importlib

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes.security_gate_output_node import (
    _CLASSIFIED_MARKER,
    _MAX_OUTPUT_CHARS,
    _NON_DISCLOSURE_MARKER,
    _TRUNCATION_NOTICE,
    SecurityGateOutputNode,
    _security_gate_output,
)


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch):
    mod = importlib.import_module("src.nodes.security_gate_output_node")
    monkeypatch.setattr(mod, "emit_trace_event", lambda *a, **kw: None)


@pytest.fixture()
def node():
    return SecurityGateOutputNode()


def _gate(text, audience="internal"):
    sanitized, triggered = _security_gate_output(text, audience)
    return sanitized, triggered


# ── Restricted material: the passage goes, not just the word ──────────────────

_CLASSIFIED_LINES = [
    "3. 機密情報として添付資料Aに山田太郎の連絡先を記載した",
    "4. 特定秘密に指定された運用計画の詳細は別紙のとおり",
    "5. 防衛秘密に該当する装備数量は12個である",
    "6. 外交上の秘密に属する交渉経緯を確認した",
    "7. 内閣情報調査室から提供された資料を参照した",
    "8. 未公表の個人情報を含む名簿を確認した",
]


@pytest.mark.parametrize("line", _CLASSIFIED_LINES)
@pytest.mark.parametrize("audience", ["internal", "public"])
def test_restricted_line_is_removed_whole(line, audience):
    """Replacing only the marker word would leave the sentence that carries the
    content — which is the verbatim re-emission the gate exists to prevent."""
    body = f"### 決定事項\n1. 予算配分を決定した\n{line}\n9. 会議を閉会した"
    sanitized, triggered = _gate(body, audience)

    assert triggered is True
    assert line not in sanitized
    # the identifying detail inside the line is gone with it
    for fragment in ("山田太郎", "別紙のとおり", "12個", "交渉経緯", "名簿"):
        if fragment in line:
            assert fragment not in sanitized
    # the surrounding content is untouched
    assert "1. 予算配分を決定した" in sanitized
    assert "9. 会議を閉会した" in sanitized


def test_restricted_marker_word_alone_still_triggers():
    sanitized, triggered = _gate("計画内容：機密情報を含む予算案")
    assert triggered is True
    assert "機密情報を含む予算案" not in sanitized
    assert _CLASSIFIED_MARKER in sanitized


# ── Public audience: the rule is re-applied at the boundary ───────────────────

_NON_DISCLOSURE_LINES = [
    "2. 内部情報の取り扱いについて確認した",
    "3. 非公開とされた資料を参照した",
    "4. 個人情報の管理体制を点検した",
    "5. プライバシーに関する懸念を議論した",
    "6. 秘密保持契約の締結を了承した",
]


@pytest.mark.parametrize("line", _NON_DISCLOSURE_LINES)
def test_public_audience_removes_non_disclosable_lines_at_the_boundary(line):
    """The formatting node applies this rule too. The gate applies it again, so
    the guarantee does not depend on an upstream node having run."""
    body = f"### 決定事項\n1. 予算配分を決定した\n{line}"
    sanitized, triggered = _gate(body, "public")

    assert triggered is True
    assert line not in sanitized
    assert _NON_DISCLOSURE_MARKER in sanitized
    assert "1. 予算配分を決定した" in sanitized


@pytest.mark.parametrize("line", _NON_DISCLOSURE_LINES)
def test_internal_audience_keeps_non_disclosable_lines(line):
    """The internal briefing is entitled to this content; only restricted
    material is removed for that audience."""
    sanitized, _ = _gate(f"### 決定事項\n{line}", "internal")
    assert line in sanitized


# ── Layering: the two rules stay independent, and re-scanning is a no-op ──────


def test_markers_are_inert_to_both_scans():
    """A redacted line must not be re-labelled by the other layer, so each
    marker says which rule removed the passage."""
    for marker in (_CLASSIFIED_MARKER, _NON_DISCLOSURE_MARKER, _TRUNCATION_NOTICE):
        for audience in ("internal", "public"):
            sanitized, triggered = _gate(marker, audience)
            assert sanitized == marker
            assert triggered is False


@pytest.mark.parametrize("audience", ["internal", "public"])
def test_gate_is_idempotent(audience):
    body = (
        "### 決定事項\n1. 予算配分を決定した\n2. 機密情報の取扱いを確認した\n"
        "3. 非公開資料を参照した\n4. 会議を閉会した"
    )
    once, _ = _gate(body, audience)
    twice, _ = _gate(once, audience)
    assert twice == once


def test_restricted_line_beyond_the_length_cap_never_reappears():
    filler = "\n".join(f"{i}. 通常の審議事項を確認した" for i in range(1200))
    body = filler + "\n機密情報として指定された添付資料の内容"
    sanitized, _ = _gate(body, "internal")
    assert "機密情報として指定された添付資料の内容" not in sanitized
    assert len(sanitized) <= _MAX_OUTPUT_CHARS + len(_TRUNCATION_NOTICE) + 1


def test_truncation_happens_at_a_line_boundary():
    """Cutting mid-line could leave half a redaction marker reading as prose."""
    body = "\n".join(f"{i}. 通常の審議事項を確認した" for i in range(1200))
    sanitized, _ = _gate(body, "internal")
    assert sanitized.endswith(_TRUNCATION_NOTICE)
    for line in sanitized.split("\n")[:-1]:
        assert line == "" or line.endswith("確認した")


# ── The reverse direction: ordinary content is never rewritten ────────────────

_STRUCTURAL_TOKENS = [
    "議題：予算配分 1,234,567円について",
    "文書番号 GOV-2026-004 の取扱いを確認した",
    "令和6年度 第3回 審議会",
    "情報公開法第5条第1号の規定により整理した",
    "対象期間は 90d、件数は 4821 件である",
    "整理番号 ABC-1234 および JPY 1,000 の記載を確認した",
    "AGENTIC STAR 2026 に関する報告",
    "電話 03-1234-5678、内線 4821",
]


@pytest.mark.parametrize("line", _STRUCTURAL_TOKENS)
@pytest.mark.parametrize("audience", ["internal", "public"])
def test_ordinary_lines_pass_through_byte_identical(line, audience):
    """The template renders excerpts of a document, not computed aggregates, so
    it applies no numeric rounding and rewrites no identifier. Document numbers,
    article references, fiscal years and quoted figures survive unchanged."""
    sanitized, triggered = _gate(line, audience)
    assert sanitized == line
    assert triggered is False


def test_a_document_with_no_restricted_content_is_returned_unchanged():
    body = "\n".join(_STRUCTURAL_TOKENS)
    sanitized, triggered = _gate(body, "public")
    assert sanitized == body
    assert triggered is False


# ── No route around the gate ──────────────────────────────────────────────────


def test_both_output_fields_carry_the_gated_text(node):
    """The framework surfaces `formatted_output or result`; leaving the pre-gate
    text in `result` would hand a caller a way past the boundary."""
    state = {
        "formatted_summary": "1. 予算配分を決定した\n2. 機密情報の詳細を記載した",
        "result": "1. 予算配分を決定した\n2. 機密情報の詳細を記載した",
        "audience_mode": "internal",
        "node_history": [],
        "error_log": [],
        "correlation_id": "boundary-test",
    }
    result = node.execute(state)
    assert result["status"] == AgentStatus.SUCCESS.value
    assert result["formatted_output"] == result["result"]
    assert "機密情報の詳細を記載した" not in result["result"]


def test_no_output_to_gate_is_an_error(node):
    result = node.execute(
        {
            "formatted_summary": "",
            "summary": "",
            "result": "",
            "node_history": [],
            "error_log": [],
            "correlation_id": "boundary-test",
        }
    )
    assert result["status"] == AgentStatus.ERROR.value
    assert result.get("formatted_output") is None


# ── Audit trail ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body,expected",
    [
        ("承認された予算計画の概要", False),
        ("特定秘密保護法の対象となる記録を確認した", True),
    ],
)
def test_audit_records_the_decision_and_never_the_text(node, monkeypatch, body, expected):
    from unittest.mock import MagicMock

    mod = importlib.import_module("src.nodes.security_gate_output_node")
    spy = MagicMock()
    monkeypatch.setattr(mod, "emit_trace_event", spy)

    node.execute(
        {
            "formatted_summary": body,
            "audience_mode": "internal",
            "node_history": [],
            "error_log": [],
            "correlation_id": "boundary-test",
        }
    )

    assert spy.call_args[0][0] == "gov_c2_004.security_gate_output"
    payload = spy.call_args[0][1]
    assert payload["gate_triggered"] is expected
    assert set(payload) == {"gate_triggered", "output_length", "audience_mode", "document_type"}
    assert body not in repr(payload)
