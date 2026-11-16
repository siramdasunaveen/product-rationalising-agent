import json

import pytest

from app.adapters.local.ai import NoLLMProvider
from app.ports.ai import AIRawResult, AIRequest, AIUnavailable
from app.services.ai_runner import citations_must_exist, run_ai_task

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "citations"],
    "additionalProperties": False,
}
REQUEST = AIRequest(task="explanation", system="sys", prompt="Explain CAND-1042", schema=SCHEMA)
FALLBACK = {"summary": "Template explanation", "citations": []}


class FakeProvider:
    """Returns scripted responses in order; records the prompts it received."""

    name = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, request: AIRequest) -> AIRawResult:
        self.prompts.append(request.prompt)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        text = nxt if isinstance(nxt, str) else json.dumps(nxt)
        return AIRawResult(text=text, model="fake-model", provider=self.name)


def test_valid_output_is_used():
    provider = FakeProvider({"summary": "Duplicate of P-2", "citations": ["EV-1"]})
    result = run_ai_task(provider, REQUEST, fallback=lambda: FALLBACK,
                         citation_check=citations_must_exist({"EV-1"}))
    assert result.source == "llm"
    assert result.attempts == 1
    assert result.output["citations"] == ["EV-1"]


def test_invented_citation_is_sent_back_then_corrected():
    provider = FakeProvider(
        {"summary": "x", "citations": ["EV-404"]},
        {"summary": "x", "citations": ["EV-1"]},
    )
    result = run_ai_task(provider, REQUEST, fallback=lambda: FALLBACK,
                         citation_check=citations_must_exist({"EV-1"}))
    assert result.source == "llm"
    assert result.attempts == 2
    assert "EV-404" in provider.prompts[1]  # the rejection reason went back to the model


def test_schema_violation_retries_max_two_then_falls_back():
    provider = FakeProvider({"summary": 1}, "not json", {"summary": "x"})
    result = run_ai_task(provider, REQUEST, fallback=lambda: FALLBACK, max_retries=2)
    assert result.source == "fallback"
    assert result.attempts == 3
    assert result.output == FALLBACK
    assert any("not valid JSON" in e for e in result.errors)


def test_unavailable_goes_straight_to_fallback():
    provider = FakeProvider(AIUnavailable("down"))
    result = run_ai_task(provider, REQUEST, fallback=lambda: FALLBACK)
    assert result.source == "fallback"
    assert result.attempts == 1


def test_no_llm_mode_uses_fallback():
    result = run_ai_task(NoLLMProvider(), REQUEST, fallback=lambda: FALLBACK)
    assert result.source == "fallback"
    assert result.output == FALLBACK


def test_invalid_fallback_is_a_programming_error():
    with pytest.raises(ValueError, match="violates its schema"):
        run_ai_task(NoLLMProvider(), REQUEST, fallback=lambda: {"oops": True})


def test_citation_check_finds_nested_ids():
    check = citations_must_exist({"EV-1", "LN-7"})
    output = {"claims": [{"text": "a", "evidence_ids": ["EV-1"]}, {"text": "b", "lineage_ids": ["LN-9"]}]}
    assert check(output) == ["cited id does not exist: LN-9"]
