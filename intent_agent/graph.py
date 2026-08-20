"""只包含一个用户意图识别节点的轻量 LangGraph 工作流。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from emotion_agent.schemas import ConversationMessage
from intent_agent.recognizer import IntentRecognizer, StructuredIntentModel
from intent_agent.schemas import IntentResult


class IntentState(TypedDict, total=False):
    """用户意图图使用的最小状态。"""

    messages: list[ConversationMessage]
    result: IntentResult


def _build_intent_node(recognizer: IntentRecognizer):
    """构建唯一的用户意图识别节点。"""

    def recognize_intent(state: IntentState) -> dict[str, IntentResult]:
        return {"result": recognizer.recognize(state.get("messages", []))}

    return recognize_intent


def create_intent_graph(recognizer: IntentRecognizer):
    """构建 START → intent_recognition → END 工作流。"""
    builder = StateGraph(IntentState)
    builder.add_node("intent_recognition", _build_intent_node(recognizer))
    builder.add_edge(START, "intent_recognition")
    builder.add_edge("intent_recognition", END)
    return builder.compile()


class IntentRecognitionAgent:
    """用户意图 Agent 的独立执行入口。"""

    def __init__(self, model: StructuredIntentModel, max_schema_retries: int = 0):
        self.recognizer = IntentRecognizer(model, max_schema_retries=max_schema_retries)
        self.graph = create_intent_graph(self.recognizer)

    def analyze(
        self,
        messages: Sequence[ConversationMessage | dict[str, str]],
    ) -> IntentResult:
        """执行一次用户意图识别工作流。"""
        state = self.graph.invoke({"messages": list(messages)})
        return IntentResult.model_validate(state["result"])
