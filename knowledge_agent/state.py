"""LangGraph Tool-Use Loop的共享State。"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from emotion_agent.schemas import ConversationMessage, EmotionResult
from knowledge_agent.schemas import (
    DraftModelOutput,
    Evidence,
    ProductContext,
    ReplyPolicy,
    SafetyDecision,
    SemanticGuardOutput,
    ToolCallRecord,
)


class KnowledgeAgentState(TypedDict, total=False):
    """并行节点写入不同字段，证据列表使用Reducer聚合。"""

    session_id: str
    scene: str
    conversation: list[ConversationMessage]
    question: str
    requested_product_ids: list[str]

    emotion: EmotionResult
    emotion_error: str
    product_context: ProductContext
    safety: SafetyDecision

    agent_messages: Annotated[list[BaseMessage], add_messages]
    evidence: Annotated[list[Evidence], operator.add]
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    tool_errors: Annotated[list[str], operator.add]
    tool_iterations: int

    draft: DraftModelOutput
    semantic_guard: SemanticGuardOutput
    retrieval_violations: list[str]
    guard_violations: list[str]
    generation_attempts: int
    reply_policy: ReplyPolicy

    tracing_enabled: bool
    trace_id: str
    trace_url: str
