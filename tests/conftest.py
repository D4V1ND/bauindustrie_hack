import pytest

from lv import ai


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch, tmp_path):
    """No test may reach Claude: the key is hidden from every test, whether it sits in the environment or in .env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai, "ENV_FILE", tmp_path / "no-key-here" / ".env")
