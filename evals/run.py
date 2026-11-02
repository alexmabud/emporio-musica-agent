"""Eval runner: ``python -m evals.run`` (or ``make eval``) calls the real model on every scenario.

Each scenario runs on a fresh conversation. The run is scored on trajectory (tools called) and key
facts in the reply, on two levels (see ``evals.checks``): a scenario **passes** when all its
critical checks hold; desirable checks are counted apart. Results go to
``evals/results/<model-slug>.json``.

A complete eval may not fit one day of a free tier, so progress is saved after every scenario in
``<model-slug>-partial.json`` and ``--resume`` continues it, as long as the prompt, the tool
schemas and the model are exactly the same (``evals.version``). When every scenario is done the
partial file becomes the model's full result.

``--rescore`` scores a saved complete result again with the current checks, from the transcripts
already saved and without calling the model (``evals.rescore``).
"""

import argparse
import json
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

from emporio_agent.agent import build_agent, create_model, is_daily_quota_error, reply_text
from emporio_agent.config import PROJECT_ROOT, ConfigError, Settings, load_settings
from emporio_agent.data_loader import build_database
from emporio_agent.policies import load_policy_text
from emporio_agent.tools import build_tools
from emporio_agent.validation import GroundingEvents
from evals.checks import CheckResult, Run, TurnResult, evaluate, is_passed
from evals.rescore import (
    format_comparison,
    rescore_document,
    rescore_record,
    summary_line,
    totals,
)
from evals.scenarios import SCENARIOS, Scenario
from evals.version import checks_version, compute_version, differences

RESULTS_DIR = PROJECT_ROOT / "evals" / "results"

AgentFactory = Callable[[Path], Any]


class CallCounter(BaseCallbackHandler):
    """Counts every request sent to the chat model, retries and regenerations included.

    Attributes:
        started: Requests sent.
        failed: Requests that raised an error.
    """

    def __init__(self) -> None:
        """Start with no requests counted."""
        self.started = 0
        self.failed = 0

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        """Count one request."""
        self.started += 1

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Count one failed request."""
        self.failed += 1


def model_slug(spec: str) -> str:
    """File-name-safe version of a model spec: ``:`` and ``/`` become ``-``."""
    return spec.replace(":", "-").replace("/", "-")


def fallback_specs(configured: Sequence[str], *, with_fallbacks: bool) -> tuple[str, ...]:
    """Reserve models to use: none by default, so every result belongs to a single model."""
    return tuple(configured) if with_fallbacks else ()


def _tools_called_in_turn(messages: list[Any]) -> tuple[str, ...]:
    """Tool names requested after the customer's last message, in order."""
    last_human = max(i for i, message in enumerate(messages) if message.type == "human")
    return tuple(
        call["name"]
        for message in messages[last_human:]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    )


_ROLES = {"human": "user", "ai": "assistant", "tool": "tool"}


