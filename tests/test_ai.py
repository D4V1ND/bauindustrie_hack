import pytest
from pydantic import BaseModel

from lv import ai


class Capital(BaseModel):
    country: str
    capital: str


@pytest.fixture
def no_key(monkeypatch, tmp_path):
    """An empty place for the key file. conftest.py already hides the real key from every test."""
    monkeypatch.setattr(ai, "ENV_FILE", tmp_path / ".env")
    return tmp_path / ".env"


def test_a_missing_key_stops_with_a_message_that_says_what_to_do(no_key):
    with pytest.raises(ai.MissingKey) as stop:
        ai.ask(ai.JUDGE_MODEL, "Answer in the requested shape.", ["Germany"], Capital, effort="high")

    assert "ANTHROPIC_API_KEY=" in str(stop.value) and str(no_key) in str(stop.value)


def test_no_question_needs_no_key(no_key):
    assert ai.ask(ai.JUDGE_MODEL, "Answer in the requested shape.", [], Capital, effort="high") == []


def test_the_key_is_read_from_the_env_file_and_the_request_is_built_the_way_claude_accepts(no_key):
    no_key.write_text("ANTHROPIC_API_KEY=test-key-not-real\n", encoding="utf-8")

    call = ai.structured(ai.JUDGE_MODEL, Capital, "low").first  # the chat model with its bound request settings

    assert call.bound.anthropic_api_key.get_secret_value() == "test-key-not-real"
    assert (call.bound.model, call.bound.temperature) == ("claude-sonnet-5-5", None)
    assert call.bound.reasoning_effort == "low"  # the effort level that was asked for travels with the request
    assert call.kwargs["output_config"]["format"]["type"] == "json_schema"  # the native method
    assert "tool_choice" not in call.kwargs and "tools" not in call.kwargs  # no forced tool call


def test_every_job_names_its_model_and_its_effort_level():
    assert (ai.PROPOSE_MODEL, ai.PROPOSE_EFFORT) == ("claude-opus-5-5", "medium")
    assert (ai.ASSIGN_MODEL, ai.ASSIGN_EFFORT) == ("claude-sonnet-5-5", "high")
    assert (ai.JUDGE_MODEL, ai.JUDGE_EFFORT) == ("claude-sonnet-5-5", "high")


class FakeModel:
    def __init__(self, answers):
        self.answers = answers

    def batch(self, prompts, config, return_exceptions):
        assert return_exceptions and config["max_concurrency"] == ai.MAX_PARALLEL_CALLS
        assert prompts[0] == [("system", "Answer in the requested shape."), ("human", "Germany")]
        return self.answers


def test_a_failed_call_gives_no_answer_for_that_question_only(monkeypatch):
    berlin = Capital(country="Germany", capital="Berlin")
    monkeypatch.setattr(ai, "structured", lambda model, shape, effort: FakeModel([berlin, TimeoutError("too slow")]))

    assert ai.ask(ai.JUDGE_MODEL, "Answer in the requested shape.", ["Germany", "France"], Capital, effort="high") == [berlin, None]


def test_when_every_call_fails_the_first_error_is_reported(monkeypatch):
    monkeypatch.setattr(ai, "structured", lambda model, shape, effort: FakeModel([PermissionError("invalid x-api-key")] * 2))

    with pytest.raises(RuntimeError, match="invalid x-api-key"):
        ai.ask(ai.JUDGE_MODEL, "Answer in the requested shape.", ["Germany", "France"], Capital, effort="high")
