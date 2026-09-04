"""executor.py - The Co-Pilot Phase.

Compiles the LangGraph with a durable checkpointer (Cosmos-backed in Azure,
in-memory locally) and a Human-in-the-Loop (HITL) breakpoint
(``interrupt_before=["human_review"]``). It then drives a CLI loop: run until
the interrupt, prompt the user (Approve/Reject/Modify), patch the state with
``graph.update_state``, and resume - until the workflow completes.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from checkpointer import get_checkpointer
from models import GraphState, WorkflowSchema
from skill import Skill
from tools import is_high_risk

console = Console()


def compile_graph(graph: StateGraph) -> CompiledStateGraph:
    """Compile with durable persistence and a HITL breakpoint.

    Uses a Cosmos-backed checkpointer in Azure (so paused sessions survive
    restarts / scale-out) and an in-memory one locally.
    """
    return graph.compile(
        checkpointer=get_checkpointer(),
        interrupt_before=["human_review"],
    )


def _prompt_human(state: dict[str, Any]) -> dict[str, Any]:
    """Render the pending action and collect the human decision."""
    action = state.get("drafted_action", "(no action drafted)")

    console.print(
        Panel(
            f"[bold]Pending action awaiting your review:[/bold]\n\n{action}",
            border_style="yellow",
            title="⏸  HUMAN-IN-THE-LOOP BREAKPOINT",
        )
    )

    choice = Prompt.ask(
        "[bold yellow]Decision[/bold yellow]",
        choices=["approve", "reject", "modify"],
        default="approve",
    )

    feedback = ""
    if choice in ("reject", "modify"):
        feedback = Prompt.ask("[bold]Feedback for the redraft[/bold]")

    return {"human_decision": choice, "human_feedback": feedback}


def _current_step(values: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the step dict the graph is currently paused on, if any."""
    steps = values.get("workflow", {}).get("steps", [])
    idx = values.get("step_index", 0)
    return steps[idx] if 0 <= idx < len(steps) else None


def run_executor(
    schema: WorkflowSchema,
    compiled: CompiledStateGraph,
    *,
    skill: Optional[Skill] = None,
    auto_approve: bool = False,
    thread_id: Optional[str] = None,
    inputs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Drive the compiled graph to completion with terminal HITL.

    * ``auto_approve`` approves every step without prompting (used by the
      simulation harness).
    * When a ``skill`` marks the current step ``AUTONOMOUS``, the executor
      approves it automatically — supervision has been earned away.
    * ``inputs`` seeds runtime values used to fill templated exemplars.

    Returns the final graph state (including the full ``history`` trace).
    """
    # Unique thread per run so repeated simulations don't share checkpoints.
    thread_id = thread_id or f"session-{uuid.uuid4().hex[:8]}"
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

    initial_state: GraphState = {
        "workflow": schema.model_dump(),
        "step_index": 0,
        "history": [],
        "completed": False,
        "inputs": inputs or {},
    }

    console.print(
        Panel.fit(
            "[bold cyan]Co-Pilot Phase[/bold cyan]\n"
            "Running the agentic state machine with human oversight.",
            border_style="cyan",
            title="Executor",
        )
    )

    # First run: goes until the interrupt before human_review.
    inputs: Any = initial_state
    while True:
        # Stream/execute until the next interrupt (or completion).
        for _ in compiled.stream(inputs, config=config):
            pass

        snapshot = compiled.get_state(config)

        # If there are no more pending nodes, the run finished.
        if not snapshot.next:
            break

        # We're paused before human_review. Decide how to proceed.
        values = snapshot.values
        step = _current_step(values)
        step_id = step.get("id") if step else None

        if auto_approve:
            patch = {"human_decision": "approve", "human_feedback": ""}
        elif (
            skill is not None
            and step_id is not None
            and skill.is_autonomous(step_id)
            and not is_high_risk(step)
        ):
            console.print(
                Panel.fit(
                    f"Step {step.get('name', step_id)} is [bold]AUTONOMOUS[/bold], "
                    "auto-approved by the learned skill.",
                    border_style="green",
                    title="⚡ No human needed",
                )
            )
            patch = {"human_decision": "approve", "human_feedback": ""}
        else:
            patch = _prompt_human(values)

        compiled.update_state(config, patch)

        # Resume from the checkpoint (None means "continue where paused").
        inputs = None

    final_state = compiled.get_state(config).values
    console.print(
        Panel.fit(
            "[bold green]Workflow complete.[/bold green]",
            border_style="green",
            title="Executor",
        )
    )
    return final_state