def _transcript(messages: list[Any]) -> tuple[dict[str, Any], ...]:
    """The conversation as plain data: user, assistant (with tool calls) and tool messages."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = _ROLES.get(message.type)
        if role is None:
            continue
        item: dict[str, Any] = {"role": role, "content": message.text}
        if isinstance(message, AIMessage) and message.tool_calls:
            item["tool_calls"] = [
                {"name": c["name"], "args": c["args"]} for c in message.tool_calls
            ]
        if role == "tool":
            item["name"] = message.name
        items.append(item)
    return tuple(items)


def run_scenario(agent_factory: AgentFactory, scenario: Scenario, workdir: Path) -> Run:
    """Play a scenario on a fresh agent and conversation.

    Args:
        agent_factory: Builds an agent that logs escalations to the given file.
        scenario: The scenario to play.
        workdir: Folder for the scenario's escalation log.

    Returns:
        What the agent did on each turn, the escalation records it wrote, how many requests
        it sent to the model and the full transcript.
    """
    escalations_path = workdir / f"escalations-{scenario.id}.jsonl"
    agent = agent_factory(escalations_path)
    counter = CallCounter()
    config = {"configurable": {"thread_id": f"eval-{scenario.id}"}, "callbacks": [counter]}
    turns = []
    result: dict[str, Any] = {"messages": []}
    with GroundingEvents() as grounding:
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
    return Run(
        turns=tuple(turns),
        escalations=records,
        model_calls=counter.started,
        failed_calls=counter.failed,
        grounding_retries=grounding.events.count("grounding_retry"),
        safe_messages=grounding.events.count("grounding_safe_message"),
        transcript=_transcript(result["messages"]),
    )


def _scenario_record(
    scenario: Scenario,
    run: Run | None,
    results: list[CheckResult],
    error: str | None,
    stamp: str,
) -> dict[str, Any]:
    """The JSON-ready record of one scenario."""
    passed = error is None and is_passed(results)
    record: dict[str, Any] = {
        "id": scenario.id,
        "title": scenario.title,
        "messages": list(scenario.messages),
        "date": stamp,
        "passed": passed,
    }
    if run is not None:
        record["turns"] = [
            {"user": t.user, "reply": t.reply, "tools": list(t.tools), "latency_s": t.latency_s}
            for t in run.turns
        ]
        record["escalations"] = list(run.escalations)
        record["latency_s"] = round(sum(t.latency_s for t in run.turns), 1)
        record["model_calls"] = run.model_calls
        record["failed_calls"] = run.failed_calls
        record["grounding_retries"] = run.grounding_retries
        record["safe_messages"] = run.safe_messages
        record["checks"] = [
            {"name": r.name, "level": r.level, "passed": r.passed}
            | ({"error": r.error} if r.error else {})
            for r in results
        ]
        record["transcript"] = list(run.transcript)
    if error is not None:
        record["error"] = error
        record["checks"] = []
    return record


def format_table(records: list[dict[str, Any]]) -> str:
    """A plain-text pass/fail table, followed by every check that failed."""
    lines = [f"{'id':<4} {'result':<7} {'desirable':<10} {'calls':>5} {'secs':>7}  scenario"]
    failures = []
    for record in records:
        checks = record["checks"]
        desirable = [c for c in checks if c["level"] == "desirable"]
        done = sum(1 for c in desirable if c["passed"])
        result = "PASS" if record["passed"] else "FAIL"
        latency = record.get("latency_s", 0.0)
        calls = record.get("model_calls", 0)
        note = ""
        if record.get("grounding_retries") or record.get("safe_messages"):
            note = (
                f" (grounding: {record['grounding_retries']} retry, {record['safe_messages']} safe)"
            )
        lines.append(
            f"{record['id']:<4} {result:<7} {f'{done}/{len(desirable)}':<10} {calls:>5} "
            f"{latency:>7}  {record['title']}{note}"
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
) -> tuple[AgentFactory, dict[str, str]]:
    """Build the real agent factory and the version fingerprint of the agent it creates.

    Returns:
        The factory (chosen model, store data, policies) and the version (model, prompt and
        tool schemas) that saved results are tied to.
    """
    model = create_model(model_spec, settings.llm_temperature)
    fallbacks = [
        create_model(spec, settings.llm_temperature)
        for spec in fallback_specs(settings.llm_fallback_models, with_fallbacks=with_fallbacks)
    ]
    build_database(settings.data_dir, settings.db_path)
    policy = load_policy_text(settings.policy_pdf)
    version = compute_version(
        policy, build_tools(settings.db_path, settings.escalations_path), model_spec
    )

    def make(escalations_path: Path) -> Any:
        return build_agent(
            model,
            fallback_models=fallbacks,
            grounding_max_retries=settings.grounding_max_retries,
            db_path=settings.db_path,
            escalations_path=escalations_path,
            policy_text=policy,
        )

    return make, version


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
    parser.add_argument("--only", help="comma-separated scenario ids, e.g. 8,17 (a quick subset)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue the saved partial run (needs the same prompt, tool schemas and model)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="discard the saved partial run and begin a new round on the current version",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="score the saved complete result again with the current checks, without calling "
        "the model (the transcripts are kept; the changes are logged in the file)",
    )
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if args.rescore and (args.only or args.resume or args.restart):
        parser.error("--rescore only re-scores the saved result; no other run option applies")
    if args.only and (args.resume or args.restart):
        parser.error("--only runs a quick subset and cannot be combined with --resume/--restart")
    if args.resume and args.restart:
        parser.error("--resume and --restart contradict each other")
    return args


def _write_json(path: Path, document: dict[str, Any]) -> None:
    """Write atomically, so a crash never leaves a half-written results file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_progress(
    partial_path: Path, args: argparse.Namespace, version: dict[str, str]
) -> dict[str, Any] | None:
    """The saved partial run to continue, or ``None`` to begin a new one.

    Raises:
        SystemExit: If a partial run exists and neither ``--resume`` nor ``--restart`` was given,
            or if it was produced by a different prompt, tool schemas or model.
    """
    if not partial_path.exists() or args.restart:
        return None
    if not args.resume:
        raise SystemExit(
            f"A partial run is saved in {partial_path.name}. Pass --resume to continue it or "
            "--restart to discard it and begin again."
        )
    saved = json.loads(partial_path.read_text(encoding="utf-8"))
    changed = differences(saved.get("fingerprint", {}), version)
    if changed:
        raise SystemExit(
            f"Cannot resume: the {', '.join(changed)} changed since {partial_path.name} was saved, "
            "so its results do not describe the current agent. Use --restart to begin a new round "
            "on the current version."
        )
    return saved


