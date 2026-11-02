"""Scoring from saved results: totals, the summary line and ``--rescore``.

The transcript of every scenario is saved with the result, so the checks can be run again without
calling the model. That is how a check that turned out to be wrong is fixed honestly: the replies
stay exactly as the model wrote them, only the verdict on them changes, and the change is logged.
"""

from collections.abc import Sequence
from typing import Any

from evals.checks import Run, TurnResult, evaluate, is_passed
from evals.scenarios import Scenario


def totals(records: list[dict[str, Any]]) -> dict[str, int]:
    """Counts over the scenario records: verdicts, desirable checks and model requests."""
    checks = [c for record in records for c in record["checks"]]
    desirable = [c for c in checks if c["level"] == "desirable"]
    return {
        "scenarios_run": len(records),
        "scenarios_passed": sum(1 for record in records if record["passed"]),
        "model_calls": sum(record.get("model_calls", 0) for record in records),
        "failed_calls": sum(record.get("failed_calls", 0) for record in records),
        "grounding_retries": sum(record.get("grounding_retries", 0) for record in records),
        "safe_messages": sum(record.get("safe_messages", 0) for record in records),
        "desirable_passed": sum(1 for c in desirable if c["passed"]),
        "desirable_total": len(desirable),
    }


def summary_line(
    model: str, stamp: str, counts: dict[str, int], rescored: str | None = None
) -> str:
    """The one-line markdown summary of a result (``rescored``: date of the last re-scoring)."""
    line = (
        f"`{model}` · {stamp} · critical {counts['scenarios_passed']}/"
        f"{counts['scenarios_run']} scenarios passed · desirable "
        f"{counts['desirable_passed']}/{counts['desirable_total']} checks · "
        f"{counts['model_calls']} model calls"
    )
    return (
        f"{line} · checks re-scored from the saved transcripts on {rescored}" if rescored else line
    )


def run_from_record(record: dict[str, Any]) -> Run:
    """Rebuild what the checks look at from a saved scenario record."""
    return Run(
        turns=tuple(
            TurnResult(t["user"], t["reply"], tuple(t["tools"]), t["latency_s"])
            for t in record["turns"]
        ),
        escalations=tuple(record.get("escalations", ())),
        model_calls=record.get("model_calls", 0),
        failed_calls=record.get("failed_calls", 0),
        grounding_retries=record.get("grounding_retries", 0),
        safe_messages=record.get("safe_messages", 0),
        transcript=tuple(record.get("transcript", ())),
    )


def rescore_record(scenario: Scenario, record: dict[str, Any]) -> dict[str, Any]:
    """The record with its checks and verdict computed again by the current checks.

    A record without turns (the scenario crashed) has nothing to score and is returned as is.

    Raises:
        ValueError: If the scenario's messages are not the ones the record was played with.
    """
    if list(scenario.messages) != record["messages"]:
        raise ValueError(f"scenario {record['id']} has other messages than the saved run")
    if "turns" not in record:
        return record
    results = evaluate(run_from_record(record), scenario.checks)
    checks = [
        {"name": r.name, "level": r.level, "passed": r.passed}
        | ({"error": r.error} if r.error else {})
        for r in results
    ]
    return record | {"checks": checks, "passed": is_passed(results)}


def rescore_document(
    document: dict[str, Any], scenarios: Sequence[Scenario], *, today: str, checks_sha: str
) -> dict[str, Any]:
    """A complete result scored again by the current checks, with the changes logged.

    Args:
        document: A saved complete result.
        scenarios: The current scenarios (and so the current checks).
        today: Date of the re-scoring, ``YYYY-MM-DD``.
        checks_sha: ``evals.version.checks_version()`` of the current checks.

    Returns:
        The updated document: new checks, verdicts, totals and summary, plus ``checks_sha`` and a
        ``rescoring`` entry listing every check whose outcome changed. A document already scored
        by these checks, where nothing changes, is returned as it came.

    Raises:
        ValueError: If a saved scenario is gone or has other messages than the current one.
    """
    by_id = {s.id: s for s in scenarios}
    records, changes = [], []
    for record in document["scenarios"]:
        if record["id"] not in by_id:
            raise ValueError(f"scenario {record['id']} no longer exists")
        new = rescore_record(by_id[record["id"]], record)
        before = {c["name"]: c["passed"] for c in record["checks"]}
        changes += [
            {
                "scenario": record["id"],
                "check": c["name"],
                "level": c["level"],
                "before": before.get(c["name"]),
                "after": c["passed"],
            }
            for c in new["checks"]
            if before.get(c["name"]) != c["passed"]
        ]
        records.append(new)
    if document.get("checks_sha") == checks_sha and not changes:
        return document
    entry = {
        "date": today,
        "from_checks_sha": document.get("checks_sha"),
        "to_checks_sha": checks_sha,
        "changes": changes,
    }
    counts = totals(records)
    return document | {
        "checks_sha": checks_sha,
        "rescoring": [*document.get("rescoring", []), entry],
        "scenarios": records,
        "totals": counts,
        "summary": summary_line(document["model"], document["date"], counts, rescored=today),
    }


def _cell(record: dict[str, Any]) -> str:
    desirable = [c for c in record["checks"] if c["level"] == "desirable"]
    done = sum(1 for c in desirable if c["passed"])
    return f"{'PASS' if record['passed'] else 'FAIL'} {done}/{len(desirable)}"


def format_comparison(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Before/after table: critical verdict and desirable checks passed/total, per scenario."""
    old = {r["id"]: r for r in before["scenarios"]}
    lines = [f"{'id':<4} {'before':<9} {'after':<9}  scenario"]
    for record in after["scenarios"]:
        was, now = _cell(old[record["id"]]), _cell(record)
        mark = "  <- changed" if was != now else ""
        lines.append(f"{record['id']:<4} {was:<9} {now:<9}  {record['title']}{mark}")
    lines += ["", f"before: {before['summary']}", f"after:  {after['summary']}"]
    return "\n".join(lines)
