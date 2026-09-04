"""Shared data models for the Progressive Automation Harness.

These types are used across the interview, graph-building, and execution
phases so that a single source of truth defines the workflow contract.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class WorkflowStep(BaseModel):
    """A single sequential step in the extracted workflow."""

    id: int = Field(..., description="1-based ordinal position of the step.")
    name: str = Field(..., description="Short human-readable name of the step.")
    description: str = Field(
        ..., description="What this step does and why it is needed."
    )
    # Free-form condition that gates whether the step should run.
    condition: Optional[str] = Field(
        default=None,
        description="Optional condition that must hold for the step to run.",
    )
    human_required: bool = Field(
        default=False,
        description=(
            "True for irreversible or high-consequence steps (submitting, "
            "sending, paying, transferring, deleting) that must always be "
            "reviewed by a human, even once the skill is mature."
        ),
    )


class WorkflowSchema(BaseModel):
    """Structured contract produced by the interview phase.

    This is the JSON schema referenced throughout the harness. It is emitted
    by ``interviewer.py`` and consumed by ``graph_builder.py``.
    """

    task_name: str = Field(..., description="Name of the repetitive task.")
    summary: str = Field(..., description="One-paragraph summary of the goal.")
    required_inputs: list[str] = Field(
        default_factory=list,
        description="Data inputs the workflow needs to run.",
    )
    steps: list[WorkflowStep] = Field(
        default_factory=list,
        description="Ordered list of steps that make up the workflow.",
    )


# ---------------------------------------------------------------------------
# LangGraph runtime state
# ---------------------------------------------------------------------------

# Human decision surfaced at the HITL breakpoint.
HumanDecision = Literal["approve", "reject", "modify"]


class GraphState(TypedDict, total=False):
    """Mutable state threaded through the LangGraph nodes.

    Only ``total=False`` keys are required; the executor seeds a subset and the
    nodes populate the rest as the run progresses.
    """

    # The workflow contract being executed.
    workflow: dict[str, Any]

    # Index of the step currently being processed (0-based).
    step_index: int

    # The action drafted by ``draft_action`` awaiting human review.
    drafted_action: str

    # The most recent human decision (approve/reject/modify).
    human_decision: HumanDecision

    # Free-form human feedback used to redraft on rejection/modification.
    human_feedback: str

    # Result returned by the (mocked) execution tool.
    execution_result: str

    # Append-only trace of everything that happened, used for logging.
    history: Annotated[list[dict[str, Any]], "run trace"]

    # Set to True once every step has been executed.
    completed: bool

    # Runtime input values for this run, used to fill templated exemplars.
    inputs: dict[str, Any]
