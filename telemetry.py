"""telemetry.py - OpenTelemetry tracing exported to Azure Application Insights.

Turns the harness into an observable agentic system: every HTTP request and,
more importantly, every drafted/executed workflow step becomes a span with
attributes (step name, provenance source, execution status). Traces flow to the
existing Application Insights resource via ``APPLICATIONINSIGHTS_CONNECTION_STRING``.

All calls are best-effort and fully degrade to no-ops when the SDK isn't
installed or no connection string is set, so local dev and tests are unaffected.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

_ENABLED = False
_TRACER_NAME = "progressive-automation-harness"


def setup_telemetry(app: object | None = None) -> bool:
    """Configure Azure Monitor + instrument FastAPI. Returns True if enabled."""
    global _ENABLED
    if _ENABLED:
        return True
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=conn,
            logger_name=_TRACER_NAME,
        )
    except Exception:
        return False
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except Exception:
            pass
    _ENABLED = True
    return True


def is_enabled() -> bool:
    return _ENABLED


@contextmanager
def span(name: str, **attributes: object) -> Iterator[Optional[object]]:
    """Start a span with attributes; a silent no-op when tracing is unavailable."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(_TRACER_NAME)
        with tracer.start_as_current_span(name) as current:
            for key, value in attributes.items():
                try:
                    current.set_attribute(key, value)
                except Exception:
                    pass
            yield current
    except Exception:
        yield None


def set_attribute(current: object | None, key: str, value: object) -> None:
    """Set an attribute on a span if tracing produced one."""
    if current is None:
        return
    try:
        current.set_attribute(key, value)  # type: ignore[attr-defined]
    except Exception:
        pass
