"""skill.py — The learned, portable capability artifact.

A :class:`Skill` is the *output* of the harness: a versioned bundle that
captures, per workflow step, how a human-approved action looks. As approvals
accumulate, each step climbs a maturity ladder:

    COLD          no approved exemplars yet — the LLM (or mock) drafts fresh.
    PRIMED        >=1 exemplar — the LLM is primed with examples; offline we
                  replay the best exemplar. Human still reviews.
    DETERMINISTIC the same action has been approved enough times — replay it
                  verbatim; human only confirms.
    AUTONOMOUS    approved so consistently that the executor stops asking and
                  runs it automatically.

Skills are pure data + light logic (no LangGraph/LangChain imports) so they can
be unit-tested and inspected standalone.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field


class Maturity(str, Enum):
    """Automation level a single step has earned."""

    COLD = "cold"
    PRIMED = "primed"
    DETERMINISTIC = "deterministic"
    AUTONOMOUS = "autonomous"


# How many *identical* approved actions are needed to reach each level.
DEFAULT_THRESHOLDS: dict[str, int] = {
    "primed": 1,
    "deterministic": 3,
    "autonomous": 5,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(text: str) -> str:
    """Collapse whitespace/case so near-identical drafts count as the same."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "workflow"


def templatize(action: str, inputs: Optional[dict] = None) -> str:
    """Replace input VALUES in an action with ``{key}`` placeholders so a skill
    generalises across runs (e.g. 'read MSFT price' -> 'read {symbol} price').
    Longer values are replaced first to avoid partial matches.
    """
    out = action or ""
    items = sorted(
        ((k, str(v).strip()) for k, v in (inputs or {}).items() if str(v).strip()),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    for key, value in items:
        out = re.sub(re.escape(value), "{" + key + "}", out, flags=re.IGNORECASE)
    return out


def fill_template(template: str, inputs: Optional[dict] = None) -> str:
    """Fill ``{key}`` placeholders in a templated action from the run's inputs."""
    out = template or ""
    for key, value in (inputs or {}).items():
        out = out.replace("{" + key + "}", str(value))
    return out


class Exemplar(BaseModel):
    """A single human-approved action for a step."""

    action: str
    feedback: str = ""
    timestamp: str = Field(default_factory=_now)


class StepStrategy(BaseModel):
    """Everything the skill has learned about drafting one step."""

    step_id: int
    step_name: str
    step_description: str = ""
    exemplars: list[Exemplar] = Field(default_factory=list)
    # Times a human rejected/modified this step; caps autonomy (demote-on-reject).
    rejections: int = 0
    # Times execution failed (tool errored/degraded); caps autonomy (demote-on-failure).
    failures: int = 0
    # LLM-distilled, generalised instruction compiled from the exemplars (#12).
    distilled: Optional[str] = None

    @property
    def approvals(self) -> int:
        return len(self.exemplars)

    def add_approval(self, action: str, feedback: str = "") -> None:
        self.exemplars.append(Exemplar(action=action, feedback=feedback))

    def _dominant(self) -> tuple[Optional[str], int]:
        """Return the most frequently approved action and its count."""
        if not self.exemplars:
            return None, 0
        counts = Counter(_normalize(e.action) for e in self.exemplars)
        norm, count = counts.most_common(1)[0]
        # Return the original (non-normalized) text of a matching exemplar.
        for e in self.exemplars:
            if _normalize(e.action) == norm:
                return e.action, count
        return None, 0

    def best_exemplar(self) -> Optional[str]:
        """The action to replay: the most-approved one, else the latest."""
        action, _ = self._dominant()
        if action is not None:
            return action
        return self.exemplars[-1].action if self.exemplars else None

    def maturity(self, thresholds: dict[str, int]) -> Maturity:
        """Grade this step from its approval history.

        A step that has ever been rejected OR whose execution ever failed cannot
        reach AUTONOMOUS: once burned, a human keeps confirming it (it is capped
        at DETERMINISTIC).
        """
        _, dominant_count = self._dominant()
        if self.approvals == 0:
            return Maturity.COLD
        if (
            dominant_count >= thresholds.get("autonomous", 5)
            and self.rejections == 0
            and self.failures == 0
        ):
            return Maturity.AUTONOMOUS
        if dominant_count >= thresholds.get("deterministic", 3):
            return Maturity.DETERMINISTIC
        if self.approvals >= thresholds.get("primed", 1):
            return Maturity.PRIMED
        return Maturity.COLD


class Skill(BaseModel):
    """A portable, versioned capability compiled from approved runs."""

    task_name: str
    slug: str = ""
    summary: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    strategies: dict[str, StepStrategy] = Field(default_factory=dict)
    thresholds: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    version: int = 1
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    # Set by Deploy: a durable "published to the registry" marker.
    published: bool = False
    published_at: Optional[str] = None

    def model_post_init(self, _context: object) -> None:  # pydantic v2 hook
        if not self.slug:
            self.slug = slugify(self.task_name)

    # -- strategy access ---------------------------------------------------
    def strategy(self, step_id: int) -> Optional[StepStrategy]:
        return self.strategies.get(str(step_id))

    def ensure_strategy(
        self, step_id: int, step_name: str, step_description: str = ""
    ) -> StepStrategy:
        key = str(step_id)
        if key not in self.strategies:
            self.strategies[key] = StepStrategy(
                step_id=step_id,
                step_name=step_name,
                step_description=step_description,
            )
        return self.strategies[key]

    # -- maturity ----------------------------------------------------------
    def maturity_for(self, step_id: int) -> Maturity:
        strat = self.strategy(step_id)
        return strat.maturity(self.thresholds) if strat else Maturity.COLD

    def is_autonomous(self, step_id: int) -> bool:
        return self.maturity_for(step_id) == Maturity.AUTONOMOUS

    def overall_maturity(self) -> Maturity:
        """The weakest step's maturity — the whole skill is only as mature as that."""
        if not self.strategies:
            return Maturity.COLD
        order = [Maturity.COLD, Maturity.PRIMED, Maturity.DETERMINISTIC, Maturity.AUTONOMOUS]
        return min(
            (self.maturity_for(s.step_id) for s in self.strategies.values()),
            key=order.index,
        )

    # -- drafting ----------------------------------------------------------
    def draft_step(
        self,
        step_id: int,
        offline: bool,
        llm_draft: Callable[[list[Exemplar]], str],
        feedback: str = "",
        inputs: Optional[dict] = None,
    ) -> tuple[str, str]:
        """Produce the next action, deferring to learned exemplars when mature.

        Replayed exemplars are templated (``{key}`` placeholders), so ``inputs``
        fills them for the current run, letting one skill serve many inputs.

        Returns ``(action, source)`` where ``source`` explains provenance, e.g.
        ``skill:autonomous``, ``skill:deterministic``, ``skill:primed-replay``,
        ``llm`` or ``mock``.
        """
        strat = self.strategy(step_id)
        exemplars = strat.exemplars if strat else []
        best = strat.best_exemplar() if strat else None
        mat = self.maturity_for(step_id)

        # Rejection/modification always forces a fresh draft.
        if feedback:
            return llm_draft(exemplars), ("mock" if offline else "llm")

        if best and mat in (Maturity.DETERMINISTIC, Maturity.AUTONOMOUS):
            return fill_template(best, inputs), f"skill:{mat.value}"

        if best and mat == Maturity.PRIMED and offline:
            return fill_template(best, inputs), "skill:primed-replay"

        # COLD, or PRIMED online (LLM drafts, primed with exemplars).
        return llm_draft(exemplars), ("mock" if offline else "llm")

    # -- persistence -------------------------------------------------------
    def save(self, skills_dir: Path) -> Path:
        skills_dir.mkdir(parents=True, exist_ok=True)
        self.updated_at = _now()
        path = skills_dir / f"{self.slug}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "Skill":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def load_for_task(cls, task_name: str, skills_dir: Path) -> Optional["Skill"]:
        path = skills_dir / f"{slugify(task_name)}.json"
        return cls.load(path) if path.exists() else None

    # -- reporting ---------------------------------------------------------
    def report_rows(self) -> list[dict[str, object]]:
        """Rows for a maturity table: one per step, ordered by id."""
        rows: list[dict[str, object]] = []
        for strat in sorted(self.strategies.values(), key=lambda s: s.step_id):
            _, dominant = strat._dominant()
            rows.append(
                {
                    "step_id": strat.step_id,
                    "step_name": strat.step_name,
                    "approvals": strat.approvals,
                    "dominant": dominant,
                    "rejections": strat.rejections,
                    "failures": strat.failures,
                    "distilled": bool(strat.distilled),
                    "maturity": strat.maturity(self.thresholds).value,
                }
            )
        return rows

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def to_prompt(self) -> str:
        """Render the skill as a model-agnostic few-shot prompt.

        The output is plain text any LLM (Claude, GPT, Gemini, local) can consume:
        the workflow, its inputs, and the human-approved action per step. This is
        why a skill learned here is portable across models.
        """
        lines = [
            f'You are executing the workflow "{self.task_name}".',
            self.summary,
            f"Required inputs: {', '.join(self.required_inputs) or 'none'}.",
            "",
            "Produce the single next action for the requested step. Follow these "
            "human-approved examples (learned by supervised approval):",
        ]
        for strat in sorted(self.strategies.values(), key=lambda s: s.step_id):
            example = (
                strat.distilled
                or strat.best_exemplar()
                or "(no approved example yet)"
            )
            lines.append(f"- Step {strat.step_id} ({strat.step_name}): {example}")
        return "\n".join(line for line in lines if line)
