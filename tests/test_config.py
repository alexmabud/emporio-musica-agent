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


def test_reserve_models_without_their_provider_key_are_ignored_not_an_error() -> None:
    """Only GOOGLE_API_KEY is filled in: a Groq reserve is skipped, a Gemini reserve is kept."""
    env = {
        "LLM_MODEL": "google_genai:gemini-3.5-flash",
        "GOOGLE_API_KEY": "fake-google-key",
        "GROQ_API_KEY": "",  # the blank line a copied .env.example may carry
        "LLM_FALLBACK_MODELS": "groq:openai/gpt-oss-120b, google_genai:gemini-3.5-flash-lite",
    }
    settings = load_settings(env, root=ROOT)
    assert settings.llm_fallback_models == ("google_genai:gemini-3.5-flash-lite",)


def test_a_reserve_is_kept_when_its_key_is_there_or_its_provider_is_unknown() -> None:
    """Groq with a key stays; GEMINI_API_KEY counts for Google; other providers are not judged."""
    env = {
        "LLM_MODEL": "google_genai:gemini-3.5-flash",
        "GEMINI_API_KEY": "fake",
        "GROQ_API_KEY": "fake",
        "LLM_FALLBACK_MODELS": "groq:a, google_genai:b, other:c",
    }
    assert load_settings(env, root=ROOT).llm_fallback_models == (
        "groq:a",
        "google_genai:b",
        "other:c",
    )
    no_keys = {"LLM_MODEL": "x:y", "LLM_FALLBACK_MODELS": "groq:a, google_genai:b, other:c"}
    assert load_settings(no_keys, root=ROOT).llm_fallback_models == ("other:c",)


def test_the_main_model_is_never_filtered_by_its_key() -> None:
    """A missing key for the model in use must stay a visible startup problem, not vanish."""
    settings = load_settings({"LLM_MODEL": "groq:openai/gpt-oss-120b"}, root=ROOT)
    assert settings.llm_model == "groq:openai/gpt-oss-120b"


def test_env_example_defaults_to_gemini_and_makes_the_groq_key_optional() -> None:
    """A copied .env.example needs only GOOGLE_API_KEY: Gemini is the default, Groq is optional."""
    from dotenv import dotenv_values

    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
    assert values["LLM_MODEL"] == "google_genai:gemini-3.5-flash"
    assert "GOOGLE_API_KEY" in values and "GROQ_API_KEY" not in values  # the latter is a comment
    assert not values["LLM_FALLBACK_MODELS"]  # results belong to a single model by default
    settings = load_settings({**values, "GOOGLE_API_KEY": "fake"}, root=ROOT)
    assert settings.llm_model == "google_genai:gemini-3.5-flash"
    assert settings.llm_fallback_models == ()
