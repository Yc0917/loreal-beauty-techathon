"""Knowledge Agent的对外调用入口。"""

from __future__ import annotations

from collections.abc import Sequence

from emotion_agent.schemas import ConversationMessage
from knowledge_agent.observability import knowledge_trace
from knowledge_agent.prompts import PROMPT_VERSION
from knowledge_agent.schemas import KnowledgeReplyResult
from knowledge_agent.settings import KnowledgeSettings


class KnowledgeAgent:
    """封装LangGraph运行、Langfuse Trace与结果校验。"""

    def __init__(self, *, graph, settings: KnowledgeSettings) -> None:
        self.graph = graph
        self.settings = settings

    async def generate_reply(
        self,
        *,
        session_id: str,
        messages: Sequence[ConversationMessage | dict[str, str]],
        scene: str = "未知",
        product_ids: Sequence[str] = (),
        question: str | None = None,
    ) -> KnowledgeReplyResult:
        """生成可审核草稿，本方法不会向千牛发送消息。"""
        conversation = [
            item
            if isinstance(item, ConversationMessage)
            else ConversationMessage.model_validate(item)
            for item in messages
        ]
        if not conversation:
            raise ValueError("至少需要一条会话消息")
        resolved_question = (question or "").strip()
        if not resolved_question:
            resolved_question = next(
                (
                    item.text
                    for item in reversed(conversation)
                    if item.role == "买家"
                ),
                "",
            )
        if not resolved_question:
            raise ValueError("对话中至少需要一条买家消息")

        initial_state = {
            "session_id": session_id,
            "scene": scene.strip() or "未知",
            "conversation": conversation,
            "question": resolved_question,
            "requested_product_ids": list(dict.fromkeys(product_ids)),
        }
        with knowledge_trace(session_id, self.settings.llm.model) as trace:
            config = {
                "run_name": "knowledge-agent-reply",
                "max_concurrency": 8,
            }
            if trace.handler is not None:
                config["callbacks"] = [trace.handler]
            state = await self.graph.ainvoke(initial_state, config=config)
            trace.capture_ids()

        draft = state["draft"]
        return KnowledgeReplyResult(
            reply=draft.reply,
            reply_policy=state["reply_policy"],
            emotion=state.get("emotion"),
            emotion_error=state.get("emotion_error"),
            safety=state["safety"],
            product_context=state["product_context"],
            evidence=state.get("evidence", []),
            tool_calls=state.get("tool_calls", []),
            unsupported_points=draft.unsupported_points,
            guard_violations=state.get("guard_violations", []),
            prompt_version=PROMPT_VERSION,
            tracing_enabled=trace.enabled,
            trace_id=trace.trace_id,
            trace_url=trace.trace_url,
        )
