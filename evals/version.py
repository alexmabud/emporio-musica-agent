"""The version of the agent under evaluation: prompt, tool schemas and model.

Saved results are only comparable, and a complete eval may only be resumed across days, when these
three are exactly the same. The prompt part ignores the moment-dependent section ("Agora": date,
time and whether the store is open), which changes every minute without changing the agent.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime

from langchain_core.tools import BaseTool

from emporio_agent.hours import STORE_TIMEZONE
from emporio_agent.prompts import build_system_prompt

# Any fixed moment will do: the section it affects is removed below.
_REFERENCE_MOMENT = datetime(2026, 1, 5, 10, 0, tzinfo=STORE_TIMEZONE)
_MOMENT_SECTION = re.compile(r"## Agora\n.*?\n\n(?=## )", re.DOTALL)

PARTS = {"model": "model", "prompt_sha": "prompt", "tools_sha": "tool schemas"}


def strip_moment(prompt: str) -> str:
    """Remove the date, time and store-status section from a system prompt."""
    return _MOMENT_SECTION.sub("", prompt, count=1)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compute_version(policy_text: str, tools: Sequence[BaseTool], model_spec: str) -> dict[str, str]:
    """Fingerprint of the agent under evaluation.

    Args:
        policy_text: Clean text of the policy manual, as injected in the prompt.
        tools: The agent's tools; their names, descriptions and argument schemas are hashed.
        model_spec: The model in ``provider:model`` format.

    Returns:
        ``model`` and short hashes of the static prompt (``prompt_sha``) and of the tool schemas
        (``tools_sha``).
    """
    prompt = strip_moment(build_system_prompt(policy_text, _REFERENCE_MOMENT))
    schemas = sorted(
        (tool.name, tool.description, tool.get_input_schema().model_json_schema()) for tool in tools
    )
    return {
        "model": model_spec,
        "prompt_sha": _sha(prompt),
        "tools_sha": _sha(json.dumps(schemas, sort_keys=True, ensure_ascii=False)),
    }


def differences(saved: dict[str, str], current: dict[str, str]) -> list[str]:
    """Names of the parts (model, prompt, tool schemas) that differ between two versions."""
    return [name for key, name in PARTS.items() if saved.get(key) != current.get(key)]
