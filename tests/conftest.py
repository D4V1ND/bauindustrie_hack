import pytest

from lv import ai


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch, tmp_path):
    """No test may reach Claude or Voyage: the keys are hidden from every test, in the environment and in .env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(ai, "ENV_FILE", tmp_path / "no-key-here" / ".env")
