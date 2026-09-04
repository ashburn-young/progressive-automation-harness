"""mcp_fabric_server.py - a demo MCP server for Microsoft Fabric pipeline builds.

Runs over stdio and exposes Fabric-shaped tools (create pipeline, add a copy
activity, create a dataflow, run a pipeline, check status). It is a *simulated*
stand-in for the real Microsoft Fabric MCP server / Fabric REST APIs, following
the same "target system is mocked" honesty as the finance demo: results are
labelled ``[simulated Fabric]`` and use deterministic content-hash ids so they
are reproducible and auditable. Swap this for the real Fabric MCP server to act
on a live workspace.
"""

from __future__ import annotations

import hashlib

from mcp.server.mcpserver import MCPServer

import fabric_client

mcp = MCPServer("fabric-builder")


def _fid(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:8].upper()
    return f"{prefix}-{digest}"


@mcp.tool()
def create_lakehouse(name: str = "harness_lakehouse") -> str:
    """Create a Lakehouse (the destination for ingested tables)."""
    if fabric_client.is_enabled():
        return fabric_client.create_lakehouse(name)
    lid = _fid("LH", name)
    return f"[simulated Fabric] Created lakehouse '{name}' (id {lid})."


@mcp.tool()
def create_pipeline(name: str = "ingest-pipeline", workspace: str = "analytics") -> str:
    """Create a Data Factory pipeline in a Fabric workspace."""
    if fabric_client.is_enabled():
        return fabric_client.create_pipeline(name)
    pid = _fid("PL", name, workspace)
    return (
        f"[simulated Fabric] Created pipeline '{name}' in workspace "
        f"'{workspace}' (id {pid})."
    )


@mcp.tool()
def add_copy_activity(
    pipeline: str = "ingest-pipeline",
    source: str = "OneLake/Files/raw.csv",
    sink: str = "Lakehouse/Tables/raw",
) -> str:
    """Add a Copy activity that moves data from a source to a sink."""
    aid = _fid("ACT", pipeline, source, sink)
    return (
        f"[simulated Fabric] Added copy activity {aid} to '{pipeline}': "
        f"{source} -> {sink}."
    )


@mcp.tool()
def create_dataflow(name: str = "clean-dataflow", query: str = "") -> str:
    """Create a Dataflow Gen2 that applies a transform (Power Query M)."""
    if fabric_client.is_enabled():
        return fabric_client.create_dataflow(name, query)
    did = _fid("DF", name, query)
    detail = f" applying: {query[:60]}" if query else ""
    return f"[simulated Fabric] Created dataflow '{name}' (id {did}){detail}."


@mcp.tool()
def run_pipeline(pipeline: str = "ingest-pipeline") -> str:
    """Trigger a pipeline run and return the run id (a real side effect)."""
    if fabric_client.is_enabled():
        return fabric_client.run_pipeline(pipeline)
    rid = _fid("RUN", pipeline)
    return (
        f"[simulated Fabric] Started pipeline '{pipeline}'. Run {rid} is "
        "InProgress."
    )


@mcp.tool()
def get_pipeline_status(run_id: str = "") -> str:
    """Check the status/result of a pipeline run."""
    if fabric_client.is_enabled():
        return fabric_client.list_pipelines()
    return (
        f"[simulated Fabric] Run {run_id or 'RUN-UNKNOWN'} Succeeded: "
        "1 table written, 0 errors."
    )


if __name__ == "__main__":
    mcp.run("stdio")
