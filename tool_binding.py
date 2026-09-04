"""tool_binding.py — choose a tool and its arguments as STRUCTURED data.

Instead of parsing tool arguments out of a drafted sentence (fragile, per-domain
heuristics), ask the model to emit an explicit ``{tool, args}`` decision grounded
on the available tool catalog (MCP tools + built-ins) and the run's inputs. The
model resolves entities itself (e.g. a company name -> its stock ticker), so the
executor never has to guess.

Offline, disabled, or on any error this returns ``None`` and the caller falls
back to its heuristic resolver, so demos and unit tests are unaffected.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A structured tool decision for one workflow step."""

    tool: str = Field(description="Tool name from the catalog, or 'none'.")
    server: Optional[str] = Field(
        default=None, description="MCP server name when the tool is an MCP tool."
    )
    args_json: str = Field(
        default="{}",
        description='JSON object of arguments, e.g. {"query": "Visa"}.',
    )


def _enabled() -> bool:
    return os.getenv("STRUCTURED_TOOL_BINDING", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _params(schema: Any) -> str:
    """Comma-joined parameter names from an MCP tool's JSON input schema."""
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            return ", ".join(props.keys())
    return ""


def _format_catalog(mcp_tools: list[dict[str, Any]]) -> str:
    names = {t.get("name") for t in (mcp_tools or [])}
    lines: list[str] = []
    for t in mcp_tools or []:
        params = _params(t.get("input_schema"))
        lines.append(
            f"- {t.get('name')} (mcp server: {t.get('server')}): "
            f"{t.get('description', '')}. args: {{{params}}}"
        )
    if "get_stock_price" not in names:
        lines.append(
            '- get_stock_price (builtin): latest market price. '
            'args: {"query": "<company name or ticker>"}'
        )
    lines.append(
        '- browse_web (builtin): open a URL or run a web search and read the '
        'page. args: {"target": "<url or search query>"}'
    )
    lines.append("- none: no external tool is needed (pure reasoning / present).")
    return "\n".join(lines)


def bind_tool(
    step: dict[str, Any],
    action: str,
    inputs: Optional[dict[str, Any]],
    mcp_tools: list[dict[str, Any]],
    model: Optional[str] = None,
) -> Optional[ToolCall]:
    """Return a structured tool decision, or ``None`` to fall back to heuristics."""
    if not _enabled():
        return None

    import llm_provider

    if llm_provider.is_offline():
        return None
    chat = llm_provider.build_chat_model(deployment=model)
    if chat is None:
        return None

    system = SystemMessage(
        content=(
            "You bind ONE workflow step to exactly one tool and produce its "
            "arguments as concrete values. Resolve entities yourself (for "
            "example, map a company name to its official stock ticker). If the "
            "step needs no external tool (pure reasoning, summarising, or "
            "presenting already-known information), return tool='none'. Only "
            "choose a tool that appears in the catalog. Put arguments in "
            "args_json as a JSON object."
        )
    )
    human = HumanMessage(
        content=(
            f"Step: {step.get('name', '')}: {step.get('description', '')}\n"
            f"Planned action: {action}\n"
            f"Run inputs: {json.dumps(inputs or {}, ensure_ascii=False)}\n\n"
            f"Tool catalog:\n{_format_catalog(mcp_tools)}"
        )
    )
    try:
        structured = chat.with_structured_output(ToolCall)
        result = structured.invoke([system, human])
    except Exception:
        return None
    if not result or not (result.tool or "").strip():
        return None
    return result
