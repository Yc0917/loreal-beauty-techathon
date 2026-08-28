"""检索工具的通用序列化与证据ID方法。"""

from __future__ import annotations

import hashlib

from knowledge_agent.schemas import ToolEnvelope


def evidence_id(source_type: str, source_id: str, content: str) -> str:
    """基于证据内容生成可复现的短ID。"""
    digest = hashlib.sha256(
        f"{source_type}|{source_id}|{content}".encode("utf-8")
    ).hexdigest()[:12]
    return f"EV-{digest.upper()}"


def dump_envelope(envelope: ToolEnvelope) -> str:
    """使ToolMessage保留可程序解析的JSON。"""
    return envelope.model_dump_json()
