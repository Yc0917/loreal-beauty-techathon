"""Langfuse v4 Trace、会话聚合和客服数据脱敏。"""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

from knowledge_agent.prompts import PROMPT_VERSION


PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
LONG_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{16,24}(?!\d)")


def _redact_text(value: str) -> str:
    """在Trace离开本机前脱敏电话、邮箱和长订单号。"""
    value = PHONE_PATTERN.sub("[PHONE_REDACTED]", value)
    value = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)
    return LONG_NUMBER_PATTERN.sub("[LONG_ID_REDACTED]", value)


def _mask_otel_spans(
    *, params: MaskOtelSpansParams
) -> MaskOtelSpansResult | None:
    """覆盖LangChain回调产生的第三方OpenTelemetry Span。"""
    patches = {}
    for identifier, span in params.spans.items():
        replacements: dict[str, str] = {}
        for key, value in span.attributes.items():
            if not isinstance(value, str):
                continue
            redacted = _redact_text(value)
            if redacted != value:
                replacements[key] = redacted
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches) if patches else None


def _anonymous_session_id(session_id: str) -> str:
    """不把业务会话主键直接发送给观测平台。"""
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return f"session-{digest}"


@dataclass
class TraceHandle:
    """一次Agent运行的Langfuse句柄。"""

    enabled: bool
    handler: CallbackHandler | None = None
    client: Langfuse | None = None
    trace_id: str | None = None
    trace_url: str | None = None

    def capture_ids(self) -> None:
        if not self.enabled or self.client is None:
            return
        self.trace_id = self.client.get_current_trace_id() or self.trace_id
        if self.trace_id:
            self.trace_url = self.client.get_trace_url(trace_id=self.trace_id)


@contextmanager
def knowledge_trace(session_id: str, model: str) -> Iterator[TraceHandle]:
    """在密钥存在时为整个LangGraph运行创建Trace。"""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key:
        yield TraceHandle(enabled=False)
        return

    sample_rate = float(os.getenv("LANGFUSE_SAMPLE_RATE", "1.0"))
    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").strip(),
        environment=os.getenv(
            "LANGFUSE_TRACING_ENVIRONMENT", "development"
        ).strip(),
        sample_rate=sample_rate,
        mask_otel_spans=_mask_otel_spans,
    )
    # 预先生成Trace ID，确保Graph执行完成后仍能返回稳定URL。
    trace_id = client.create_trace_id()
    handler = CallbackHandler(
        public_key=public_key,
        trace_context={"trace_id": trace_id},
    )
    handle = TraceHandle(
        enabled=True,
        handler=handler,
        client=client,
        trace_id=trace_id,
        trace_url=client.get_trace_url(trace_id=trace_id),
    )
    with propagate_attributes(
        trace_name="knowledge-agent-reply",
        session_id=_anonymous_session_id(session_id),
        tags=["langgraph", "tool-use-loop", "loreal-knowledge"],
        version=PROMPT_VERSION,
        metadata={"model": model, "promptVersion": PROMPT_VERSION},
    ):
        try:
            yield handle
        finally:
            handle.capture_ids()
