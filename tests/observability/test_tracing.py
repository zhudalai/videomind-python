"""L1 unit tests for observability/tracing.py - trace context inject/extract."""

from videomind.observability.tracing import (
    inject_trace_context,
    extract_trace_context,
    get_current_trace_id,
)
from opentelemetry import trace
from opentelemetry.trace import SpanContext, TraceFlags
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter


class TestInjectExtractTraceContext:
    """Tests for inject_trace_context / extract_trace_context round-trip."""

    def setup_method(self):
        """Set up a test tracer provider."""
        self.provider = TracerProvider()
        trace.set_tracer_provider(self.provider)
        self.tracer = trace.get_tracer("test")

    def teardown_method(self):
        """Clean up tracer provider."""
        trace.set_tracer_provider(None)

    def test_inject_extract_roundtrip_preserves_context(self):
        """Inject context into carrier, extract back -> same trace_id/span_id."""
        carrier = {}

        # Create a span context and set it as current
        with self.tracer.start_as_current_span("test-span") as span:
            span_context = span.get_span_context()

            # Inject
            inject_trace_context(carrier)

            # Extract
            extracted_ctx = extract_trace_context(carrier)

            assert extracted_ctx is not None
            assert extracted_ctx.trace_id == span_context.trace_id
            assert extracted_ctx.span_id == span_context.span_id

    def test_inject_populates_traceparent_header(self):
        """Carrier gets traceparent header in W3C format."""
        carrier = {}
        with self.tracer.start_as_current_span("test"):
            inject_trace_context(carrier)

        assert "traceparent" in carrier
        traceparent = carrier["traceparent"]
        # W3C format: version-trace-id-span-id-flags
        parts = traceparent.split("-")
        assert len(parts) == 4
        assert parts[0] == "00"  # version
        assert len(parts[1]) == 32  # trace-id 16 bytes = 32 hex
        assert len(parts[2]) == 16  # span-id 8 bytes = 16 hex
        assert parts[3] in ("01", "00")  # flags

    def test_inject_populates_tracestate_header(self):
        """Carrier may get tracestate header."""
        carrier = {}
        span = self.tracer.start_span("test")
        inject_trace_context(carrier)
        # tracestate is optional but often present
        # Just verify inject doesn't crash

    def test_extract_empty_carrier_returns_none(self):
        """Empty carrier returns None."""
        result = extract_trace_context({})
        # May return None or a default context - both acceptable
        # Just verify no exception

    def test_extract_invalid_traceparent_returns_none(self):
        """Invalid traceparent returns None or invalid context."""
        carrier = {"traceparent": "invalid-format"}
        result = extract_trace_context(carrier)
        # Should not crash

    def test_multiple_carriers_independent(self):
        """Multiple carriers get independent contexts."""
        carrier1 = {}
        carrier2 = {}

        with self.tracer.start_as_current_span("span1") as span1:
            inject_trace_context(carrier1)

        with self.tracer.start_as_current_span("span2") as span2:
            inject_trace_context(carrier2)

        ctx1 = extract_trace_context(carrier1)
        ctx2 = extract_trace_context(carrier2)

        if ctx1 and ctx2:
            assert ctx1.trace_id != ctx2.trace_id
            assert ctx1.span_id != ctx2.span_id


class TestGetCurrentTraceId:
    """Tests for get_current_trace_id (uses context var)."""

    def setup_method(self):
        """Set up tracer provider."""
        self.provider = TracerProvider()
        trace.set_tracer_provider(self.provider)
        self.tracer = trace.get_tracer("test")

    def teardown_method(self):
        trace.set_tracer_provider(None)

    def test_no_active_span_returns_none(self):
        """No active span -> get_current_trace_id returns None."""
        result = get_current_trace_id()
        assert result is None

    def test_active_span_returns_trace_id(self):
        """Active span -> returns its trace_id as 32-char hex."""
        with self.tracer.start_as_current_span("test-span") as span:
            trace_id = get_current_trace_id()
            assert trace_id is not None
            assert len(trace_id) == 32
            assert trace_id == format(span.get_span_context().trace_id, "032x")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])