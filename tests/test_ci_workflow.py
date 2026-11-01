"""Guards the CI workflow: it must stay deterministic, keyless and on the supported Python."""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_runs_on_push_and_on_pull_requests() -> None:
    """Every push and every PR gets checked."""
    text = _text()
    assert "\non:\n" in text
    assert "\n  push:" in text and "\n  pull_request:" in text


def test_the_workflow_uses_python_3_11_with_a_pip_cache() -> None:
    """3.11 is the lowest supported version (pyproject requires >=3.11); pip is cached."""
    text = _text()
    assert 'python-version: "3.11"' in text
    assert "cache: pip" in text
    assert "requirements-dev.txt" in text


def test_the_workflow_runs_ruff_and_the_deterministic_tests() -> None:
    """Lint, format check and pytest: the same commands as `make lint` and `make test`."""
    text = _text()
    assert "ruff check ." in text
    assert "ruff format --check ." in text
    assert "\n        run: pytest" in text


def test_the_workflow_needs_no_secret_and_never_calls_a_model() -> None:
    """No API key in CI, and the LLM eval (`make eval`) is never part of it."""
    code = "\n".join(
        line for line in _text().splitlines() if not line.lstrip().startswith("#")
    )  # comments may mention `make eval` to say it is NOT run here
    text = code.lower()
    assert "secrets." not in text
    assert "api_key" not in text
    assert "evals.run" not in text and "make eval" not in text
