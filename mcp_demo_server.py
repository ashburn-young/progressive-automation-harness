"""mcp_demo_server.py - a small MCP server exposing enterprise-like tools.

Runs over stdio. The harness's :mod:`mcp_client` discovers and calls these
tools, demonstrating how a workflow step binds to a capability provided by an
MCP server rather than hardcoded in the harness. Swap this for your real
enterprise MCP servers (finance, ERP, ticketing, etc.).
"""

from __future__ import annotations

import hashlib

from mcp.server.mcpserver import MCPServer

import tools

mcp = MCPServer("harness-demo-tools")

# Keyword rules for a real (deterministic) receipt classifier.
_CATEGORY_RULES = {
    "travel": ("uber", "lyft", "air", "hotel", "taxi", "train", "flight", "rental"),
    "meals": ("restaurant", "cafe", "coffee", "food", "lunch", "dinner", "bar"),
    "software": ("github", "aws", "azure", "saas", "subscription", "license", "adobe"),
}


@mcp.tool()
def get_stock_price(symbol: str = "MSFT") -> str:
    """Look up the latest market price for a stock ticker symbol."""
    return tools.get_stock_price(symbol)


@mcp.tool()
def categorize_receipts(items: str = "") -> str:
    """Categorize expense receipts into travel, meals, software, or other.

    ``items`` is a comma-separated list of merchant or description strings.
    This is real rule-based classification, not a canned response.
    """
    buckets: dict[str, list[str]] = {}
    for raw in [x.strip() for x in items.split(",") if x.strip()]:
        low = raw.lower()
        category = "other"
        for name, keywords in _CATEGORY_RULES.items():
            if any(k in low for k in keywords):
                category = name
                break
        buckets.setdefault(category, []).append(raw)
    if not buckets:
        return "No receipts were provided to categorize."
    return "; ".join(f"{c}: {', '.join(v)}" for c, v in sorted(buckets.items()))


@mcp.tool()
def submit_expense_report(total: float = 0.0, cost_center: str = "CC-1000") -> str:
    """Record an expense report and return a deterministic confirmation number.

    NOTE: this is a stand-in for a real finance/ERP system (the target system
    is intentionally mocked). The confirmation is a real content hash of the
    inputs, so it is reproducible and auditable.
    """
    digest = hashlib.sha1(f"{total:.2f}|{cost_center}".encode()).hexdigest()[:8].upper()
    return (
        f"[simulated finance system] Recorded expense report: total ${total:.2f}, "
        f"cost center {cost_center}, confirmation EXP-{digest}."
    )


if __name__ == "__main__":
    mcp.run("stdio")
