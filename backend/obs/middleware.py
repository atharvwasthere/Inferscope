"""ASGI middleware that binds a trace id to each request's context."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from obs.trace import TRACE_HEADER, new_trace_id, trace_id_var


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get(TRACE_HEADER) or new_trace_id()
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers[TRACE_HEADER] = trace_id
        return response
