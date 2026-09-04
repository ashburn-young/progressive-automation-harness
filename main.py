"""main.py — The Orchestrator & Logging.

Wires the three phases together:

    1. Interview the user  -> WorkflowSchema  (interviewer.py)
    2. Build a StateGraph  from the schema    (graph_builder.py)
    3. Execute with HITL   in the terminal    (executor.py)

Post-execution, every step whose drafted action was approved is appended to
``dspy_training_logs.jsonl``. That file is the dataset for a *future*
deterministic-hardening pass: once enough approved traces accumulate, they can
be compiled with DSPy into optimized, deterministic programs that replace the
LLM-in-the-loop drafting — progressively automating the workflow.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from executor import compile_graph, run_executor
from graph_builder import build_graph
from interviewer import demo_schema, run_interview_safe
from llm_provider import is_offline
from models import WorkflowSchema
from skill import Maturity, Skill
from skill_compiler import compile_skill_from_logs

console = Console()

# Append-only dataset consumed by the skill compiler (deterministic hardening).
BASE_DIR = Path(__file__).parent
TRAINING_LOG = BASE_DIR / "dspy_training_logs.jsonl"
SKILLS_DIR = BASE_DIR / "skills"


def _extract_approved_traces(
    schema: WorkflowSchema, final_state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Reconstruct (request, draft, feedback, execution) tuples for approvals.

    We walk the ordered ``history`` trace and pair each ``draft`` with the
    ``human_review`` and ``execute`` events for the same step. Only steps that
    were ultimately approved and executed are emitted.
    """
    history: list[dict[str, Any]] = final_state.get("history", [])

    # Latest draft per step index (redrafts overwrite earlier attempts).
    latest_draft: dict[int, str] = {}
    latest_feedback: dict[int, str] = {}
    approved: set[int] = set()
    executions: dict[int, str] = {}
    exec_status: dict[int, str] = {}
    executed_action: dict[int, str] = {}

    for event in history:
        idx = event.get("step_index")
        kind = event.get("event")
        if kind == "draft":
            latest_draft[idx] = event.get("action", "")
        elif kind == "human_review":
            if event.get("decision") == "approve":
                approved.add(idx)
            if event.get("feedback"):
                latest_feedback[idx] = event["feedback"]
        elif kind == "execute":
            executions[idx] = event.get("result", "")
            exec_status[idx] = event.get("status", "success")
            if event.get("action"):
                executed_action[idx] = event["action"]

    traces: list[dict[str, Any]] = []
    for idx in sorted(approved):
        if idx not in executions:
            continue
        step = schema.steps[idx]
        traces.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_name": schema.task_name,
                "outcome": "approved",
                "inputs": final_state.get("inputs", {}),
                "original_request": {
                    "step_id": step.id,
                    "step_name": step.name,
                    "step_description": step.description,
                    "required_inputs": schema.required_inputs,
                },
                # Learn what was actually executed (captures human edits),
                # falling back to the last drafted action.
                "drafted_action": executed_action.get(idx, latest_draft.get(idx, "")),
                "human_feedback": latest_feedback.get(idx, ""),
                "final_execution": executions[idx],
                "execution_status": exec_status.get(idx, "success"),
            }
        )
    return traces


def log_successful_run(schema: WorkflowSchema, final_state: dict[str, Any]) -> int:
    """Append approved-step traces to the DSPy training log. Returns count."""
    traces = _extract_approved_traces(schema, final_state)
    if not traces:
        console.print("[yellow]No approved steps to log.[/yellow]")
        return 0

    with TRAINING_LOG.open("a", encoding="utf-8") as fh:
        for trace in traces:
            fh.write(json.dumps(trace, ensure_ascii=False) + "\n")

    console.print(
        Panel.fit(
            f"Appended [bold]{len(traces)}[/bold] approved trace(s) to\n"
            f"[cyan]{TRAINING_LOG.name}[/cyan]\n\n"
            "[dim]This file is the dataset for future DSPy compilation and\n"
            "deterministic hardening of the workflow.[/dim]",
            border_style="green",
            title="Logging",
        )
    )
    return len(traces)


