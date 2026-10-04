"""OpenTelemetry setup.

A tracer provider is always installed so every discovery step / automation run has a
trace id (shown as "Visualizza in Husk Studio"). Spans are exported only once a local
Studio is detected. LangChain calls are instrumented with OpenInference, the same span
format Studio's `instrument_langgraph()` adapter produces.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider: TracerProvider | None = None
_exporting = False


def init_tracing(service_version: str) -> None:
    global _provider
    if _provider is not None:
        return
    _provider = TracerProvider(
        resource=Resource.create({"service.name": "husk", "service.version": service_version})
    )
    trace.set_tracer_provider(_provider)


def enable_otlp(endpoint: str) -> None:
    global _exporting
    if _exporting or _provider is None:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=3))
    )
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=_provider)
    except Exception:
        pass
    _exporting = True


def exporting() -> bool:
    """True once spans are exported to a local Studio (a trace id then exists there too)."""
    return _exporting


def tracer() -> trace.Tracer:
    return trace.get_tracer("husk")


@contextmanager
def span(name: str, **attrs: object) -> Iterator[str | None]:
    """Start a span and yield its hex trace id (None when tracing is not initialised)."""
    with tracer().start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        ctx = sp.get_span_context()
        yield format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
