"""interviewer.py — The Extraction Phase.

Runs a conversational LangChain loop where the LLM plays a Requirements
Engineer. It interviews the user about a repetitive task and, once it has
enough context, emits a structured :class:`WorkflowSchema` (JSON).
"""

from __future__ import annotations

from typing import Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from llm_provider import build_chat_model, is_offline
from models import WorkflowSchema

console = Console()

# Sentinel the model emits when it has gathered enough to produce the schema.
_READY_TOKEN = "[[READY_TO_STRUCTURE]]"

_SYSTEM_PROMPT = f"""You are a meticulous Requirements Engineer conducting a
short interview. Your goal is to fully understand a single repetitive, manual
workflow the user wants to automate.

Ask focused, one-at-a-time questions to uncover:
  1. The concrete task being performed and its goal.
  2. The data inputs required to perform it.
  3. The sequential steps involved.
  4. Any conditions, branches, or edge cases.

Keep questions concise. Do not lecture. When — and only when — you are
confident you understand the task well enough to describe it as an ordered
set of steps, stop asking questions and reply with EXACTLY this token on its
own line: {_READY_TOKEN}

Do not emit the token prematurely."""


def run_interview(
    *,
    temperature: float = 0.3,
    max_turns: int = 20,
) -> WorkflowSchema:
    """Conduct the interview and return the extracted workflow schema.

    The loop alternates between LLM questions and terminal user answers. Once
    the model emits the ready sentinel, a second structured-output call forces
    it to serialize everything into a :class:`WorkflowSchema`.
    """
    llm = build_chat_model(temperature=temperature)
    if llm is None:
        raise RuntimeError("No LLM configured; use run_interview_safe for offline mode.")

    messages: list[BaseMessage] = [SystemMessage(content=_SYSTEM_PROMPT)]

    console.print(
        Panel.fit(
            "[bold cyan]Extraction Phase[/bold cyan]\n"
            "Describe a manual workflow you'd like to automate. "
            "Type your answers when prompted; type [bold]/done[/bold] to force "
            "structuring early.",
            border_style="cyan",
            title="Interviewer",
        )
    )

    # Kick the model off with an opening question.
    messages.append(
        HumanMessage(content="Begin the interview with your first question.")
    )

    for _ in range(max_turns):
        ai: AIMessage = llm.invoke(messages)  # type: ignore[assignment]
        content = (ai.content or "").strip() if isinstance(ai.content, str) else ""
        messages.append(ai)

        if _READY_TOKEN in content:
            console.print("[dim]Interviewer has enough context. Structuring…[/dim]")
            break

        console.print(Panel(content, border_style="blue", title="Requirements Engineer"))

        answer = Prompt.ask("[bold green]You[/bold green]")
        if answer.strip().lower() == "/done":
            messages.append(
                HumanMessage(
                    content="I have provided enough detail. Please structure the "
                    "workflow now."
                )
            )
            continue

        messages.append(HumanMessage(content=answer))
    else:
        console.print("[yellow]Max interview turns reached; structuring now.[/yellow]")

    return _structure(llm, messages)


def _structure(llm, messages: list[BaseMessage]) -> WorkflowSchema:
    """Force the LLM to emit a validated :class:`WorkflowSchema`."""
    structuring_llm = llm.with_structured_output(WorkflowSchema)

    prompt = list(messages) + [
        HumanMessage(
            content="Using everything from our conversation, produce the structured "
            "workflow definition. Number the steps sequentially starting at 1."
        )
    ]

    schema: WorkflowSchema = structuring_llm.invoke(prompt)  # type: ignore[assignment]

    console.print(
        Panel.fit(
            f"[bold]{schema.task_name}[/bold]\n{schema.summary}\n\n"
            f"[cyan]Inputs:[/cyan] {', '.join(schema.required_inputs) or 'none'}\n"
            f"[cyan]Steps:[/cyan] {len(schema.steps)}",
            border_style="green",
            title="Extracted Workflow Schema",
        )
    )
    return schema


def run_interview_safe(
    demo: str = "stock", fallback: Optional[WorkflowSchema] = None
) -> WorkflowSchema:
    """Run the interview, or fall back to a canned schema when offline.

    Useful for smoke-testing the downstream graph without a live LLM.
    """
    if is_offline():
        console.print(
            "[yellow]No LLM configured. Using a demo workflow schema.[/yellow]"
        )
        return fallback or demo_schema(demo)
    return run_interview()


def demo_schema(name: str = "stock") -> WorkflowSchema:
    """Return a deterministic sample schema for offline demos and tests."""
    from models import WorkflowStep

    if name == "fabric":
        return WorkflowSchema(
            task_name="Build a Fabric Data Pipeline",
            summary=(
                "Create a Microsoft Fabric lakehouse and a pipeline that ingests "
                "CSVs from OneLake, cleans them, loads a lakehouse table, and runs it."
            ),
            required_inputs=["workspace", "source path (OneLake)", "target table"],
            steps=[
                WorkflowStep(
                    id=1,
                    name="Create a lakehouse",
                    description="Create a Fabric lakehouse as the table destination.",
                ),
                WorkflowStep(
                    id=2,
                    name="Create the ingestion pipeline",
                    description="Create a Data Factory pipeline in the workspace.",
                ),
                WorkflowStep(
                    id=3,
                    name="Add a copy activity",
                    description=(
                        "Add a copy activity from the OneLake CSV source to the "
                        "lakehouse table."
                    ),
                ),
                WorkflowStep(
                    id=4,
                    name="Create a cleaning dataflow",
                    description="Create a Dataflow Gen2 to clean and shape the data.",
                ),
                WorkflowStep(
                    id=5,
                    name="Run the pipeline",
                    description="Trigger the pipeline run against the workspace.",
                    human_required=True,
                ),
                WorkflowStep(
                    id=6,
                    name="Validate the run",
                    description="Check the run status and confirm the table loaded.",
                ),
            ],
        )

    if name == "stock":
        return WorkflowSchema(
            task_name="Check MSFT Stock Price",
            summary="Open a browser, search for the MSFT stock, and read its price.",
            required_inputs=["ticker symbol (MSFT)"],
            steps=[
                WorkflowStep(
                    id=1,
                    name="Open browser search",
                    description="Open a browser and search for 'MSFT stock price'.",
                ),
                WorkflowStep(
                    id=2,
                    name="Read current price",
                    description="Read the current MSFT stock price from the results.",
                ),
                WorkflowStep(
                    id=3,
                    name="Report price",
                    description="Report the MSFT price back to the requester.",
                ),
            ],
        )

    return WorkflowSchema(
        task_name="Weekly Expense Report",
        summary="Collect receipts, categorize them, and submit a report to finance.",
        required_inputs=["receipt images", "employee id", "cost center"],
        steps=[
            WorkflowStep(
                id=1,
                name="Gather receipts",
                description="Collect all receipts for the week.",
            ),
            WorkflowStep(
                id=2,
                name="Categorize",
                description="Assign each receipt a category.",
            ),
            WorkflowStep(
                id=3,
                name="Total & validate",
                description="Sum amounts and validate against policy.",
            ),
            WorkflowStep(
                id=4,
                name="Submit",
                description="Submit the report to the finance system.",
            ),
        ],
    )


if __name__ == "__main__":
    result = run_interview_safe()
    console.print_json(result.model_dump_json(indent=2))
