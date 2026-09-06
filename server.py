"""server.py — Web backend for the Progressive Automation Harness.

Wraps the existing harness modules behind a small HTTP API so the whole
experience (generate → co-pilot with HITL → watch the skill mature) can be
driven from a browser. Serves the static SPA in ``web/``.

Run:
    python server.py           # then open http://127.0.0.1:8000
or:
    uvicorn server:app --reload
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from executor import compile_graph
from graph_builder import build_graph
from interviewer import demo_schema
from llm_provider import (
    build_chat_model,
    is_offline,
    available_models,
    default_model,
    resolve_model,
)
from main import _extract_approved_traces
from models import WorkflowSchema
from skill import Skill
from store import get_store, compile_and_save
from telemetry import setup_telemetry, span
import approvals
import notifications
import shell
from tools import is_high_risk

load_dotenv(override=True)

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"
DEPLOYED_DIR = BASE_DIR / "deployed"

app = FastAPI(title="Progressive Automation Harness")

# Emit OpenTelemetry traces to Application Insights when configured (Azure).
setup_telemetry(app)

# Skill + trace persistence: Cosmos when COSMOS_ENDPOINT is set, else local files.
STORE = get_store()

# In-memory session registry (prototype). Each holds a compiled graph + skill.
_SESSIONS: dict[str, dict[str, Any]] = {}

# Step-count guidance injected into the extraction prompt per detail level.
_DETAIL_GUIDANCE = {
    "concise": "Produce a minimal workflow of about 3-4 high-level steps.",
    "balanced": "Produce a clear workflow of about 5-7 steps.",
    "detailed": "Produce a thorough, granular workflow of about 8-12 steps.",
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    description: str
    detail: str = "balanced"  # concise | balanced | detailed
    model: str = "auto"      # "auto" or a specific chat deployment


class StartRequest(BaseModel):
    workflow: dict[str, Any]
    inputs: dict[str, Any] = {}
    model: str = "auto"  # "auto" or a specific chat deployment
    require_approvals: int = 1  # N distinct approvers for high-risk steps (N-eyes)


class RunRequest(BaseModel):
    inputs: dict[str, Any] = {}


class DecisionRequest(BaseModel):
    decision: str  # approve | reject | modify | edit
    feedback: str = ""
    action: str = ""  # for "edit": the human-authored replacement action
    approver: str = ""  # dev fallback identity when Easy Auth headers are absent


class SimulateRequest(BaseModel):
    task: str = "stock"
    runs: int = 6


class InterviewMessageRequest(BaseModel):
    text: str
    detail: str = "balanced"


class ShellRunRequest(BaseModel):
    command: str


class ShellScriptRequest(BaseModel):
    name: str
    args: list[str] = []


class LifecycleActionRequest(BaseModel):
    action: str  # promote | certify | deprecate | retire | reactivate


class RollbackRequest(BaseModel):
    version: int


# Conversation state for interactive workflow extraction.
_INTERVIEWS: dict[str, dict[str, Any]] = {}

_INTERVIEW_SYSTEM = (
    "You are a friendly Requirements Engineer helping the user turn a manual, "
    "repetitive task into an automatable workflow (a 'skill'). Interview them "
    "with focused, ONE-AT-A-TIME questions to uncover: the concrete task and "
    "its goal; the systems, tools, or websites involved and how they are "
    "accessed; the ordered steps and any conditions or edge cases; and what a "
    "successful outcome looks like. Keep each message short and ask a single "
    "question at a time. When you have enough to define a clear ordered "
    "workflow, reply with EXACTLY this token on its own line and nothing else: "
    "[[READY]]"
)

_READY = "[[READY]]"


def _content(msg: Any) -> str:
    text = getattr(msg, "content", "")
    return text.strip() if isinstance(text, str) else str(text)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_step(values: dict[str, Any]) -> Optional[dict[str, Any]]:
    steps = values.get("workflow", {}).get("steps", [])
    idx = values.get("step_index", 0)
    return steps[idx] if 0 <= idx < len(steps) else None


def _approver_id(request: Request, req: DecisionRequest) -> str:
    """Identify the approver: Easy Auth header first, else a dev-supplied name."""
    for header in ("x-ms-client-principal-name", "x-ms-client-principal-id"):
        val = request.headers.get(header)
        if val:
            return val
    return (req.approver or "").strip() or "local-user"


def _skill_view(skill: Optional[Skill]) -> dict[str, Any]:
    if skill is None or not skill.strategies:
        return {"rows": [], "overall": "cold"}
    return {"rows": skill.report_rows(), "overall": skill.overall_maturity().value}


def _view(sess: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = sess.get("last_values") or {}
    history = values.get("history", [])
    draft_source = None
    for event in reversed(history):
        if event.get("event") == "draft":
            draft_source = event.get("source")
            break
    schema: WorkflowSchema = sess["schema"]
    step = _current_step(values)
    n_eyes = sess.get("require_approvals", 1)
    required = approvals.required_approvals(step, n_eyes) if step else 1
    have = len(sess.get("approvers", {}).get(step.get("id"), set())) if step else 0
    return {
        "session_id": sess["id"],
        "task_name": schema.task_name,
        "summary": schema.summary,
        "required_inputs": schema.required_inputs,
        "steps": [s.model_dump() for s in schema.steps],
        "status": sess["status"],
        "current_step_index": values.get("step_index", 0),
        "drafted_action": values.get("drafted_action"),
        "draft_source": draft_source,
        "history": history,
        "completed": sess["status"] == "completed",
        "offline": is_offline(),
        "skill": _skill_view(sess.get("skill")),
        "approval": {
            "required": required,
            "have": have,
            "teams": notifications.is_enabled(),
        },
    }


def _run_until_pause(sess: dict[str, Any]) -> None:
    """Advance the graph until a human review is needed or the run completes.

    Autonomous steps (skill-mastered) are auto-approved server-side so the UI
    visibly shows supervision falling away.
    """
    compiled = sess["compiled"]
    config = sess["config"]
    inputs = sess.get("inputs")

    while True:
        for _ in compiled.stream(inputs, config=config):
            pass
        inputs = None
        snapshot = compiled.get_state(config)
        sess["last_values"] = snapshot.values

        if not snapshot.next:
            sess["status"] = "completed"
            _finalize(sess)
            return

        step = _current_step(snapshot.values)
        step_id = step.get("id") if step else None
        skill: Optional[Skill] = sess.get("skill")
        if (
            skill is not None
            and step_id is not None
            and skill.is_autonomous(step_id)
            and not is_high_risk(step)
        ):
            compiled.update_state(
                config, {"human_decision": "approve", "human_feedback": ""}
            )
            continue

        sess["status"] = "awaiting_review"
        sess["inputs"] = None
        return


def _finalize(sess: dict[str, Any]) -> None:
    """Log approvals and recompile the skill so its maturity reflects this run."""
    schema: WorkflowSchema = sess["schema"]
    values = sess.get("last_values") or {}
    STORE.append_traces(_extract_approved_traces(schema, values))
    sess["skill"] = compile_and_save(
        STORE, schema.task_name, schema.summary, schema.required_inputs
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    from telemetry import is_enabled
    import content_safety

    return {
        "ok": True,
        "offline": is_offline(),
        "tracing": is_enabled(),
        "content_safety": content_safety.is_available(),
        "shell": shell.is_enabled(),
    }


@app.get("/api/governance/screen")
def governance_screen(text: str) -> dict[str, Any]:
    """Screen arbitrary text through Content Safety (governance smoke test)."""
    import content_safety

    return content_safety.screen(text)


@app.post("/api/fabric/selftest")
def fabric_selftest() -> dict[str, Any]:
    """Prove the container's managed identity can build on real Fabric."""
    import fabric_client

    if not fabric_client.is_enabled():
        return {"enabled": False, "reason": "FABRIC_WORKSPACE_ID not set."}
    created = fabric_client.create_pipeline("pah-container-selftest")
    return {
        "enabled": True,
        "created": created,
        "pipelines": fabric_client.list_pipelines(),
    }


