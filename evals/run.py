"""Eval runner: ``python -m evals.run`` (or ``make eval``) calls the real model on every scenario.

Each scenario runs on a fresh conversation. The run is scored on trajectory (tools called) and key
facts in the reply, on two levels (see ``evals.checks``): a scenario **passes** when all its
critical checks hold; desirable checks are counted apart. Results go to
``evals/results/<model-slug>.json``.
"""

import argparse
import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage

from emporio_agent.agent import build_agent, create_model, is_daily_quota_error, reply_text
from emporio_agent.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from emporio_agent.data_loader import build_database
from emporio_agent.policies import load_policy_text
from evals.checks import Check, Level, Run, TurnResult
from evals.scenarios import SCENARIOS, Scenario

RESULTS_DIR = PROJECT_ROOT / "evals" / "results"

AgentFactory = Callable[[Path], Any]


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check.

    Attributes:
        name: What the check verifies.
        level: ``critical`` or ``desirable``.
        passed: Whether it held.
        error: Why the check itself crashed, if it did (counts as not passed).
    """

    name: str
    level: Level
    passed: bool
    error: str | None = None


def model_slug(spec: str) -> str:
    """File-name-safe version of a model spec: ``:`` and ``/`` become ``-``."""
    return spec.replace(":", "-").replace("/", "-")


def fallback_specs(configured: Sequence[str], *, with_fallbacks: bool) -> tuple[str, ...]:
    """Reserve models to use: none by default, so every result belongs to a single model."""
    return tuple(configured) if with_fallbacks else ()


def evaluate(run: Run, checks: Sequence[Check]) -> list[CheckResult]:
    """Score a run. A check that crashes counts as failed and never stops the others."""
    results = []
    for check in checks:
        try:
            results.append(CheckResult(check.name, check.level, bool(check.test(run))))
        except Exception as error:
            results.append(CheckResult(check.name, check.level, False, repr(error)))
    return results


def _tools_called_in_turn(messages: list[Any]) -> tuple[str, ...]:
    """Tool names requested after the customer's last message, in order."""
    last_human = max(i for i, message in enumerate(messages) if message.type == "human")
    return tuple(
        call["name"]
        for message in messages[last_human:]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    )


def run_scenario(agent_factory: AgentFactory, scenario: Scenario, workdir: Path) -> Run:
    """Play a scenario on a fresh agent and conversation.

    Args:
        agent_factory: Builds an agent that logs escalations to the given file.
        scenario: The scenario to play.
        workdir: Folder for the scenario's escalation log.

    Returns:
        What the agent did on each turn, plus the escalation records it wrote.
    """
    escalations_path = workdir / f"escalations-{scenario.id}.jsonl"
    agent = agent_factory(escalations_path)
    config = {"configurable": {"thread_id": f"eval-{scenario.id}"}}
    turns = []
    for message in scenario.messages:
        started = time.perf_counter()
        result = agent.invoke({"messages": [("user", message)]}, config=config)
        turns.append(
            TurnResult(
                user=message,
                reply=reply_text(result),
                tools=_tools_called_in_turn(result["messages"]),
                latency_s=round(time.perf_counter() - started, 1),
            )
        )
    records: tuple[dict[str, Any], ...] = ()
    if escalations_path.exists():
        lines = escalations_path.read_text(encoding="utf-8").splitlines()
        records = tuple(json.loads(line) for line in lines if line.strip())
    return Run(turns=tuple(turns), escalations=records)


def _scenario_record(
    scenario: Scenario, run: Run | None, results: list[CheckResult], error: str | None
) -> dict[str, Any]:
    """The JSON-ready record of one scenario."""
    passed = error is None and all(r.passed for r in results if r.level == "critical")
    record: dict[str, Any] = {"id": scenario.id, "title": scenario.title, "passed": passed}
    if run is not None:
        record["turns"] = [
            {"user": t.user, "reply": t.reply, "tools": list(t.tools), "latency_s": t.latency_s}
            for t in run.turns
        ]
        record["escalations"] = list(run.escalations)
        record["latency_s"] = round(sum(t.latency_s for t in run.turns), 1)
        record["checks"] = [
            {"name": r.name, "level": r.level, "passed": r.passed}
            | ({"error": r.error} if r.error else {})
            for r in results
        ]
    if error is not None:
        record["error"] = error
        record["checks"] = []
    return record


def _totals(records: list[dict[str, Any]]) -> dict[str, int]:
    checks = [c for record in records for c in record["checks"]]
    desirable = [c for c in checks if c["level"] == "desirable"]
    return {
        "scenarios_run": len(records),
        "scenarios_passed": sum(1 for record in records if record["passed"]),
        "desirable_passed": sum(1 for c in desirable if c["passed"]),
        "desirable_total": len(desirable),
    }


