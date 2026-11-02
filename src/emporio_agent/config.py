"""Settings read from environment variables (see ``.env.example``)."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised when a setting is missing or invalid; the message says how to fix it."""


@dataclass(frozen=True)
class Settings:
    """Everything the agent needs from its environment.

    Attributes:
        llm_model: Model in LangChain ``init_chat_model`` format, e.g. ``google_genai:<model>``.
        llm_temperature: Sampling temperature.
        llm_fallback_models: Reserve models, tried in order when the main one fails.
        grounding_max_retries: Regenerations allowed for a reply with unverified amounts.
        data_dir: Folder with the original CSVs and the policy PDF.
        policy_pdf: The policy manual.
        db_path: SQLite file rebuilt from the CSVs (git-ignored).
        escalations_path: JSON Lines file for human escalations (git-ignored).
        feedback_path: JSON Lines file for the 👍/👎 votes of the Streamlit UI (git-ignored).
    """

    llm_model: str
    llm_temperature: float
    llm_fallback_models: tuple[str, ...]
    grounding_max_retries: int
    data_dir: Path
    policy_pdf: Path
    db_path: Path
    escalations_path: Path
    feedback_path: Path


def _path(env: Mapping[str, str], name: str, default: str, root: Path) -> Path:
    """Read a path setting; relative paths hang from the project root."""
    path = Path(env.get(name) or default)
    return path if path.is_absolute() else root / path


def load_settings(env: Mapping[str, str], root: Path = PROJECT_ROOT) -> Settings:
    """Build the settings from environment variables.

    Args:
        env: Environment variables (``os.environ`` in normal use).
        root: Project folder that relative paths are resolved against.

    Returns:
        The validated settings.

    Raises:
        ConfigError: If ``LLM_MODEL`` is missing, ``LLM_TEMPERATURE`` is not a number or
            ``GROUNDING_MAX_RETRIES`` is not a non-negative integer.
    """
    model = (env.get("LLM_MODEL") or "").strip()
    if not model:
        raise ConfigError(
            "LLM_MODEL is not set. Copy .env.example to .env and set LLM_MODEL "
            "(e.g. google_genai:gemini-3.5-flash) and the provider's API key."
        )
    try:
        temperature = float(env.get("LLM_TEMPERATURE") or 0.2)
    except ValueError as error:
        raise ConfigError(
            f"LLM_TEMPERATURE must be a number, got {env.get('LLM_TEMPERATURE')!r}."
        ) from error
    retries_text = (env.get("GROUNDING_MAX_RETRIES") or "1").strip()
    if not retries_text.isdigit():
        raise ConfigError(
            f"GROUNDING_MAX_RETRIES must be a non-negative integer, got {retries_text!r}."
        )
    fallbacks = [name.strip() for name in (env.get("LLM_FALLBACK_MODELS") or "").split(",")]
    data_dir = root / "data"
    return Settings(
        llm_model=model,
        llm_temperature=temperature,
        llm_fallback_models=tuple(name for name in fallbacks if name),
        grounding_max_retries=int(retries_text),
        data_dir=data_dir,
        policy_pdf=data_dir / "politicas_da_loja.pdf",
        db_path=_path(env, "DB_PATH", "var/emporio.db", root),
        escalations_path=_path(env, "ESCALATIONS_PATH", "var/escalations.jsonl", root),
        feedback_path=_path(env, "FEEDBACK_PATH", "var/feedback.jsonl", root),
    )