@app.post("/api/knowledge/seed")
def knowledge_seed() -> dict[str, Any]:
    """Build the RAG index and load the seed corpus (runs inside the VNet)."""
    import retrieval

    result = retrieval.seed_default_knowledge()
    result["available"] = retrieval.is_available()
    return result


@app.get("/api/knowledge/search")
def knowledge_search(q: str) -> dict[str, Any]:
    """Return the top grounding snippets for a query (RAG smoke test)."""
    import retrieval

    return {"query": q, "results": retrieval.retrieve(q)}


@app.get("/api/models")
def list_models() -> dict[str, Any]:
    """Selectable chat models plus the 'auto' option for the UI."""
    return {
        "default": default_model(),
        "models": available_models(),
        "auto": True,
        "offline": is_offline(),
    }


@app.post("/api/generate")
def generate(req: GenerateRequest) -> dict[str, Any]:
    """Turn a free-text description into a structured workflow schema."""
    text = req.description.strip()
    chosen = resolve_model(req.model, req.detail)
    llm = build_chat_model(deployment=chosen)
    guidance = _DETAIL_GUIDANCE.get(req.detail, _DETAIL_GUIDANCE["balanced"])
    if llm is not None:
        try:
            structured = llm.with_structured_output(WorkflowSchema)
            schema: WorkflowSchema = structured.invoke(  # type: ignore[assignment]
                [
                    SystemMessage(
                        content="You are a Requirements Engineer. Convert the user's "
                        "described repetitive task into a structured workflow with an "
                        "ordered list of concrete steps (number them from 1). Keep "
                        "steps concise and actionable. Set human_required to true for "
                        "any step that is irreversible or high-consequence (submitting, "
                        "sending, paying, transferring, deleting). " + guidance
                    ),
                    HumanMessage(content=text),
                ]
            )
            return {
                "schema": schema.model_dump(),
                "generated_by": "llm",
                "model": chosen,
            }
        except Exception:
            pass  # fall through to a demo schema

    low = text.lower()
    if "fabric" in low or "pipeline" in low or "lakehouse" in low or "dataflow" in low:
        demo = "fabric"
    elif "expense" in low or "receipt" in low:
        demo = "expense"
    else:
        demo = "stock"
    return {"schema": demo_schema(demo).model_dump(), "generated_by": "demo"}