def main() -> None:
    """Run the full Progressive Automation Harness pipeline."""
    # override=True so .env wins over any stale/empty shell env vars.
    load_dotenv(override=True)
    args = _parse_args()

    console.print(
        Panel.fit(
            "[bold]Progressive Automation Harness[/bold]\n"
            "Interview → Generate → Co-Pilot → Learn a Skill",
            border_style="bold blue",
        )
    )

    if args.simulate:
        simulate(args.task, runs=args.simulate)
        return

    if args.show_skill:
        _show_existing_skill(args.task)
        return

    if is_offline():
        console.print(
            "[yellow]No LLM configured. Running in offline demo mode.[/yellow]"
        )

    # 1. Extraction
    schema = run_interview_safe(demo=args.task)

    # Load any previously learned skill so drafting benefits immediately.
    skill = Skill.load_for_task(schema.task_name, SKILLS_DIR)

    # 2. Generation (skill-aware)
    graph = build_graph(schema, skill=skill)
    compiled = compile_graph(graph)

    # 3. Co-Pilot execution with HITL
    final_state = run_executor(
        schema, compiled, skill=skill, auto_approve=args.auto
    )

    # 4. Log approvals, then (re)compile the skill and show its maturity.
    log_successful_run(schema, final_state)
    skill = _recompile_and_report(schema)


def simulate(task: str, runs: int) -> None:
    """Run the workflow ``runs`` times auto-approving, watching the skill mature.

    This is the offline, no-key demo: each run appends approved traces, the
    skill is recompiled, and steps climb COLD → PRIMED → DETERMINISTIC →
    AUTONOMOUS until the executor stops asking for approval.
    """
    schema = demo_schema(task)
    console.print(
        Panel.fit(
            f"[bold]Simulation[/bold]: {runs} auto-approved run(s) of "
            f"'{schema.task_name}'.\nWatch the skill graduate to autonomy.",
            border_style="cyan",
        )
    )

    skill: Optional[Skill] = Skill.load_for_task(schema.task_name, SKILLS_DIR)
    for run_no in range(1, runs + 1):
        console.rule(f"[bold]Run {run_no}/{runs}[/bold]")
        graph = build_graph(schema, skill=skill)
        compiled = compile_graph(graph)
        final_state = run_executor(
            schema, compiled, skill=skill, auto_approve=True
        )
        log_successful_run(schema, final_state)
        skill = _recompile_and_report(schema)

    console.print(
        Panel.fit(
            f"Final skill maturity: [bold]{skill.overall_maturity().value.upper()}[/bold]"
            if skill
            else "No skill produced.",
            border_style="green",
            title="Simulation complete",
        )
    )


def _recompile_and_report(schema: WorkflowSchema) -> Skill:
    """Rebuild the skill from the log and print its per-step maturity."""
    skill = compile_skill_from_logs(
        schema.task_name,
        TRAINING_LOG,
        SKILLS_DIR,
        summary=schema.summary,
        required_inputs=schema.required_inputs,
    )
    _print_report(skill)
    return skill


def _print_report(skill: Skill) -> None:
    table = Table(title=f"Skill: {skill.task_name}  (v{skill.version})")
    table.add_column("Step", justify="right")
    table.add_column("Name")
    table.add_column("Approvals", justify="right")
    table.add_column("Maturity")

    colors = {
        Maturity.COLD.value: "red",
        Maturity.PRIMED.value: "yellow",
        Maturity.DETERMINISTIC.value: "cyan",
        Maturity.AUTONOMOUS.value: "green",
    }
    for row in skill.report_rows():
        mat = str(row["maturity"])
        table.add_row(
            str(row["step_id"]),
            str(row["step_name"]),
            str(row["approvals"]),
            f"[{colors.get(mat, 'white')}]{mat}[/]",
        )
    console.print(table)
    console.print(
        f"[dim]Saved to skills/{skill.slug}.json, "
        f"overall: {skill.overall_maturity().value}[/dim]"
    )


def _show_existing_skill(task: str) -> None:
    schema = demo_schema(task)
    skill = Skill.load_for_task(schema.task_name, SKILLS_DIR)
    if skill is None:
        console.print(
            f"[yellow]No skill learned yet for '{schema.task_name}'. "
            "Run a simulation first.[/yellow]"
        )
        return
    _print_report(skill)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Progressive Automation Harness")
    parser.add_argument(
        "--task",
        choices=["stock", "expense"],
        default="stock",
        help="Which demo workflow to use in offline mode (default: stock).",
    )
    parser.add_argument(
        "--simulate",
        type=int,
        metavar="N",
        help="Auto-approve N runs and watch the skill mature to autonomy.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-approve every step in a single run (no prompts).",
    )
    parser.add_argument(
        "--show-skill",
        action="store_true",
        help="Print the currently learned skill for the task and exit.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
