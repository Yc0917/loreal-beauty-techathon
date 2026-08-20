"""情绪识别与用户意图识别并行执行的统一 LangGraph。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from analysis_agent.schemas import ConversationAnalysisResult
from emotion_agent.recognizer import EmotionRecognizer
from emotion_agent.schemas import ConversationMessage, EmotionResult
from intent_agent.recognizer import IntentRecognizer
from intent_agent.schemas import IntentResult


logger = logging.getLogger(__name__)


class ConversationAnalysisState(TypedDict, total=False):
    """并行图状态；两个节点分别写入不同字段，避免并发冲突。"""

    scene: str
    messages: list[ConversationMessage]
    emotion: EmotionResult
    intent: IntentResult
    emotion_error: str
    intent_error: str


class ConversationAnalysisRetryError(ValueError):
    """两个分析分支均失败时抛出的统一异常。"""


def create_analysis_graph(
    emotion_recognizer: EmotionRecognizer,
    intent_recognizer: IntentRecognizer,
):
    """构建 START 同时分发到两个节点的并行工作流。"""

    def recognize_emotion(
        state: ConversationAnalysisState,
    ) -> dict[str, EmotionResult | str]:
        try:
            result = emotion_recognizer.recognize(
                messages=state.get("messages", []),
                scene=state.get("scene", "未知"),
            )
            return {"emotion": result}
        except Exception:
            # 分支内部吸收异常，使用户意图分支仍能独立完成。
            logger.exception("统一分析 Graph 的情绪识别分支失败")
            return {"emotion_error": "识别失败"}

    def recognize_intent(
        state: ConversationAnalysisState,
    ) -> dict[str, IntentResult | str]:
        try:
            result = intent_recognizer.recognize(
                messages=state.get("messages", []),
            )
            return {"intent": result}
        except Exception:
            # 分支内部吸收异常，使情绪识别分支仍能独立完成。
            logger.exception("统一分析 Graph 的用户意图识别分支失败")
            return {"intent_error": "识别失败"}

    builder = StateGraph(ConversationAnalysisState)
    builder.add_node("emotion_recognition", recognize_emotion)
    builder.add_node("intent_recognition", recognize_intent)
    builder.add_edge(START, "emotion_recognition")
    builder.add_edge(START, "intent_recognition")
    builder.add_edge("emotion_recognition", END)
    builder.add_edge("intent_recognition", END)
    return builder.compile()


class ConversationAnalysisAgent:
    """对外暴露单次调用、内部并行执行两个节点的统一 Agent。"""

    def __init__(
        self,
        emotion_recognizer: EmotionRecognizer,
        intent_recognizer: IntentRecognizer,
    ) -> None:
        self.graph = create_analysis_graph(emotion_recognizer, intent_recognizer)

    def analyze(
        self,
        messages: Sequence[ConversationMessage | dict[str, str]],
        scene: str = "未知",
    ) -> ConversationAnalysisResult:
        """执行统一 Graph，并保留每个分支各自的成功或失败状态。"""
        state = self.graph.invoke(
            {
                "scene": scene,
                "messages": list(messages),
            }
        )
        result = ConversationAnalysisResult(
            emotion=state.get("emotion"),
            intent=state.get("intent"),
            emotion_error=state.get("emotion_error"),
            intent_error=state.get("intent_error"),
        )
        if result.emotion is None and result.intent is None:
            raise ConversationAnalysisRetryError("情绪与用户意图识别均失败")
        return result