@app.post("/api/interview/start")
def interview_start() -> dict[str, Any]:
    """Begin an interactive extraction conversation; returns the first question."""
    iid = uuid.uuid4().hex
    llm = build_chat_model()
    if llm is None:
        _INTERVIEWS[iid] = {"messages": []}
        return {
            "interview_id": iid,
            "offline": True,
            "message": "Offline mode: pick a demo workflow with the chips instead.",
        }
    messages = [
        SystemMessage(content=_INTERVIEW_SYSTEM),
        HumanMessage(content="Start the interview with your first question."),
    ]
    reply = llm.invoke(messages)
    messages.append(reply)
    _INTERVIEWS[iid] = {"messages": messages}
    return {"interview_id": iid, "offline": False, "message": _content(reply)}


@app.post("/api/interview/{iid}/message")
def interview_message(iid: str, req: InterviewMessageRequest) -> dict[str, Any]:
    """Continue the interview; when the model is ready, return the workflow."""
    sess = _INTERVIEWS.get(iid)
    if sess is None:
        raise HTTPException(status_code=404, detail="interview not found")

    llm = build_chat_model()
    if llm is None:
        return {"done": True, "schema": demo_schema("expense").model_dump()}

    sess["messages"].append(HumanMessage(content=req.text))
    reply = llm.invoke(sess["messages"])
    sess["messages"].append(reply)
    text = _content(reply)

    if _READY in text:
        guidance = _DETAIL_GUIDANCE.get(req.detail, _DETAIL_GUIDANCE["balanced"])
        structured = llm.with_structured_output(WorkflowSchema)
        schema: WorkflowSchema = structured.invoke(  # type: ignore[assignment]
            list(sess["messages"])
            + [
                HumanMessage(
                    content="Now output the final structured workflow based on "
                    "everything we discussed. Set human_required to true for any "
                    "irreversible or high-consequence step. " + guidance
                )
            ]
        )
        return {"done": True, "schema": schema.model_dump()}

    return {"done": False, "message": text}


@app.post("/api/mcp/tools")
def mcp_tools(refresh: bool = False) -> dict[str, Any]:
    """List tools discovered from configured MCP servers."""
    try:
        import mcp_client

        return {"available": mcp_client.is_available(), "tools": mcp_client.discover_tools(refresh)}
    except Exception as exc:
        return {"available": False, "tools": [], "error": str(exc)[:160]}



@app.post("/api/session")
def start_session(req: StartRequest) -> dict[str, Any]:
    schema = WorkflowSchema.model_validate(req.workflow)
    skill = STORE.load_skill(schema.task_name)

    chosen = resolve_model(req.model, "balanced")
    graph = build_graph(schema, skill=skill, model=chosen)
    compiled = compile_graph(graph)
    thread_id = f"web-{uuid.uuid4().hex[:8]}"

    sess: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "schema": schema,
        "skill": skill,
        "compiled": compiled,
        "config": {"configurable": {"thread_id": thread_id}},
        "inputs": {
            "workflow": schema.model_dump(),
            "step_index": 0,
            "history": [],
            "completed": False,
            "inputs": req.inputs,
        },
        "status": "running",
        "last_values": None,
        # step_id -> set of distinct approver ids (N-eyes / segregation of duties).
        "approvers": {},
        # Distinct approvers required for high-risk steps (1 = standard HITL).
        "require_approvals": max(1, req.require_approvals),
    }
    _SESSIONS[sess["id"]] = sess
    with span("session.start", **{"task.name": schema.task_name}):
        _run_until_pause(sess)
    return _view(sess)


