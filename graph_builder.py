"""graph_builder.py — The Generation Phase.

Accepts a :class:`WorkflowSchema` and dynamically constructs a LangGraph
``StateGraph`` with three standard nodes:

    draft_action  ->  human_review  ->  execute_action

Routing:
    * ``draft_action``  always flows to ``human_review``.
    * ``human_review``  is a HITL breakpoint; based on the human decision it
      routes to ``execute_action`` (approve) or back to ``draft_action``
      (reject/modify) carrying feedback.
    * ``execute_action`` advances to the next step, looping until the workflow
      is complete.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from rich.console import Console
from rich.panel import Panel

from llm_provider import build_chat_model, is_offline
from models import GraphState, WorkflowSchema
import content_safety
import retrieval
from skill import Exemplar, Skill
from telemetry import span
from tools import classify_execution, execute_step

console = Console()


# ---------------------------------------------------------------------------
# Drafting helpers
# ---------------------------------------------------------------------------
def _llm_draft(
    workflow: dict[str, Any],
    step: dict[str, Any],
    feedback: str,
    exemplars: list[Exemplar],
    inputs: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> str:
    """Draft a fresh action with the LLM, or a deterministic mock offline."""
    if is_offline():
        base = f"Proposed action for step '{step['name']}': {step['description']}"
        if feedback:
            base += f" (revised per feedback: {feedback})"
        return base

    llm = build_chat_model(temperature=0.2, deployment=model)
    primer = ""
    if exemplars:
        joined = "\n".join(f"- {e.action}" for e in exemplars[-3:])
        primer = (
            f"\nHuman-approved examples for this step:\n{joined}\n"
            "Stay consistent with them."
        )
    inputs_line = ""
    if inputs:
        pairs = ", ".join(f"{k}={v}" for k, v in inputs.items())
        inputs_line = f"\nRun inputs: {pairs}. Use these concrete values."
    # RAG grounding: retrieve organisational context for this step (#6).
    grounding = ""
    try:
        snippets = retrieval.retrieve(f"{step['name']}: {step['description']}")
    except Exception:
        snippets = []
    if snippets:
        joined = "\n".join(f"- {s}" for s in snippets)
        grounding = (
            f"\nRelevant organisational knowledge (follow it):\n{joined}"
        )
    revision = (
        f"\nThe human rejected the previous draft with feedback: {feedback}\n"
        "Incorporate it."
        if feedback
        else ""
    )
    prompt = [
        SystemMessage(
            content="You draft a single concrete next action for one workflow "
            "step. Return one short paragraph describing exactly what you will do."
        ),
        HumanMessage(
            content=f"Task: {workflow['task_name']}\n"
            f"Step {step['id']}: {step['name']}: {step['description']}\n"
            f"Condition: {step.get('condition') or 'none'}"
            f"{inputs_line}{grounding}{primer}{revision}"
        ),
    ]
    return (llm.invoke(prompt).content or "").strip()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Node factories (closures capture the optional learned skill)
# ---------------------------------------------------------------------------
def _make_draft_action(
    skill: Optional[Skill], model: Optional[str] = None
) -> Callable[[GraphState], dict[str, Any]]:
    """Build the draft node; consults ``skill`` before falling back to the LLM."""

    def draft_action(state: GraphState) -> dict[str, Any]:
        workflow = state["workflow"]
        idx = state.get("step_index", 0)
        step = workflow["steps"][idx]
        feedback = state.get("human_feedback") or ""
        inputs = state.get("inputs") or {}

        def llm_draft(exemplars: list[Exemplar]) -> str:
            return _llm_draft(workflow, step, feedback, exemplars, inputs, model)

        with span(
            "draft_step",
            **{"step.id": step["id"], "step.name": step["name"]},
        ) as current:
            if skill is not None:
                action, source = skill.draft_step(
                    step["id"], is_offline(), llm_draft, feedback, inputs
                )
            else:
                action = llm_draft([])
                source = "mock" if is_offline() else "llm"
            if current is not None:
                try:
                    current.set_attribute("draft.source", source)
                except Exception:
                    pass

        console.print(
            Panel(
                action,
                border_style="magenta",
                title=f"Drafted Action · step {idx + 1} · [dim]{source}[/dim]",
            )
        )

        history = state.get("history", []) + [
            {
                "event": "draft",
                "step_index": idx,
                "step_id": step["id"],
                "step_name": step["name"],
                "action": action,
                "source": source,
            }
        ]
        # Clear any consumed feedback now that we've redrafted.
        return {"drafted_action": action, "history": history, "human_feedback": ""}

    return draft_action


def _make_human_review() -> Callable[[GraphState], dict[str, Any]]:
    def human_review(state: GraphState) -> dict[str, Any]:
        """HITL breakpoint node — records the decision written by the executor."""
        decision = state.get("human_decision", "approve")
        idx = state.get("step_index", 0)
        history = state.get("history", []) + [
            {
                "event": "human_review",
                "step_index": idx,
                "decision": decision,
                "feedback": state.get("human_feedback", ""),
            }
        ]
        return {"history": history}

    return human_review


def _make_execute_action(model: Optional[str] = None) -> Callable[[GraphState], dict[str, Any]]:
    def execute_action(state: GraphState) -> dict[str, Any]:
        """Execute the approved action via the real tool layer and advance."""
        idx = state.get("step_index", 0)
        steps = state["workflow"]["steps"]
        step = steps[idx]

        with span(
            "execute_step",
            **{"step.id": step["id"], "step.name": step["name"]},
        ) as current:
            # Governance gate: screen the action before any side effect (#10).
            safety = content_safety.screen(state["drafted_action"])
            if not safety["allowed"]:
                result = (
                    f"[blocked] Content Safety flagged this action "
                    f"(severity {safety['max_severity']}). A human must review "
                    "it; not executed."
                )
                status = "blocked"
            else:
                result = execute_step(step, state["drafted_action"], state.get("inputs"), model)
                status = classify_execution(result)
            if current is not None:
                try:
                    current.set_attribute("execution.status", status)
                    current.set_attribute("safety.checked", safety["checked"])
                    current.set_attribute("safety.severity", safety["max_severity"])
                except Exception:
                    pass
        colors = {
            "success": "green",
            "failed": "red",
            "manual": "yellow",
            "blocked": "red",
        }
        border = colors.get(status, "green")
        console.print(
            Panel(result, border_style=border, title=f"Executed · {status}")
        )

        next_index = idx + 1
        completed = next_index >= len(steps)
        history = state.get("history", []) + [
            {
                "event": "execute",
                "step_index": idx,
                "step_id": step["id"],
                "step_name": step["name"],
                "action": state.get("drafted_action", ""),
                "result": result,
                "status": status,
                "safety": safety,
            }
        ]
        return {
            "execution_result": result,
            "step_index": next_index,
            "completed": completed,
            "history": history,
        }

    return execute_action


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
def _route_after_review(state: GraphState) -> str:
    """Approve -> execute; reject/modify -> redraft."""
    return (
        "execute_action"
        if state.get("human_decision") == "approve"
        else "draft_action"
    )


def _route_after_execute(state: GraphState) -> str:
    """Loop to the next step or finish."""
    return END if state.get("completed") else "draft_action"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def build_graph(
    schema: WorkflowSchema,
    skill: Optional[Skill] = None,
    model: Optional[str] = None,
) -> StateGraph:
    """Dynamically construct the workflow ``StateGraph`` from a schema.

    When ``skill`` is provided, the draft node consults it first, drafting
    deterministically for steps the skill has already mastered. ``model``
    selects which chat deployment drafts cold/novel steps.

    Returns the *uncompiled* graph so the executor can compile it with a
    checkpointer and interrupt configuration.
    """
    if not schema.steps:
        raise ValueError("Workflow schema must contain at least one step.")

    graph = StateGraph(GraphState)

    graph.add_node("draft_action", _make_draft_action(skill, model))
    graph.add_node("human_review", _make_human_review())
    graph.add_node("execute_action", _make_execute_action(model))

    graph.set_entry_point("draft_action")

    # draft -> review (unconditional)
    graph.add_edge("draft_action", "human_review")

    # review -> execute | draft (based on human decision)
    graph.add_conditional_edges(
        "human_review",
        _route_after_review,
        {"execute_action": "execute_action", "draft_action": "draft_action"},
    )

    # execute -> draft (next step) | END
    graph.add_conditional_edges(
        "execute_action",
        _route_after_execute,
        {"draft_action": "draft_action", END: END},
    )

    console.print(
        f"[dim]Built StateGraph for '{schema.task_name}' with "
        f"{len(schema.steps)} step(s).[/dim]"
    )
    return graph
