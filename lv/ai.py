"""The one place for the Claude setup: model names, the API key and the call that returns a fixed shape.

Check one real call by hand:  .venv\\Scripts\\python.exe -m lv.ai
"""
import os
from pathlib import Path
from typing import TypeVar

from dotenv import dotenv_values
from pydantic import BaseModel

JUDGE_MODEL = "claude-sonnet-5-5"  # step 3 judge: the variant that was measured
MAX_OUTPUT_TOKENS = 16000  # answers are small; the room is for the model's thinking
MAX_PARALLEL_CALLS = 4
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

Shape = TypeVar("Shape", bound=BaseModel)


class MissingKey(Exception):
    """No API key is set up. The message says what to do."""


def structured(model: str, shape: type[Shape]):
    """The chat model, set up to answer in `shape`."""
    key = os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(ENV_FILE).get("ANTHROPIC_API_KEY")
    if not key:
        raise MissingKey(
            "This step asks Claude, and no API key is set up.\n"
            f"Create the file {ENV_FILE} with one line:\n"
            "ANTHROPIC_API_KEY=<your key from https://platform.claude.com>\n"
            "The file is listed in .gitignore, so the key stays out of git.")
    from langchain_anthropic import ChatAnthropic  # imported here: it is slow to load and most runs never ask

    # No temperature (current Claude models reject it) and no forced tool call: "json_schema" is Claude's own
    # structured output. Checked against langchain-anthropic 1.7.
    chat = ChatAnthropic(model=model, api_key=key, max_tokens=MAX_OUTPUT_TOKENS)
    return chat.with_structured_output(shape, method="json_schema")


def ask(model: str, system: str, questions: list[str], shape: type[Shape]) -> list[Shape | None]:
    """One call per question. Answers come back in the order of the questions; a failed call gives None."""
    if not questions:
        return []
    answers = structured(model, shape).batch(
        [[("system", system), ("human", question)] for question in questions],
        config={"max_concurrency": MAX_PARALLEL_CALLS}, return_exceptions=True)
    errors = [answer for answer in answers if isinstance(answer, Exception)]
    if len(errors) == len(questions):  # e.g. a wrong key or model name: say so instead of returning nothing
        raise RuntimeError(f"Every call to {model} failed. First error: {errors[0]}")
    return [None if isinstance(answer, Exception) else answer for answer in answers]


if __name__ == "__main__":
    class Capital(BaseModel):
        country: str
        capital: str

    try:
        print(ask(JUDGE_MODEL, "Answer in the requested shape.", ["Germany"], Capital))
    except MissingKey as stop:
        raise SystemExit(str(stop))