@app.post("/api/session/{session_id}/decision")
def decide(
    session_id: str, req: DecisionRequest, request: Request
) -> dict[str, Any]:
    sess = _SESSIONS.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    if sess["status"] != "awaiting_review":
        raise HTTPException(status_code=409, detail="session is not awaiting review")
    if req.decision not in ("approve", "reject", "modify", "edit"):
        raise HTTPException(status_code=400, detail="invalid decision")

    # A rejection/modification is a demotion signal: record it so the skill can
    # never auto-run a step a human has pushed back on.
    if req.decision in ("reject", "modify"):
        step = _current_step(sess.get("last_values") or {})
        if step:
            STORE.append_traces(
                [
                    {
                        "task_name": sess["schema"].task_name,
                        "outcome": "rejected",
                        "original_request": {
                            "step_id": step.get("id"),
                            "step_name": step.get("name", ""),
                        },
                    }
                ]
            )

    # N-eyes: a high-risk step needs N *distinct* approvers before it runs.
    if req.decision in ("approve", "edit"):
        step = _current_step(sess.get("last_values") or {})
        required = approvals.required_approvals(step, sess.get("require_approvals")) if step else 1
        if step and required > 1:
            who = _approver_id(request, req)
            signed = sess["approvers"].setdefault(step.get("id"), set())
            signed.add(who)
            if not approvals.is_cleared(signed, required):
                notifications.notify_pending_approval(
                    sess["schema"].task_name,
                    step.get("name", ""),
                    len(signed),
                    required,
                    os.getenv("APP_BASE_URL"),
                )
                return _view(sess)  # still awaiting a different approver

    # "edit": run the human-authored action verbatim (counts as an approval so
    # the edited text is learned as the exemplar).
    if req.decision == "edit":
        if not req.action.strip():
            raise HTTPException(status_code=400, detail="edit requires an action")
        patch = {
            "human_decision": "approve",
            "human_feedback": "",
            "drafted_action": req.action,
        }
    else:
        patch = {"human_decision": req.decision, "human_feedback": req.feedback}

    sess["compiled"].update_state(sess["config"], patch)
    sess["inputs"] = None
    with span("session.decision", **{"decision": req.decision}):
        _run_until_pause(sess)
    return _view(sess)


