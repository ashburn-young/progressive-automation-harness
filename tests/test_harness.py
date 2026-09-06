"""Tests for the skill layer, compiler, and tool routing.

These avoid LangGraph/LangChain imports so they run fast and offline.
Run with: pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skill import Maturity, Skill, StepStrategy, slugify
from skill_compiler import compile_skill_from_logs
import tools


def _make_skill() -> Skill:
    skill = Skill(task_name="Check MSFT Stock Price")
    skill.ensure_strategy(1, "Read price", "Read the current MSFT price.")
    return skill


def test_slugify():
    assert slugify("Check MSFT Stock Price!") == "check-msft-stock-price"


def test_maturity_ladder():
    strat = StepStrategy(step_id=1, step_name="s")
    thresholds = {"primed": 1, "deterministic": 3, "autonomous": 5}

    assert strat.maturity(thresholds) == Maturity.COLD
    strat.add_approval("do X")
    assert strat.maturity(thresholds) == Maturity.PRIMED
    strat.add_approval("do X")
    strat.add_approval("do X")
    assert strat.maturity(thresholds) == Maturity.DETERMINISTIC
    strat.add_approval("do X")
    strat.add_approval("do X")
    assert strat.maturity(thresholds) == Maturity.AUTONOMOUS


def test_inconsistent_approvals_do_not_reach_autonomous():
    strat = StepStrategy(step_id=1, step_name="s")
    thresholds = {"primed": 1, "deterministic": 3, "autonomous": 5}
    for i in range(6):
        strat.add_approval(f"different action {i}")
    # No single dominant action repeats enough -> stays PRIMED.
    assert strat.maturity(thresholds) == Maturity.PRIMED


def test_draft_step_replays_when_mature():
    skill = _make_skill()
    strat = skill.strategy(1)
    for _ in range(5):
        strat.add_approval("MSFT last close $427")

    called = {"n": 0}

    def llm_draft(_exemplars):
        called["n"] += 1
        return "fresh draft"

    action, source = skill.draft_step(1, offline=True, llm_draft=llm_draft)
    assert action == "MSFT last close $427"
    assert source == "skill:autonomous"
    assert called["n"] == 0  # deterministic replay skipped the LLM


def test_draft_step_feedback_forces_fresh_draft():
    skill = _make_skill()
    strat = skill.strategy(1)
    for _ in range(5):
        strat.add_approval("MSFT last close $427")

    def llm_draft(_exemplars):
        return "revised draft"

    action, source = skill.draft_step(
        1, offline=True, llm_draft=llm_draft, feedback="use a different source"
    )
    assert action == "revised draft"
    assert source == "mock"


def test_compiler_builds_and_matures_skill(tmp_path: Path):
    log = tmp_path / "log.jsonl"
    skills_dir = tmp_path / "skills"

    trace = {
        "task_name": "Check MSFT Stock Price",
        "original_request": {
            "step_id": 1,
            "step_name": "Read price",
            "step_description": "Read the current MSFT price.",
            "required_inputs": ["MSFT"],
        },
        "drafted_action": "Read MSFT price from results",
        "human_feedback": "",
        "final_execution": "MSFT last close $427",
    }
    with log.open("w", encoding="utf-8") as fh:
        for _ in range(5):
            fh.write(json.dumps(trace) + "\n")

    skill = compile_skill_from_logs("Check MSFT Stock Price", log, skills_dir)

    assert skill.maturity_for(1) == Maturity.AUTONOMOUS
    assert skill.is_autonomous(1)
    assert (skills_dir / "check-msft-stock-price.json").exists()


def test_compiler_is_idempotent_roundtrip(tmp_path: Path):
    log = tmp_path / "log.jsonl"
    skills_dir = tmp_path / "skills"
    trace = {
        "task_name": "T",
        "original_request": {"step_id": 1, "step_name": "s", "required_inputs": []},
        "drafted_action": "a",
    }
    log.write_text(json.dumps(trace) + "\n", encoding="utf-8")

    skill = compile_skill_from_logs("T", log, skills_dir)
    reloaded = Skill.load(skills_dir / "t.json")
    assert reloaded.strategy(1).approvals == skill.strategy(1).approvals == 1


def test_execute_step_routes_stock(monkeypatch):
    # Isolate the built-in path: no structured binding, no MCP resolution.
    monkeypatch.setenv("STRUCTURED_TOOL_BINDING", "0")
    monkeypatch.setattr(tools, "_resolve_mcp", lambda step, action, inputs=None: None)
    monkeypatch.setattr(tools, "get_stock_price", lambda symbol="MSFT": f"PRICE:{symbol}")
    step = {"name": "Read current price", "description": "MSFT stock price"}
    result = tools.execute_step(step, "read the MSFT price")
    assert result == "PRICE:MSFT"


def test_resolve_ticker(monkeypatch):
    # Known company and a clean ticker resolve with no network.
    assert tools.resolve_ticker("microsoft") == "MSFT"
    assert tools.resolve_ticker("V") == "V"
    # An unknown company name uses the live symbol search (mocked here).
    tools._TICKER_CACHE.clear()
    monkeypatch.setattr(tools, "_yahoo_symbol_search", lambda q: "BRK-B")
    assert tools.resolve_ticker("Berkshire Hathaway") == "BRK-B"


def test_run_binding_dispatch(monkeypatch):
    import mcp_client
    from tool_binding import ToolCall

    # Force the built-in path (no MCP tool of the same name in the way).
    monkeypatch.setattr(mcp_client, "is_available", lambda: False)
    monkeypatch.setattr(tools, "get_stock_price", lambda s="MSFT": f"PRICE:{s}")
    # A built-in tool driven by structured args (no text parsing).
    call = ToolCall(tool="get_stock_price", args_json='{"query": "AAPL"}')
    assert tools._run_binding(call, {"name": "x"}, "act", None) == "PRICE:AAPL"
    # 'none' becomes an honest manual step, not a fake success.
    none_call = ToolCall(tool="none", args_json="{}")
    assert tools._run_binding(none_call, {"name": "Present"}, "a", None).startswith("[manual]")


def test_bind_tool_disabled(monkeypatch):
    import tool_binding

    monkeypatch.setenv("STRUCTURED_TOOL_BINDING", "0")
    assert tool_binding.bind_tool({"name": "x"}, "a", None, []) is None


def test_lifecycle_gate():
    from skill import Skill

    s = Skill(task_name="Gate Demo")
    strat = s.ensure_strategy(1, "do it")
    assert s.is_promotable() is False  # no approvals yet
    for _ in range(3):
        strat.add_approval("the same action")
    assert s.overall_maturity().value == "deterministic"
    assert s.is_promotable() is True
    strat.rejections = 1
    assert s.has_regressions() is True
    assert s.is_promotable() is False  # a regression blocks promotion


def test_snapshot_and_restore():
    from skill import Skill

    s = Skill(task_name="Ver Demo", summary="v1 summary")
    s.ensure_strategy(1, "step").add_approval("action one")
    s.snapshot(note="published v1")
    pinned = s.version
    s.summary = "v2 summary"
    s.ensure_strategy(1, "step").add_approval("action two changed")
    assert s.restore(pinned) is True
    assert s.summary == "v1 summary"


def test_compile_preserves_lifecycle(tmp_path, monkeypatch):
    import store as store_mod
    from skill import Skill

    monkeypatch.setattr(store_mod, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(store_mod, "TRAINING_LOG", tmp_path / "log.jsonl")
    fs = store_mod.FileStore()
    s = Skill(task_name="Persist Demo", summary="sum")
    s.ensure_strategy(1, "step").add_approval("act")
    s.status = "published"
    s.published = True
    s.version = 4
    s.snapshot(note="v")
    fs.save_skill(s)
    out = store_mod.compile_and_save(fs, "Persist Demo", "sum", [])
    assert out.status == "published"
    assert out.published is True
    assert out.version == 4
    assert len(out.versions) >= 1


def test_evaluate_skill():
    import evaluation
    from skill import Skill

    s = Skill(task_name="Eval Demo", summary="does a thing", required_inputs=["x"])
    strat = s.ensure_strategy(1, "step one")
    for _ in range(5):  # climb to autonomous
        strat.add_approval("do it")
    ev = evaluation.evaluate_skill(s, [])
    keys = {d["key"] for d in ev["dimensions"]}
    assert keys == {"safety", "completeness", "executability", "maintainability", "cost"}
    assert 0 <= ev["overall"] <= 100
    by = {d["key"]: d["score"] for d in ev["dimensions"]}
    assert by["completeness"] == 100  # the single step has an exemplar
    assert by["cost"] == 100  # mastered step replays with no LLM call
    # A recorded failure drags executability below 100.
    strat.failures = 2
    ev2 = evaluation.evaluate_skill(s, [])
    ex = {d["key"]: d["score"] for d in ev2["dimensions"]}["executability"]
    assert ex < 100


def test_detect_symbol():
    assert tools._detect_symbol("check microsoft stock") == "MSFT"
    assert tools._detect_symbol("AAPL quote") == "AAPL"
    # Ordinary uppercase words must not be mistaken for a ticker.
    assert tools._detect_symbol("I will report the price in USD") is None
    # Company names map to tickers; exchange labels are not tickers.
    assert tools._detect_symbol("look up Visa Inc. (NYSE: V)") == "V"
    assert tools._detect_symbol("listed on the NYSE exchange") is None


def test_symbol_from_prefers_inputs():
    # Runtime inputs steer the ticker; otherwise default to MSFT (never "I").
    ctx = "I will retrieve the latest stock price and report it in USD."
    assert tools._symbol_from({"symbol": "AAPL"}, ctx) == "AAPL"
    assert tools._symbol_from({"company": "microsoft"}, ctx) == "MSFT"
    assert tools._symbol_from(None, ctx) == "MSFT"


def test_high_risk_gate():
    assert tools.is_high_risk({"name": "Submit expense report", "description": ""})
    assert tools.is_high_risk({"name": "x", "description": "", "human_required": True})
    assert not tools.is_high_risk({"name": "Read current price", "description": "read"})


def test_rejection_caps_autonomy():
    thresholds = {"primed": 1, "deterministic": 3, "autonomous": 5}
    strat = StepStrategy(step_id=1, step_name="s")
    for _ in range(6):
        strat.add_approval("do X")
    assert strat.maturity(thresholds) == Maturity.AUTONOMOUS
    strat.rejections = 1
    assert strat.maturity(thresholds) == Maturity.DETERMINISTIC


def test_compiler_rejection_demotes():
    from skill_compiler import compile_from_records

    records = [
        {
            "task_name": "T",
            "outcome": "approved",
            "original_request": {"step_id": 1, "step_name": "s"},
            "drafted_action": "do X",
        }
        for _ in range(6)
    ]
    records.append(
        {
            "task_name": "T",
            "outcome": "rejected",
            "original_request": {"step_id": 1, "step_name": "s"},
        }
    )
    skill = compile_from_records("T", records)
    assert skill.maturity_for(1) == Maturity.DETERMINISTIC


def test_skill_generalises_across_inputs():
    """A skill trained on MSFT runs should replay with AAPL when given new inputs."""
    from skill_compiler import compile_from_records

    records = [
        {
            "task_name": "T",
            "outcome": "approved",
            "original_request": {"step_id": 1, "step_name": "s"},
            "drafted_action": "Read the current MSFT price and record it.",
            "inputs": {"symbol": "MSFT"},
        }
        for _ in range(6)
    ]
    skill = compile_from_records("T", records)

    # Exemplar is stored templated, not tied to MSFT.
    best = skill.strategy(1).best_exemplar()
    assert "{symbol}" in best
    assert "MSFT" not in best

    # Replaying with AAPL inputs fills the placeholder, never calling the LLM.
    action, source = skill.draft_step(
        1,
        offline=True,
        llm_draft=lambda ex: "SHOULD_NOT_BE_USED",
        inputs={"symbol": "AAPL"},
    )
    assert "AAPL" in action
    assert "MSFT" not in action
    assert source.startswith("skill:")


def test_classify_execution():
    assert tools.classify_execution("MSFT is trading at 1 USD") == "success"
    assert tools.classify_execution("[mock] simulated $1") == "failed"
    assert tools.classify_execution("  [manual] no tool bound") == "manual"


def test_failure_caps_autonomy():
    thresholds = {"primed": 1, "deterministic": 3, "autonomous": 5}
    strat = StepStrategy(step_id=1, step_name="s")
    for _ in range(6):
        strat.add_approval("do X")
    assert strat.maturity(thresholds) == Maturity.AUTONOMOUS
    strat.failures = 1
    assert strat.maturity(thresholds) == Maturity.DETERMINISTIC


def test_compiler_failure_demotes():
    from skill_compiler import compile_from_records

    records = [
        {
            "task_name": "T",
            "outcome": "approved",
            "original_request": {"step_id": 1, "step_name": "s"},
            "drafted_action": "do X",
            "execution_status": "failed",
        }
        for _ in range(6)
    ]
    skill = compile_from_records("T", records)
    assert skill.strategy(1).failures == 6
    assert skill.maturity_for(1) == Maturity.DETERMINISTIC


class _FakeCosmos:
    """Minimal stand-in for a Cosmos container (dict-backed)."""

    def __init__(self):
        self.items: dict[str, dict] = {}

    def read_item(self, item, partition_key):
        if item not in self.items:
            raise KeyError(item)
        return self.items[item]

    def upsert_item(self, body):
        self.items[body["id"]] = body


def test_cosmos_checkpointer_survives_restart():
    """A paused HITL session persisted by one saver instance is recoverable by
    a fresh instance sharing the same backing store (simulates a restart)."""
    from checkpointer import CosmosCheckpointSaver
    from langgraph.graph import StateGraph, START, END

    fake = _FakeCosmos()

    def build():
        g = StateGraph(dict)
        g.add_node("draft", lambda s: {"n": s.get("n", 0) + 1})
        g.add_node("human_review", lambda s: s)
        g.add_edge(START, "draft")
        g.add_edge("draft", "human_review")
        g.add_edge("human_review", END)
        return g

    cfg = {"configurable": {"thread_id": "t-1"}}

    saver_a = CosmosCheckpointSaver("x", container=fake)
    graph_a = build().compile(
        checkpointer=saver_a, interrupt_before=["human_review"]
    )
    for _ in graph_a.stream({"n": 0}, config=cfg):
        pass
    # Paused before human_review; a doc was persisted to the fake store.
    assert "t-1" in fake.items

    # New process/instance: fresh saver over the same store recovers state.
    saver_b = CosmosCheckpointSaver("x", container=fake)
    graph_b = build().compile(
        checkpointer=saver_b, interrupt_before=["human_review"]
    )
    snapshot = graph_b.get_state(cfg)
    assert snapshot.values.get("n") == 1
    assert snapshot.next == ("human_review",)


def test_embeddings_cosine():
    import embeddings

    assert embeddings.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert embeddings.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert embeddings.cosine([], [1.0]) == 0.0


def test_semantic_tool_binding(monkeypatch):
    """With embeddings available, a step binds to the semantically closest tool."""
    import embeddings

    tools._TOOL_VEC_CACHE.clear()
    monkeypatch.setattr(embeddings, "is_available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_one", lambda q: [1.0, 0.0])
    monkeypatch.setattr(
        embeddings,
        "embed",
        lambda texts: [
            [1.0, 0.0] if "stock" in t.lower() else [0.0, 1.0] for t in texts
        ],
    )

    catalog = [
        {"name": "get_stock_price", "server": "s", "description": "fetch a quote"},
        {"name": "submit_expense_report", "server": "s", "description": "expenses"},
    ]
    step = {"name": "Check price", "description": "get the current stock price"}
    bound = tools._resolve_mcp_semantic(catalog, step, "")
    assert bound is not None
    server, name, _args = bound
    assert name == "get_stock_price"


def test_content_safety_unconfigured(monkeypatch):
    import content_safety

    monkeypatch.delenv("AZURE_AI_ENDPOINT", raising=False)
    monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
    verdict = content_safety.screen("hello")
    assert verdict["allowed"] is True
    assert verdict["checked"] is False


def test_content_safety_blocks_high_severity(monkeypatch):
    import io
    import json as _json

    import content_safety

    monkeypatch.setenv(
        "AZURE_AI_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p/openai/v1",
    )
    monkeypatch.setattr(content_safety, "_token", lambda: "tok")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = {
        "categoriesAnalysis": [
            {"category": "Violence", "severity": 6},
            {"category": "Hate", "severity": 0},
        ]
    }
    monkeypatch.setattr(
        content_safety.urllib.request,
        "urlopen",
        lambda req, timeout=15: _Resp(_json.dumps(payload).encode()),
    )
    verdict = content_safety.screen("some action")
    assert verdict["checked"] is True
    assert verdict["max_severity"] == 6
    assert verdict["allowed"] is False


def test_content_safety_allows_low_severity(monkeypatch):
    import io
    import json as _json

    import content_safety

    monkeypatch.setenv(
        "AZURE_AI_ENDPOINT",
        "https://x.services.ai.azure.com/api/projects/p/openai/v1",
    )
    monkeypatch.setattr(content_safety, "_token", lambda: "tok")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = {"categoriesAnalysis": [{"category": "Hate", "severity": 2}]}
    monkeypatch.setattr(
        content_safety.urllib.request,
        "urlopen",
        lambda req, timeout=15: _Resp(_json.dumps(payload).encode()),
    )
    verdict = content_safety.screen("read a stock price")
    assert verdict["checked"] is True
    assert verdict["allowed"] is True


def test_drift_report_flags_recent_failures():
    import monitoring

    # 10 clean older runs, then 5 recent failures -> drift.
    traces = []
    for i in range(10):
        traces.append({"timestamp": f"2026-01-01T00:00:{i:02d}", "outcome": "approved",
                       "execution_status": "success"})
    for i in range(5):
        traces.append({"timestamp": f"2026-02-01T00:00:{i:02d}", "outcome": "approved",
                       "execution_status": "failed"})
    report = monitoring.drift_report(traces, window=5)
    assert report["recent"]["failure_rate"] == 1.0
    assert report["baseline"]["failure_rate"] == 0.0
    assert report["drifting"] is True


def test_drift_report_stable():
    import monitoring

    traces = [
        {"timestamp": f"2026-01-01T00:00:{i:02d}", "outcome": "approved",
         "execution_status": "success"}
        for i in range(12)
    ]
    report = monitoring.drift_report(traces, window=5)
    assert report["drifting"] is False


def test_distilled_used_in_prompt():
    from skill import Skill, StepStrategy

    skill = Skill(task_name="T", slug="t")
    strat = StepStrategy(step_id=1, step_name="Read price")
    strat.add_approval("read the MSFT price")
    strat.distilled = "Read the {symbol} price and cite the source."
    skill.strategies["1"] = strat
    prompt = skill.to_prompt()
    assert "cite the source" in prompt


def test_edit_learns_executed_action():
    """A human-edited action (executed) becomes the learned exemplar, not the draft."""
    from main import _extract_approved_traces
    from models import WorkflowSchema, WorkflowStep

    schema = WorkflowSchema(
        task_name="T",
        summary="s",
        steps=[WorkflowStep(id=1, name="Step one", description="do it")],
    )
    final_state = {
        "inputs": {},
        "history": [
            {"event": "draft", "step_index": 0, "action": "LLM draft text"},
            {"event": "human_review", "step_index": 0, "decision": "approve"},
            {
                "event": "execute",
                "step_index": 0,
                "action": "HUMAN EDITED action",
                "result": "ok",
                "status": "success",
            },
        ],
    }
    traces = _extract_approved_traces(schema, final_state)
    assert len(traces) == 1
    assert traces[0]["drafted_action"] == "HUMAN EDITED action"


def test_nyes_required_approvals(monkeypatch):
    import approvals

    monkeypatch.delenv("REQUIRED_APPROVALS", raising=False)
    high = {"name": "Submit", "description": "submit to finance", "human_required": True}
    low = {"name": "Read", "description": "read a value"}
    # Default is single-approver HITL (no dead-end for solo users).
    assert approvals.required_approvals(high) == 1
    assert approvals.required_approvals(low) == 1
    # N-eyes is opt-in and applies only to high-risk steps.
    assert approvals.required_approvals(high, 2) == 2
    assert approvals.required_approvals(low, 2) == 1
    assert approvals.is_cleared({"a"}, 2) is False
    assert approvals.is_cleared({"a", "b"}, 2) is True


def test_fabric_demo_schema():
    from interviewer import demo_schema

    schema = demo_schema("fabric")
    assert schema.task_name == "Build a Fabric Data Pipeline"
    assert len(schema.steps) == 6
    # "Run the pipeline" is the high-risk, human-required step.
    assert schema.steps[4].human_required is True


# --- Governed shell / script tool -----------------------------------------
def test_shell_disabled_by_default(monkeypatch):
    import shell

    monkeypatch.delenv("SHELL_TOOL_ENABLED", raising=False)
    assert shell.is_enabled() is False
    # A shell step is a no-op (falls through) when the tool is off.
    assert shell.try_execute({"name": "x"}, "run `git status`") is None
    # Direct call is refused too.
    assert shell.run_command("git status")["ok"] is False


def test_shell_allowlist_and_operators(monkeypatch):
    import shell

    monkeypatch.setenv("SHELL_TOOL_ENABLED", "1")
    # Not on the allowlist.
    assert "allowlist" in (shell.run_command("notarealcmd --x")["error"] or "")
    # Chaining / pipes / redirection rejected for ad-hoc commands.
    assert "chaining" in (shell.run_command("git status && rm x")["error"] or "").lower()
    assert shell.run_command("cat a | grep b")["error"]
    # Deny-list catches destructive patterns.
    assert shell.run_command("rm -rf /")["error"]


def test_shell_runs_allowed_command(monkeypatch):
    import shell

    monkeypatch.setenv("SHELL_TOOL_ENABLED", "1")
    res = shell.run_command("python --version")
    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert "Python" in (res["stdout"] + res["stderr"])


def test_shell_script_traversal_blocked(monkeypatch):
    import shell

    monkeypatch.setenv("SHELL_TOOL_ENABLED", "1")
    res = shell.run_script("../server.py", [])
    assert res["ok"] is False
    assert "not found" in (res["error"] or "")


def test_shell_extracts_backtick_command(monkeypatch):
    import shell

    monkeypatch.setenv("SHELL_TOOL_ENABLED", "1")
    out = shell.try_execute({"name": "Check status"}, "Run `python --version` now")
    assert out is not None
    assert out.startswith("[shell]")
    assert "Python" in out


def test_shell_step_is_high_risk():
    # A step that runs a command must stay human-gated.
    assert tools.is_high_risk({"name": "Run command", "description": "run script build"}) is True
    assert tools.is_high_risk({"name": "Read a value", "description": "look it up"}) is False


def test_list_skills_returns_saved(tmp_path, monkeypatch):
    import store as store_mod
    from skill import Skill

    monkeypatch.setattr(store_mod, "SKILLS_DIR", tmp_path)
    fs = store_mod.FileStore()
    skill = Skill(task_name="Lib Demo")
    skill.ensure_strategy(1, "Step one").add_approval("do the thing")
    fs.save_skill(skill)
    listed = fs.list_skills()
    assert any(s.task_name == "Lib Demo" for s in listed)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
