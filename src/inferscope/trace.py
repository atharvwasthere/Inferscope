"""Trace-id propagation primitives.

Pure module — contextvar + helpers only, no web framework imports.
Safe to import from the SDK and any service. The ASGI middleware that
populates the contextvar is a server concern and stays in ``obs.middleware``.
"""
import contextvars
from uuid import uuid4

TRACE_HEADER = "X-Trace-Id"

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def get_trace_id() -> str:
    return trace_id_var.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    return trace_id_var.set(trace_id)


def new_trace_id() -> str:
    return str(uuid4())
