import pytest

from src.agent import guardrails


def test_tool_limit_not_exceeded():
    assert guardrails.check_tool_limit(5) is False


def test_tool_limit_exceeded():
    assert guardrails.check_tool_limit(20) is True


def test_confidence_gate_uncertain():
    assert guardrails.check_confidence("I am not sure how to proceed with this record.") is True


def test_confidence_gate_certain():
    assert guardrails.check_confidence("The fallout has been resolved by updating the NPI field.") is False


def test_tool_allowed_empty_allowlist():
    assert guardrails.is_tool_allowed("call_api", []) is True


def test_tool_allowed_in_list():
    assert guardrails.is_tool_allowed("call_api", ["call_api", "escalate"]) is True


def test_tool_not_allowed():
    assert guardrails.is_tool_allowed("query_db", ["call_api", "escalate"]) is False
