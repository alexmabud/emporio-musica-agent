"""Builds the README comparison table from the saved results: ``python -m evals.report``."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from emporio_agent.config import PROJECT_ROOT

RESULTS_DIR = PROJECT_ROOT / "evals" / "results"


def load_results(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    """Read the saved runs of full scenario sets (partial and aborted runs are left out)."""
    documents = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(results_dir.glob("*.json"))
    ]
    return [doc for doc in documents if not doc.get("partial") and not doc.get("aborted")]


def _cell(record: dict[str, Any] | None) -> str:
    """``✅ 2/3`` : critical verdict, then desirable checks passed over total."""
    if record is None:
        return "–"
    desirable = [c for c in record["checks"] if c["level"] == "desirable"]
    done = sum(1 for c in desirable if c["passed"])
    return f"{'✅' if record['passed'] else '❌'} {done}/{len(desirable)}"


def render_table(documents: Sequence[dict[str, Any]]) -> str:
    """Markdown table: one row per scenario, per model the critical verdict and desirable count.

    Args:
        documents: Saved runs, as written by ``evals.run``.

    Returns:
        The table plus one summary line per model.
    """
    if not documents:
        return "No complete eval run saved yet."
    models = [doc["model"] for doc in documents]
    by_model = [{s["id"]: s for s in doc["scenarios"]} for doc in documents]
    order: dict[str, str] = {}
    for doc in documents:
        for scenario in doc["scenarios"]:
            order.setdefault(scenario["id"], scenario["title"])

    lines = [
        "| Scenario | " + " | ".join(f"`{model}`" for model in models) + " |",
        "|---|" + "---|" * len(models),
    ]
    for scenario_id, title in order.items():
        cells = " | ".join(_cell(records.get(scenario_id)) for records in by_model)
        lines.append(f"| {scenario_id} — {title} | {cells} |")
    lines += [
        "",
        "Cell: critical verdict (✅ pass / ❌ fail), then desirable checks passed/total.",
        "",
    ]
    lines += [f"- {doc['summary']}" for doc in documents]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the table for the results folder (default ``evals/results``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    print(render_table(load_results(Path(args[0]) if args else RESULTS_DIR)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