def _rescore(args: argparse.Namespace, scenarios: Sequence[Scenario]) -> int:
    """Score the model's saved complete result again with the current checks; print before/after."""
    spec = args.model
    if spec is None:
        load_dotenv()
        try:
            spec = load_settings(os.environ).llm_model
        except ConfigError as error:
            raise SystemExit(f"Configuration error: {error}") from error
    path = (
        args.results_dir
        / f"{model_slug(spec)}{'-with-fallbacks' if args.with_fallbacks else ''}.json"
    )
    if not path.exists():
        raise SystemExit(f"Nothing to re-score: there is no complete result in {path}")
    before = json.loads(path.read_text(encoding="utf-8"))
    try:
        after = rescore_document(
            before, scenarios, today=date.today().isoformat(), checks_sha=checks_version()
        )
    except ValueError as error:
        raise SystemExit(f"Cannot re-score {path.name}: {error}. Run the eval again.") from error
    if after is before:
        print(f"{path.name} was already scored by these checks; nothing changed.")
        return 0
    _write_json(path, after)
    print(format_comparison(before, after))
    print(f"\nSaved to {path}")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    agent_factory: AgentFactory | None = None,
    version: dict[str, str] | None = None,
    scenarios: Sequence[Scenario] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the scenarios, print the table and save the results.

    Args:
        argv: Command-line arguments (``sys.argv[1:]`` when omitted).
        agent_factory: Builds the agent per scenario; tests inject a scripted one.
        version: Fingerprint of the agent (model, prompt, tool schemas); computed from the real
            agent when omitted. Tests inject one.
        scenarios: The scenarios to run (all of them by default); tests inject a few.
        sleep: Used for ``--pause``; tests inject a recorder.

    Returns:
        ``0`` when the run finished, ``1`` when it stopped early on an exhausted daily quota.
    """
    args = _parse(argv)
    everything = tuple(SCENARIOS if scenarios is None else scenarios)
    wanted = [item.strip() for item in args.only.split(",")] if args.only else []
    unknown = sorted(set(wanted) - {s.id for s in everything})
    if unknown:
        raise SystemExit(f"Unknown scenario id(s): {', '.join(unknown)}")
    subset = bool(wanted)
    selected = [s for s in everything if not wanted or s.id in wanted]

    settings: Settings | None = None
    if args.rescore:
        return _rescore(args, everything)
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
        agent_factory, version = build_agent_factory(
            settings, model_spec, with_fallbacks=args.with_fallbacks
        )
    version = version or {"model": model_spec, "prompt_sha": "-", "tools_sha": "-"}

    base = f"{model_slug(model_spec)}{'-with-fallbacks' if args.with_fallbacks else ''}"
    full_path = args.results_dir / f"{base}.json"
    partial_path = args.results_dir / f"{base}-partial.json"
    subset_path = args.results_dir / f"{base}-subset.json"

    saved = None if subset else _load_progress(partial_path, args, version)
    texts = {s.id: list(s.messages) for s in selected}
    by_id = {s.id: s for s in everything}
    kept = [
        rescore_record(by_id[r["id"]], r)  # kept results are scored by the checks of this run
        for r in (saved["scenarios"] if saved else [])
        if texts.get(r["id"]) == r["messages"]
    ]
    sessions: list[dict[str, Any]] = list(saved["sessions"]) if saved else []
    records: list[dict[str, Any]] = list(kept)
    order = {s.id: position for position, s in enumerate(everything)}
    stamp = date.today().isoformat()

    def document(*, partial: bool, aborted: bool) -> dict[str, Any]:
        ordered = sorted(records, key=lambda r: order[r["id"]])
        counts = totals(ordered)
        remaining = [s.id for s in selected if s.id not in {r["id"] for r in records}]
        summary = summary_line(model_spec, stamp, counts)
        return {
            "model": model_spec,
            "date": stamp,
            "fingerprint": version,
            "with_fallbacks": args.with_fallbacks,
            "checks_sha": checks_version(),
            "partial": partial,
            "aborted": aborted,
            "remaining": remaining,
            "summary": summary,
            "totals": counts,
            "sessions": sessions,
            "scenarios": ordered,
        }

    todo = [s for s in selected if s.id not in {r["id"] for r in records}]
    session: dict[str, Any] = {"date": stamp, "scenarios": [], "model_calls": 0, "failed_calls": 0}
    aborted = False
    with tempfile.TemporaryDirectory() as workdir:
        for position, scenario in enumerate(todo):
            if position and args.pause > 0:
                sleep(args.pause)
            run: Run | None = None
            results: list[CheckResult] = []
            error: str | None = None
            try:
                run = run_scenario(agent_factory, scenario, Path(workdir))
                results = evaluate(run, scenario.checks)
            except Exception as failure:
                if is_daily_quota_error(failure):
                    aborted = True
                    print(
                        f"[{position + 1}/{len(todo)}] {scenario.id} NOT DONE: daily quota",
                        flush=True,
                    )
                    break
                error = f"{type(failure).__name__}: {failure}"
            record = _scenario_record(scenario, run, results, error, stamp)
            records.append(record)
            session["scenarios"].append(scenario.id)
            session["model_calls"] += record.get("model_calls", 0)
            session["failed_calls"] += record.get("failed_calls", 0)
            print(
                f"[{position + 1}/{len(todo)}] {scenario.id} "
                f"{'PASS' if record['passed'] else 'FAIL'}",
                flush=True,
            )
            if not subset:
                _write_json(partial_path, document(partial=True, aborted=False))

    if session["scenarios"]:
        sessions.append(session)
    unfinished = any(s.id not in {r["id"] for r in records} for s in selected)
    final = document(partial=subset or unfinished, aborted=aborted)
    if subset:
        target = subset_path
    elif not final["remaining"]:
        target = full_path
        partial_path.unlink(missing_ok=True)
    else:
        target = partial_path
    if records:
        _write_json(target, final)

    print("\n" + format_table(final["scenarios"]))
    print(f"\n{final['summary']}")
    if records:
        print(f"Saved to {target}")
    if final["remaining"] and not subset:
        print(f"Remaining ({len(final['remaining'])}): {', '.join(final['remaining'])}")
    if aborted:
        print("Stopped early: the daily quota of the model is used up. Run again with --resume.")
    return 1 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
