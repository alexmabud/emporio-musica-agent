"""Behavior tests for reading the agent settings from the environment."""

from pathlib import Path

import pytest

from emporio_agent.config import ConfigError, load_settings

ROOT = Path("/project")


def test_defaults_and_paths_are_resolved_against_the_project_root() -> None:
    """Only LLM_MODEL is required; everything else has a default under the project folder."""
    settings = load_settings({"LLM_MODEL": "google_genai:gemini-3.5-flash"}, root=ROOT)
    assert settings.llm_model == "google_genai:gemini-3.5-flash"
    assert settings.llm_temperature == 0.2
    assert settings.data_dir == ROOT / "data"
    assert settings.policy_pdf == ROOT / "data" / "politicas_da_loja.pdf"
    assert settings.db_path == ROOT / "var" / "emporio.db"
    assert settings.escalations_path == ROOT / "var" / "escalations.jsonl"
    assert settings.feedback_path == ROOT / "var" / "feedback.jsonl"


def test_values_from_the_environment_override_the_defaults() -> None:
    """Relative paths hang from the root; absolute paths are kept."""
    env = {
        "LLM_MODEL": "groq:some-model",
        "LLM_TEMPERATURE": "0.7",
        "DB_PATH": "build/store.db",
        "ESCALATIONS_PATH": "/tmp/cases.jsonl",
        "FEEDBACK_PATH": "ratings/votes.jsonl",
    }
    settings = load_settings(env, root=ROOT)
    assert settings.llm_temperature == 0.7
    assert settings.db_path == ROOT / "build" / "store.db"
    assert settings.escalations_path == Path("/tmp/cases.jsonl")
    assert settings.feedback_path == ROOT / "ratings" / "votes.jsonl"


@pytest.mark.parametrize("env", [{}, {"LLM_MODEL": "  "}])
def test_a_missing_model_explains_how_to_fix_it(env: dict[str, str]) -> None:
    """The first thing a new user hits must say what to do, not show a stack trace."""
    with pytest.raises(ConfigError, match=r"LLM_MODEL.*\.env\.example"):
        load_settings(env, root=ROOT)


def test_an_invalid_temperature_is_rejected() -> None:
    """A typo in LLM_TEMPERATURE is reported by name."""
    with pytest.raises(ConfigError, match="LLM_TEMPERATURE"):
        load_settings({"LLM_MODEL": "x:y", "LLM_TEMPERATURE": "warm"}, root=ROOT)


def test_fallback_models_are_a_comma_separated_list() -> None:
    """LLM_FALLBACK_MODELS lists the reserve models in order; blanks are ignored."""
    env = {"LLM_MODEL": "a:main", "LLM_FALLBACK_MODELS": " a:one , a:two ,, "}
    assert load_settings(env, root=ROOT).llm_fallback_models == ("a:one", "a:two")
    assert load_settings({"LLM_MODEL": "a:main"}, root=ROOT).llm_fallback_models == ()


def test_grounding_retries_default_to_one_and_can_be_changed() -> None:
    """GROUNDING_MAX_RETRIES: one regeneration by default, 0 allowed, junk rejected by name."""
    assert load_settings({"LLM_MODEL": "a:b"}, root=ROOT).grounding_max_retries == 1
    assert (
        load_settings(
            {"LLM_MODEL": "a:b", "GROUNDING_MAX_RETRIES": "0"}, root=ROOT
        ).grounding_max_retries
        == 0
    )
    for bad in ("-1", "many", "1.5"):
        with pytest.raises(ConfigError, match="GROUNDING_MAX_RETRIES"):
            load_settings({"LLM_MODEL": "a:b", "GROUNDING_MAX_RETRIES": bad}, root=ROOT)