@app.get("/api/session/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    sess = _SESSIONS.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _view(sess)


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> dict[str, Any]:
    """Auto-approve N runs and return the skill maturity after each run."""
    schema = demo_schema(req.task)
    skill = STORE.load_skill(schema.task_name)
    progression: list[dict[str, Any]] = []

    runs = max(1, min(req.runs, 20))
    for run_no in range(1, runs + 1):
        graph = build_graph(schema, skill=skill)
        compiled = compile_graph(graph)
        config = {"configurable": {"thread_id": f"sim-{uuid.uuid4().hex[:8]}"}}
        state: Any = {
            "workflow": schema.model_dump(),
            "step_index": 0,
            "history": [],
            "completed": False,
        }
        while True:
            for _ in compiled.stream(state, config=config):
                pass
            state = None
            snap = compiled.get_state(config)
            if not snap.next:
                break
            compiled.update_state(
                config, {"human_decision": "approve", "human_feedback": ""}
            )
        STORE.append_traces(_extract_approved_traces(schema, snap.values))
        skill = compile_and_save(
            STORE, schema.task_name, schema.summary, schema.required_inputs
        )
        progression.append({"run": run_no, **_skill_view(skill)})

    return {"task_name": schema.task_name, "progression": progression}


@app.get("/api/skill/{task}")
def get_skill(task: str) -> dict[str, Any]:
    schema = demo_schema(task)
    skill = STORE.load_skill(schema.task_name)
    if skill is None:
        return {"exists": False, "task_name": schema.task_name}
    return {"exists": True, "task_name": skill.task_name, "json": skill.to_json()}


def _skill_summary(skill: Skill) -> dict[str, Any]:
    """A compact card for the skills library."""
    rows = skill.report_rows()
    return {
        "task_name": skill.task_name,
        "slug": skill.slug,
        "summary": skill.summary,
        "overall": skill.overall_maturity().value,
        "steps": len(skill.strategies),
        "approvals": sum(int(r.get("approvals", 0)) for r in rows),
        "published": bool(skill.published),
        "status": skill.status,
        "display": _derived_status(skill, False),
        "certified": skill.is_promotable(),
        "version": skill.version,
        "updated_at": skill.updated_at,
        "required_inputs": skill.required_inputs,
    }


def _derived_status(skill: Skill, drifting: bool) -> str:
    """The lifecycle state to display, overlaying drift on the stored status."""
    if skill.status in ("deprecated", "retired"):
        return skill.status
    if skill.status == "published" or skill.published:
        return "needs_attention" if drifting else "active"
    if skill.is_promotable():
        return "candidate"
    return "in_training" if skill.strategies else "draft"


def _lifecycle_view(skill: Skill, traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Full lifecycle state: stored + derived status, publish gate, drift, versions."""
    import monitoring

    drift = monitoring.drift_report(traces) if traces else {"drifting": False}
    drifting = bool(drift.get("drifting"))
    order = ["cold", "primed", "deterministic", "autonomous"]
    reasons: list[str] = []
    if not skill.strategies:
        reasons.append("no learned steps yet")
    elif order.index(skill.overall_maturity().value) < order.index("deterministic"):
        reasons.append("reach DETERMINISTIC (approve the same action 3+ times)")
    if skill.has_regressions():
        reasons.append("clear rejected / failed steps")
    return {
        "task_name": skill.task_name,
        "status": skill.status,
        "display": _derived_status(skill, drifting),
        "certified": skill.is_promotable(),
        "gate_reasons": reasons,
        "version": skill.version,
        "owner": skill.owner,
        "published": bool(skill.published),
        "published_at": skill.published_at,
        "last_validated_at": skill.last_validated_at,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
        "overall": skill.overall_maturity().value,
        "regressions": skill.has_regressions(),
        "drift": drift,
        "versions": [
            {
                "version": v.get("version"),
                "at": v.get("at"),
                "overall": v.get("overall"),
                "note": v.get("note", ""),
            }
            for v in skill.versions
        ],
    }


@app.get("/api/skills")
def list_skills() -> dict[str, Any]:
    """The skills library: every compiled skill, newest first."""
    import monitoring

    skills = [s for s in STORE.list_skills() if s.strategies]
    cards: list[dict[str, Any]] = []
    for s in skills:
        card = _skill_summary(s)
        # Only published skills can be "needs attention" (drift), so limit reads.
        if s.published or s.status == "published":
            try:
                traces = STORE.read_traces(s.task_name)
                drifting = bool(monitoring.drift_report(traces).get("drifting")) if traces else False
                card["display"] = _derived_status(s, drifting)
            except Exception:
                pass
        cards.append(card)
    cards.sort(key=lambda c: c["updated_at"], reverse=True)
    return {"skills": cards, "store": STORE.kind, "count": len(cards)}


@app.get("/api/skill-report")
def skill_report(name: str) -> dict[str, Any]:
    """Maturity rows + overall for a task by exact name (empty when none)."""
    skill = STORE.load_skill(name)
    return {
        "task_name": name,
        "exists": bool(skill and skill.strategies),
        "required_inputs": skill.required_inputs if skill else [],
        **_skill_view(skill),
    }


@app.get("/api/skill-artifact")
def skill_artifact(name: str) -> dict[str, Any]:
    """Load a skill by its exact task name (works for LLM-generated tasks too)."""
    skill = STORE.load_skill(name)
    if skill is None:
        return {"exists": False, "task_name": name}
    return {"exists": True, "task_name": skill.task_name, "json": skill.to_json()}


@app.get("/api/skill-export")
def skill_export(name: str) -> dict[str, Any]:
    """Export a skill as a model-agnostic prompt (usable by Claude, GPT, etc.)."""
    skill = STORE.load_skill(name)
    if skill is None:
        return {"exists": False, "task_name": name}
    return {
        "exists": True,
        "task_name": skill.task_name,
        "prompt": skill.to_prompt(),
    }


@app.post("/api/skill-distill")
def skill_distill(name: str) -> dict[str, Any]:
    """Distill approved exemplars into optimized per-step instructions (#12)."""
    import distiller

    skill = STORE.load_skill(name)
    if skill is None:
        return {"exists": False, "task_name": name, "distilled_steps": 0}
    updated = distiller.distill_skill(skill)
    if updated:
        STORE.save_skill(skill)
    return {
        "exists": True,
        "task_name": skill.task_name,
        "distilled_steps": updated,
        "prompt": skill.to_prompt(),
    }


@app.get("/api/skill/{task}/drift")
def skill_drift(task: str) -> dict[str, Any]:
    """Report execution drift for a task from its stored traces (#13)."""
    import monitoring

    traces = STORE.read_traces(task)
    return {"task_name": task, **monitoring.drift_report(traces)}


@app.post("/api/skill-deploy")
def skill_deploy(name: str, request: Request) -> dict[str, Any]:
    """Publish the skill: snapshot a version, mark it published in the store
    (Cosmos when deployed), set its lifecycle status, and drop a copy in the
    local ``deployed/`` registry stub."""
    skill = STORE.load_skill(name)
    if skill is None:
        raise HTTPException(
            status_code=404,
            detail="No skill to deploy yet. Run or simulate this workflow first.",
        )
    now = datetime.now(timezone.utc).isoformat()
    skill.snapshot(note=f"published v{skill.version}")
    skill.version += 1
    skill.published = True
    skill.published_at = now
    skill.last_validated_at = now
    skill.status = "published"
    owner = request.headers.get("x-ms-client-principal-name")
    if owner:
        skill.owner = owner
    STORE.save_skill(skill)

    DEPLOYED_DIR.mkdir(parents=True, exist_ok=True)
    path = DEPLOYED_DIR / f"{skill.slug}.json"
    path.write_text(skill.to_json(), encoding="utf-8")
    return {
        "deployed": True,
        "published": True,
        "store": STORE.kind,
        "path": str(path.relative_to(BASE_DIR)).replace("\\", "/"),
        "run_endpoint": f"POST /api/skill-run?name={skill.task_name}",
        "task_name": skill.task_name,
        "overall": skill.overall_maturity().value,
        "steps": len(skill.strategies),
        "version": skill.version,
        "certified": skill.is_promotable(),
        "status": skill.status,
    }


@app.get("/api/skill-lifecycle")
def skill_lifecycle(name: str) -> dict[str, Any]:
    """Full lifecycle state for a skill (status, publish gate, drift, versions)."""
    skill = STORE.load_skill(name)
    if skill is None:
        return {"exists": False, "task_name": name}
    return {"exists": True, **_lifecycle_view(skill, STORE.read_traces(name))}


@app.post("/api/skill-lifecycle")
def skill_lifecycle_action(
    name: str, req: LifecycleActionRequest, request: Request
) -> dict[str, Any]:
    """Drive a lifecycle transition: promote / certify / deprecate / retire / reactivate."""
    skill = STORE.load_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="No such skill.")
    action = (req.action or "").lower()
    now = datetime.now(timezone.utc).isoformat()
    if action in ("promote", "publish"):
        if not skill.is_promotable():
            reasons = _lifecycle_view(skill, STORE.read_traces(name))["gate_reasons"]
            raise HTTPException(
                status_code=400,
                detail="Not ready to promote — " + "; ".join(reasons) + ".",
            )
        skill.snapshot(note=f"promoted v{skill.version}")
        skill.version += 1
        skill.published = True
        skill.published_at = now
        skill.last_validated_at = now
        skill.status = "published"
        owner = request.headers.get("x-ms-client-principal-name")
        if owner:
            skill.owner = owner
    elif action == "certify":
        skill.last_validated_at = now
    elif action == "deprecate":
        skill.status = "deprecated"
    elif action == "retire":
        skill.status = "retired"
        skill.published = False
    elif action == "reactivate":
        skill.status = "published" if skill.is_promotable() else "draft"
        skill.published = skill.status == "published"
        if skill.published:
            skill.published_at = now
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'.")
    STORE.save_skill(skill)
    return {"ok": True, "action": action, **_lifecycle_view(skill, STORE.read_traces(name))}


@app.post("/api/skill-rollback")
def skill_rollback(name: str, req: RollbackRequest) -> dict[str, Any]:
    """Restore a skill's content from a prior version snapshot."""
    skill = STORE.load_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="No such skill.")
    if not skill.restore(req.version):
        raise HTTPException(status_code=404, detail=f"No version {req.version} to roll back to.")
    skill.snapshot(note=f"rolled back to v{req.version}")
    skill.version += 1
    skill.updated_at = datetime.now(timezone.utc).isoformat()
    STORE.save_skill(skill)
    return {"ok": True, "restored_from": req.version, **_lifecycle_view(skill, STORE.read_traces(name))}


def _seed_skill(
    task_name: str,
    summary: str,
    steps: list[tuple[str, str]],
    approvals: int,
    *,
    status: str = "draft",
    published: bool = False,
    snapshots: int = 0,
    inputs: Optional[list[str]] = None,
) -> Skill:
    """Build a demo skill matured to a given level with a lifecycle state."""
    skill = Skill(task_name=task_name, summary=summary, required_inputs=list(inputs or []))
    for i, (name, desc) in enumerate(steps, start=1):
        strat = skill.ensure_strategy(i, name, desc)
        for _ in range(approvals):  # identical approvals climb the maturity ladder
            strat.add_approval(f"{name}: {desc}")
    now = datetime.now(timezone.utc).isoformat()
    skill.status = status
    if published:
        skill.published = True
        skill.published_at = now
        skill.last_validated_at = now
    for _ in range(snapshots):
        skill.snapshot(note=f"published v{skill.version}")
        skill.version += 1
    return skill


def _drift_traces(task_name: str) -> list[dict[str, Any]]:
    """Traces whose recent failure rate trips drift detection (needs-attention)."""
    out: list[dict[str, Any]] = []
    for i in range(10):
        out.append({"task_name": task_name, "timestamp": f"2026-08-01T00:00:{i:02d}",
                    "outcome": "approved", "execution_status": "success"})
    for i in range(6):
        out.append({"task_name": task_name, "timestamp": f"2026-09-05T00:00:{i:02d}",
                    "outcome": "approved", "execution_status": "failed"})
    return out


@app.post("/api/skills/seed")
def seed_skills() -> dict[str, Any]:
    """Create demo skills spanning lifecycle states so the Library and Lifecycle
    panel can be explored without running full sessions. Idempotent (upserts)."""
    defs = [
        {"drift": False, "build": dict(
            task_name="Summarize Competitor Pricing",
            summary="Open a competitor's pricing page, extract the plan tiers and prices, and summarize the differences versus our product.",
            inputs=["competitor"],
            steps=[("Open pricing page", "Navigate to the competitor's public pricing page."),
                   ("Extract tiers", "Read each plan tier and its monthly price."),
                   ("Summarize differences", "Write a short comparison versus our product.")],
            approvals=5, status="published", published=True, snapshots=2)},
        {"drift": False, "build": dict(
            task_name="Weekly Expense Report",
            summary="Collect receipts, categorize them, total the amounts, and prepare the weekly expense report.",
            steps=[("Collect receipts", "Gather the week's receipts."),
                   ("Categorize", "Classify each receipt by expense type."),
                   ("Total and prepare", "Sum the totals and format the report.")],
            approvals=3, status="draft")},
        {"drift": False, "build": dict(
            task_name="Onboard a New Vendor",
            summary="Validate a new vendor's details and create the vendor record.",
            steps=[("Validate details", "Check the vendor's tax id and banking details."),
                   ("Create record", "Create the vendor in the system.")],
            approvals=1, status="draft")},
        {"drift": True, "build": dict(
            task_name="Nightly Data Export",
            summary="Export the day's transactions to the data lake and verify the row count.",
            steps=[("Query transactions", "Select the day's transactions."),
                   ("Export to lake", "Write the file to the data lake."),
                   ("Verify count", "Confirm the exported row count matches.")],
            approvals=5, status="published", published=True, snapshots=1)},
        {"drift": False, "build": dict(
            task_name="Legacy Invoice Sync",
            summary="Sync invoices from the legacy billing system (superseded by the new pipeline).",
            steps=[("Read legacy invoices", "Pull invoices from the legacy database."),
                   ("Map fields", "Map legacy fields to the new schema."),
                   ("Write to target", "Insert into the target system.")],
            approvals=3, status="deprecated")},
    ]
    created: list[dict[str, Any]] = []
    for d in defs:
        skill = _seed_skill(**d["build"])
        STORE.save_skill(skill)
        if d.get("drift"):
            STORE.append_traces(_drift_traces(skill.task_name))
        created.append({"task_name": skill.task_name, "status": skill.status, "published": skill.published})
    return {"seeded": created, "count": len(created), "store": STORE.kind}


def _schema_from_skill(skill: Skill) -> WorkflowSchema:
    """Reconstruct a runnable workflow schema from a learned skill."""
    from models import WorkflowStep

    steps = [
        WorkflowStep(
            id=s.step_id, name=s.step_name, description=s.step_description
        )
        for s in sorted(skill.strategies.values(), key=lambda s: s.step_id)
    ]
    return WorkflowSchema(
        task_name=skill.task_name,
        summary=skill.summary,
        required_inputs=skill.required_inputs,
        steps=steps,
    )


def _tool_from_result(result: str) -> Optional[str]:
    """Identify which tool produced a step result, for the run visualization."""
    text = (result or "").lstrip()
    if text.startswith("[mcp:"):
        end = text.find("]")
        return text[1:end] if end > 0 else "mcp"
    if text.startswith("[browsed"):
        return "web"
    if text.startswith("[shell]"):
        return "shell"
    if text.startswith("[manual]"):
        return "manual"
    if text.startswith("[blocked]"):
        return "blocked"
    if text.startswith("[mock]"):
        return "mock"
    if "yahoo finance" in text.lower():
        return "builtin"
    return "builtin"


def _run_steps_view(schema: WorkflowSchema, values: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair draft (source) and execute (action/result/status) events per step so
    the UI can play back exactly what the deployed skill did, step by step."""
    history = values.get("history", [])
    drafts: dict[Any, dict[str, Any]] = {}
    execs: dict[Any, dict[str, Any]] = {}
    for e in history:
        if e.get("event") == "draft":
            drafts[e.get("step_id")] = e
        elif e.get("event") == "execute":
            execs[e.get("step_id")] = e

    out: list[dict[str, Any]] = []
    for s in schema.steps:
        sd = s.model_dump()
        d = drafts.get(s.id)
        x = execs.get(s.id)
        high_risk = is_high_risk(sd)
        if x is not None:
            result = x.get("result", "")
            out.append({
                "id": s.id,
                "name": s.name,
                "source": (d or {}).get("source"),
                "action": x.get("action") or (d or {}).get("action") or "",
                "result": result,
                "status": x.get("status", "success"),
                "tool": _tool_from_result(result),
                "high_risk": high_risk,
            })
        elif d is not None:
            # Drafted but not executed: the headless run paused at a human gate.
            out.append({
                "id": s.id,
                "name": s.name,
                "source": d.get("source"),
                "action": d.get("action", ""),
                "result": None,
                "status": "blocked",
                "tool": None,
                "high_risk": True,
            })
    return out


@app.post("/api/skill-run")
def skill_run(name: str, req: Optional[RunRequest] = None) -> dict[str, Any]:
    """Run a deployed skill headlessly. Executes non-risky steps automatically
    and stops at the first high-risk step (which needs a human)."""
    skill = STORE.load_skill(name)
    if skill is None or not skill.strategies:
        return {"ran": False, "reason": "No deployed skill found for that task."}

    schema = _schema_from_skill(skill)
    compiled = compile_graph(build_graph(schema, skill=skill))
    config = {"configurable": {"thread_id": f"run-{uuid.uuid4().hex[:8]}"}}
    state: Any = {
        "workflow": schema.model_dump(),
        "step_index": 0,
        "history": [],
        "completed": False,
        "inputs": (req.inputs if req else {}),
    }
    blocked: Optional[str] = None
    while True:
        for _ in compiled.stream(state, config=config):
            pass
        state = None
        snap = compiled.get_state(config)
        if not snap.next:
            break
        step = _current_step(snap.values)
        if step and is_high_risk(step):
            blocked = step.get("name")
            break
        compiled.update_state(
            config, {"human_decision": "approve", "human_feedback": ""}
        )

    values = compiled.get_state(config).values
    steps = _run_steps_view(schema, values)
    executions = [
        e["result"] for e in values.get("history", []) if e.get("event") == "execute"
    ]
    autonomous = sum(1 for s in steps if (s.get("source") or "").startswith("skill"))
    return {
        "ran": blocked is None,
        "status": "blocked" if blocked else "completed",
        "task_name": skill.task_name,
        "summary": skill.summary,
        "overall": skill.overall_maturity().value,
        "blocked_step": blocked,
        "reason": (
            f"Step '{blocked}' is high-risk and needs a human; not run headless."
            if blocked
            else None
        ),
        "steps": steps,
        "executions": executions,
        "steps_executed": len(executions),
        "total_steps": len(schema.steps),
        "autonomous_steps": autonomous,
    }


# ---------------------------------------------------------------------------
# Governed shell / script execution (opt-in via SHELL_TOOL_ENABLED)
# ---------------------------------------------------------------------------
@app.get("/api/shell/status")
def shell_status() -> dict[str, Any]:
    """Whether the shell tool is enabled, plus its allowlist and script registry."""
    return shell.status()


@app.get("/api/scripts")
def list_scripts() -> dict[str, Any]:
    """Prewritten scripts available to invoke by name."""
    return {"enabled": shell.is_enabled(), "scripts": shell.list_scripts()}


@app.post("/api/shell/run")
def shell_run(req: ShellRunRequest) -> dict[str, Any]:
    """Run an allow-listed, operator-free ad-hoc command. 403 when disabled."""
    if not shell.is_enabled():
        raise HTTPException(
            status_code=403,
            detail="Shell tool is disabled. Set SHELL_TOOL_ENABLED=1 to enable it.",
        )
    with span("shell.run", command=req.command):
        return shell.run_command(req.command)


@app.post("/api/shell/script")
def shell_script(req: ShellScriptRequest) -> dict[str, Any]:
    """Invoke a prewritten script from scripts/ with flags. 403 when disabled."""
    if not shell.is_enabled():
        raise HTTPException(
            status_code=403,
            detail="Shell tool is disabled. Set SHELL_TOOL_ENABLED=1 to enable it.",
        )
    with span("shell.script", script=req.name):
        return shell.run_script(req.name, req.args)


# ---------------------------------------------------------------------------
# Static SPA
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