def format_table(records: list[dict[str, Any]]) -> str:
    """A plain-text pass/fail table, followed by every check that failed."""
    lines = [f"{'id':<4} {'result':<7} {'desirable':<10} {'secs':>6}  scenario"]
    failures = []
    for record in records:
        checks = record["checks"]
        desirable = [c for c in checks if c["level"] == "desirable"]
        done = sum(1 for c in desirable if c["passed"])
        result = "PASS" if record["passed"] else "FAIL"
        latency = record.get("latency_s", 0.0)
        lines.append(
            f"{record['id']:<4} {result:<7} {f'{done}/{len(desirable)}':<10} {latency:>6}  "
            f"{record['title']}"
        )
        if "error" in record:
            failures.append(f"  {record['id']}: ERROR {record['error']}")
        failures += [
            f"  {record['id']}: [{c['level']}] {c['name']}" for c in checks if not c["passed"]
        ]
    if failures:
        lines += ["", "Checks that failed:", *failures]
    return "\n".join(lines)


def build_agent_factory(
    settings: Settings, model_spec: str, *, with_fallbacks: bool
) -> AgentFactory:
    """Build the real agent factory: the chosen model, the store data and the policies."""
    model = create_model(model_spec, settings.llm_temperature)
    fallbacks = [
        create_model(spec, settings.llm_temperature)
        for spec in fallback_specs(settings.llm_fallback_models, with_fallbacks=with_fallbacks)
    ]
    build_database(settings.data_dir, settings.db_path)
    policy = load_policy_text(settings.policy_pdf)

    def make(escalations_path: Path) -> Any:
        return build_agent(
            model,
            fallback_models=fallbacks,
            grounding_max_retries=settings.grounding_max_retries,
            db_path=settings.db_path,
            escalations_path=escalations_path,
            policy_text=policy,
        )

    return make


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="evals.run", description=__doc__)
    parser.add_argument(
        "--model", help="model spec, e.g. groq:openai/gpt-oss-120b (default: LLM_MODEL)"
    )
    parser.add_argument(
        "--with-fallbacks",
        action="store_true",
        help="use LLM_FALLBACK_MODELS too (a resilience run; results then belong to a chain)",
    )
    parser.add_argument(
        "--pause", type=float, default=0.0, help="seconds to wait between scenarios (free tiers)"
    )
    parser.add_argument("--only", help="comma-separated scenario ids, e.g. 8,17 (partial run)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    agent_factory: AgentFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the scenarios, print the table and save the results file.

    Args:
        argv: Command-line arguments (``sys.argv[1:]`` when omitted).
        agent_factory: Builds the agent per scenario; tests inject a scripted one.
        sleep: Used for ``--pause``; tests inject a recorder.

    Returns:
        ``0`` when the run finished, ``1`` when it stopped early on an exhausted daily quota.
    """
    args = _parse(argv)
    wanted = [item.strip() for item in args.only.split(",")] if args.only else []
    unknown = sorted(set(wanted) - {s.id for s in SCENARIOS})
    if unknown:
        raise SystemExit(f"Unknown scenario id(s): {', '.join(unknown)}")
    scenarios = [s for s in SCENARIOS if not wanted or s.id in wanted]

    settings: Settings | None = None
    if agent_factory is None:
        load_dotenv()
        try:
            settings = load_settings(os.environ)
        except ConfigError as error:
            raise SystemExit(f"Configuration error: {error}") from error
    model_spec = args.model or (settings.llm_model if settings else None)
    if model_spec is None:
        raise SystemExit("--model is required when no agent is injected")
    if agent_factory is None:
        assert settings is not None
        agent_factory = build_agent_factory(
            settings, model_spec, with_fallbacks=args.with_fallbacks
        )

    records: list[dict[str, Any]] = []
    aborted = False
    with tempfile.TemporaryDirectory() as workdir:
        for position, scenario in enumerate(scenarios):
            if position and args.pause > 0:
                sleep(args.pause)
            run: Run | None = None
            results: list[CheckResult] = []
            error: str | None = None
            try:
                run = run_scenario(agent_factory, scenario, Path(workdir))
                results = evaluate(run, scenario.checks)
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
                aborted = is_daily_quota_error(failure)
            records.append(_scenario_record(scenario, run, results, error))
            print(
                f"[{position + 1}/{len(scenarios)}] {scenario.id} "
                f"{'PASS' if records[-1]['passed'] else 'FAIL'}",
                flush=True,
            )
            if aborted:
                break

    totals = _totals(records)
    partial = len(scenarios) != len(SCENARIOS) or aborted
    stamp = date.today().isoformat()
    summary = (
        f"`{model_spec}` · {stamp} · critical {totals['scenarios_passed']}/"
        f"{totals['scenarios_run']} scenarios passed · desirable "
        f"{totals['desirable_passed']}/{totals['desirable_total']} checks"
    )
    document = {
        "model": model_spec,
        "date": stamp,
        "with_fallbacks": args.with_fallbacks,
        "partial": partial,
        "aborted": aborted,
        "summary": summary,
        "totals": totals,
        "scenarios": records,
    }
    suffix = ("-with-fallbacks" if args.with_fallbacks else "") + ("-partial" if partial else "")
    target = args.results_dir / f"{model_slug(model_spec)}{suffix}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n" + format_table(records))
    print(f"\n{summary}\nSaved to {target}")
    if aborted:
        print("Stopped early: the daily quota of the model is used up.")
    return 1 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
