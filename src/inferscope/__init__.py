"""inferscope — LLM observability SDK.

Public surface is intentionally small. It grows one export at a time, as each
piece earns a caller:

  T5 — ``wrap(client)``, the interceptor that replaces ``LLMWrapper.chat()``
  T9 — ``observe``, the span-tree decorator

What is exported today is the wire contract (``InferenceEvent``, ``Publisher``),
the HTTP transport that ships it, trace-id propagation, and PII handling — the
pieces that already have real callers on both sides of the collector boundary.

The Redis stream constants (``STREAM_KEY``, ``CONSUMER_GROUP``, ``EVENT_FIELD``)
are deliberately NOT re-exported here. They are collector transport internals;
``redis_bus`` imports them from ``inferscope.events`` directly. Putting them on
the client SDK's front door would be public API nobody calls.
"""

from importlib.metadata import version

from inferscope.delivery import DeliveryOutcome
from inferscope.events import InferenceEvent, Publisher
from inferscope.pii_tokenizer import PiiTokenizer
from inferscope.redactor import PATTERNS, redact
from inferscope.trace import (
    TRACE_HEADER,
    get_trace_id,
    new_trace_id,
    set_trace_id,
    trace_id_var,
)
from inferscope.transport import HttpPublisher

# Single source of truth is pyproject.toml — a hardcoded literal here would drift.
__version__ = version("inferscope")

__all__ = [
    "PATTERNS",
    "TRACE_HEADER",
    "DeliveryOutcome",
    "HttpPublisher",
    "InferenceEvent",
    "PiiTokenizer",
    "Publisher",
    "__version__",
    "get_trace_id",
    "new_trace_id",
    "redact",
    "set_trace_id",
    "trace_id_var",
]
