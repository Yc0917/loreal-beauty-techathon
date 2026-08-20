"""只包含一个情绪识别节点的轻量 LangGraph 工作流。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from emotion_agent.recognizer import EmotionRecognizer, StructuredEmotionModel
from emotion_agent.schemas import ConversationMessage, EmotionResult


class EmotionState(TypedDict, total=False):
    """情绪识别图使用的最小状态。"""

    scene: str
    messages: list[ConversationMessage]
    result: EmotionResult


def _build_emotion_node(recognizer: EmotionRecognizer):
    """构建唯一的情绪识别节点。"""

    def recognize_emotion(state: EmotionState) -> dict[str, EmotionResult]:
        result = recognizer.recognize(
            messages=state.get("messages", []),
            scene=state.get("scene", "未知"),
        )
        return {"result": result}

    return recognize_emotion


def create_emotion_graph(recognizer: EmotionRecognizer):
    """构建 START → emotion_recognition → END 工作流。"""
    builder = StateGraph(EmotionState)
    builder.add_node("emotion_recognition", _build_emotion_node(recognizer))
    builder.add_edge(START, "emotion_recognition")
    builder.add_edge("emotion_recognition", END)
    return builder.compile()


class EmotionRecognitionAgent:
    """独立入口；模型必须由外部注入，当前不绑定任何真实接口。"""

    def __init__(
        self,
        model: StructuredEmotionModel,
        max_schema_retries: int = 0,
    ):
        self.recognizer = EmotionRecognizer(
            model,
            max_schema_retries=max_schema_retries,
        )
        self.graph = create_emotion_graph(self.recognizer)

    def analyze(
        self,
        messages: Sequence[ConversationMessage | dict[str, str]],
        scene: str = "未知",
    ) -> EmotionResult:
        """执行一次单节点情绪识别工作流。"""
        state = self.graph.invoke(
            {
                "scene": scene,
                "messages": list(messages),
            }
        )
        return EmotionResult.model_validate(state["result"])
