"""shell.py — a governed shell / script execution tool for the harness.

Two capabilities a workflow step can bind to:

1. **Ad-hoc commands** — a fresh one-off command written for whatever the task
   needs (check files, run ``git``, install a package, a quick test/build, a
   small fix). Nothing reusable sits behind it.
2. **Prewritten scripts** — invoke a script that already lives in a governed
   ``scripts/`` folder (PowerShell ``.ps1``, Python ``.py``, batch
   ``.cmd``/``.bat``, or shell ``.sh``) with the right flags.

SAFETY POSTURE — this is real code execution, so it is deliberately locked down:

* **Disabled by default.** Set ``SHELL_TOOL_ENABLED=1`` to turn it on. It is
  intentionally *not* enabled in the public cloud deployment.
* **Allow-listed.** Only base commands in ``SHELL_ALLOWED_CMDS`` may run ad-hoc.
* **Deny-listed.** Obvious destructive patterns are refused outright.
* **Operator-free.** Ad-hoc commands may not chain / redirect / pipe
  (no ``;`` ``&&`` ``|`` ``>`` `` ` `` ``$()``), so exactly one inspectable
  process is spawned with ``shell=False``.
* **Sandboxed to the repo.** Scripts must live inside ``scripts/`` — path
  traversal is blocked.
* **Time-boxed and size-capped.**
* **Human-gated.** Shell steps are treated as high-risk (see
  ``tools.is_high_risk``) so the co-pilot always pauses for approval, and the
  drafted command is screened by Content Safety before it runs.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).parent

# Base commands permitted for ad-hoc execution (override with SHELL_ALLOWED_CMDS).
# PowerShell is intentionally absent here: arbitrary PowerShell is reached only
# through governed prewritten .ps1 scripts, not free-form ad-hoc commands.
_DEFAULT_ALLOWED = (
    "git,python,python3,pip,pip3,pytest,py,node,npm,npx,pnpm,yarn,"
    "dotnet,go,cargo,rustc,java,mvn,gradle,ruby,gem,"
    "echo,ls,dir,type,cat,rg,grep,find,where,which,whoami,pwd,tree,head,tail"
)

# Script extensions -> the interpreter prefix used to run them.
_SCRIPT_INTERP: dict[str, list[str]] = {
    ".ps1": [
        "powershell", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File",
    ],
    ".py": [sys.executable or "python"],
    ".sh": ["bash"],
    ".cmd": ["cmd", "/c"],
    ".bat": ["cmd", "/c"],
}

# Refused outright, anywhere in a command or a script argument (case-insensitive).
_DENY_PATTERNS = (
    r"rm\s+-rf?", r"\brmdir\s+/s", r"\bdel\s+/[sfq]", r"remove-item[^\n]*-recurse",
    r"\bformat\s+[a-z]:", r"\bmkfs\b", r"\bdd\s+if=", r">\s*/dev/sd",
    r":\(\)\s*\{", r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r"reg\s+delete", r"\bdiskpart\b", r"\bfdisk\b",
    r"git\s+push[^\n]*--force", r"--no-verify", r"\bgit\s+reset\s+--hard",
    r"invoke-expression", r"\biex\b", r"downloadstring", r"\bcurl\b[^\n]*\|\s*(ba)?sh",
    r"\bsudo\b", r"\brunas\b", r"net\s+user", r"chmod\s+777\s+/",
)

# Shell metacharacters that would let an ad-hoc command escape the single-process
# sandbox. Their presence rejects the command (use a prewritten script instead).
_OPERATOR_RE = re.compile(r"[;&|`]|\$\(|\|\||&&|>>|<<|(?<!\d)>(?!\=)|<")

_MAX_OUTPUT = 6000  # characters retained per stream


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    return os.getenv("SHELL_TOOL_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _allowed_cmds() -> set[str]:
    raw = os.getenv("SHELL_ALLOWED_CMDS", _DEFAULT_ALLOWED)
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def _timeout() -> int:
    try:
        return max(1, int(os.getenv("SHELL_TIMEOUT", "60")))
    except ValueError:
        return 60


def _scripts_dir() -> Path:
    return (BASE_DIR / os.getenv("SHELL_SCRIPTS_DIR", "scripts")).resolve()


def scripts_dir_rel() -> str:
    try:
        return str(_scripts_dir().relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        return str(_scripts_dir())


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def _base_name(token: str) -> str:
    name = os.path.basename(token.strip().strip("\"'"))
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".py", ".sh"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    return name.lower()


def _denied(text: str) -> Optional[str]:
    low = (text or "").lower()
    for pat in _DENY_PATTERNS:
        if re.search(pat, low):
            return pat
    return None


def _has_operator(command: str) -> bool:
    return bool(_OPERATOR_RE.search(command or ""))


def _clip(text: str) -> str:
    text = text or ""
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + f"\n… (+{len(text) - _MAX_OUTPUT} more chars)"
    return text


def _err(command: str, message: str) -> dict[str, Any]:
    return {
        "ok": False, "command": command, "exit_code": None,
        "stdout": "", "stderr": "", "error": message,
    }


def _spawn(argv: list[str], label: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv, cwd=str(BASE_DIR), capture_output=True, text=True,
            timeout=_timeout(), shell=False,
        )
    except FileNotFoundError:
        return _err(label, f"Executable not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return _err(label, f"Timed out after {_timeout()}s.")
    except Exception as exc:  # pragma: no cover - defensive
        return _err(label, f"{exc.__class__.__name__}: {exc}")
    return {
        "ok": proc.returncode == 0, "command": label, "exit_code": proc.returncode,
        "stdout": _clip(proc.stdout), "stderr": _clip(proc.stderr), "error": None,
    }


# ---------------------------------------------------------------------------
# Capability 1 — ad-hoc commands
# ---------------------------------------------------------------------------
def run_command(command: str) -> dict[str, Any]:
    """Run a single, allow-listed, operator-free command with ``shell=False``."""
    command = (command or "").strip()
    if not is_enabled():
        return _err(command, "Shell tool is disabled. Set SHELL_TOOL_ENABLED=1 to enable it.")
    if not command:
        return _err(command, "Empty command.")
    bad = _denied(command)
    if bad:
        return _err(command, f"Refused: command matches a blocked pattern ({bad}).")
    if _has_operator(command):
        return _err(
            command,
            "Refused: chaining, pipes, and redirection are not allowed in ad-hoc "
            "commands. Put multi-step logic in a prewritten script instead.",
        )
    try:
        argv = shlex.split(command, posix=(os.name != "nt"))
    except ValueError as exc:
        return _err(command, f"Could not parse command: {exc}")
    if not argv:
        return _err(command, "Empty command.")
    base = _base_name(argv[0])
    allowed = _allowed_cmds()
    if base not in allowed:
        return _err(
            command,
            f"Refused: '{base}' is not in the allowlist. Allowed: "
            f"{', '.join(sorted(allowed))}.",
        )
    return _spawn(argv, label=command)


# ---------------------------------------------------------------------------
# Capability 2 — prewritten scripts
# ---------------------------------------------------------------------------
def _resolve_script(name: str) -> Optional[Path]:
    name = (name or "").strip().strip("\"'`")
    if not name:
        return None
    root = _scripts_dir()
    candidate = (root / name).resolve()
    # Must stay inside scripts/ (blocks ../ traversal and absolute escapes).
    if root != candidate and root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix.lower() not in _SCRIPT_INTERP:
        return None
    return candidate


def _interpreter_for(path: Path) -> list[str]:
    return list(_SCRIPT_INTERP[path.suffix.lower()])


def run_script(name: str, args: Optional[list[Any]] = None) -> dict[str, Any]:
    """Invoke a prewritten script from ``scripts/`` with the given flags."""
    args = [str(a) for a in (args or [])]
    if not is_enabled():
        return _err(name, "Shell tool is disabled. Set SHELL_TOOL_ENABLED=1 to enable it.")
    path = _resolve_script(name)
    if path is None:
        return _err(
            name,
            f"Script '{name}' not found under {scripts_dir_rel()}/ "
            "(only .ps1/.py/.cmd/.bat/.sh inside that folder are allowed).",
        )
    for arg in args:
        bad = _denied(arg)
        if bad:
            return _err(name, f"Refused: argument matches a blocked pattern ({bad}).")
    argv = _interpreter_for(path) + [str(path), *args]
    label = f"{path.name} {' '.join(args)}".strip()
    return _spawn(argv, label=label)


def list_scripts() -> list[dict[str, Any]]:
    """Prewritten scripts available to invoke (name, ext, size in bytes)."""
    root = _scripts_dir()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix.lower() in _SCRIPT_INTERP:
            out.append(
                {"name": path.name, "ext": path.suffix.lower(), "size": path.stat().st_size}
            )
    return out


def status() -> dict[str, Any]:
    return {
        "enabled": is_enabled(),
        "allowed_cmds": sorted(_allowed_cmds()),
        "scripts_dir": scripts_dir_rel(),
        "timeout": _timeout(),
        "scripts": list_scripts(),
    }


# ---------------------------------------------------------------------------
# Step binding — called by tools.execute_step
# ---------------------------------------------------------------------------
_SIGNAL_WORDS = (
    "command", "shell", "terminal", "powershell", "cmd", "script", "git ",
    "pip install", "npm install", "npm run", "run tests", "pytest", "build",
    "install package", "checkout", "commit", "clone",
)


def _has_signal(blob: str) -> bool:
    return any(w in blob for w in _SIGNAL_WORDS)


def _split_args(rest: str) -> list[str]:
    rest = (rest or "").strip().strip("`")
    if not rest:
        return []
    try:
        return shlex.split(rest, posix=(os.name != "nt"))
    except ValueError:
        return rest.split()


def _extract_command_text(text: str, has_signal: bool) -> Optional[str]:
    # 1. Fenced code block ``` ... ```
    m = re.search(r"```[a-zA-Z0-9]*\s*(.+?)```", text, re.S)
    if m:
        return m.group(1).strip().splitlines()[0].strip()
    # 2. Inline backticks `cmd`
    m = re.search(r"`([^`\n]+)`", text)
    if m:
        return m.group(1).strip()
    # 3. An explicit prefix on any line.
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        for pfx in ("run:", "command:", "shell:", "cmd:", "powershell:", "ps:", "$ ", "> "):
            if low.startswith(pfx):
                return s[len(pfx):].strip()
    # 4. Bare command: first token is allow-listed and the step clearly asked
    #    for a shell/command action (avoids hijacking ordinary prose).
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if first_line:
        first_tok = first_line.split()[0] if first_line.split() else ""
        if has_signal and _base_name(first_tok) in _allowed_cmds():
            return first_line
    return None


def _extract(step: dict[str, Any], action: str) -> Optional[tuple[str, Any]]:
    text = (action or "").strip()
    if not text:
        return None
    blob = f"{step.get('name', '')} {step.get('description', '')} {text}".lower()
    # Explicit "run/invoke/execute/call [the] script NAME --flags".
    sm = re.search(r"(?i)(?:run|invoke|execute|call)\s+(?:the\s+)?script\s+(\S+)(.*)", text)
    if sm:
        return ("script", (sm.group(1).strip("`'\""), _split_args(sm.group(2))))
    cmd = _extract_command_text(text, _has_signal(blob))
    if not cmd:
        return None
    # If the command's first token names a script in scripts/, run it as one.
    tokens = cmd.split()
    if tokens and _resolve_script(tokens[0]) is not None:
        return ("script", (tokens[0], _split_args(cmd[len(tokens[0]):])))
    return ("cmd", cmd)


def _format(res: dict[str, Any]) -> str:
    if res.get("error"):
        # "[mock]" prefix => tools.classify_execution grades it a failure so a
        # broken command never lets the step graduate to autonomous.
        return f"[mock] Shell refused/failed: {res['error']}"
    prefix = "[shell]" if res["ok"] else "[mock]"
    tail = "" if res["ok"] else " (non-zero exit)"
    body = res["stdout"] or res["stderr"] or "(no output)"
    return f"{prefix} `{res['command']}` exit {res['exit_code']}{tail}\n{body}".strip()


def try_execute(step: dict[str, Any], action: str) -> Optional[str]:
    """If the step is a shell/script action, run it and return a result string.

    Returns ``None`` when the tool is disabled or the step is not a shell step,
    so ``execute_step`` falls through to its other tools.
    """
    if not is_enabled():
        return None
    extracted = _extract(step, action)
    if not extracted:
        return None
    kind, payload = extracted
    if kind == "script":
        name, args = payload
        return _format(run_script(name, args))
    return _format(run_command(payload))
