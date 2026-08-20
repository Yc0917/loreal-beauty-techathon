"""统一并行分析 Agent 的模型组装入口。"""

from __future__ import annotations

from typing import cast

from analysis_agent.graph import ConversationAnalysisAgent
from emotion_agent.providers import create_openai_compatible_structured_model
from emotion_agent.recognizer import EmotionRecognizer, StructuredEmotionModel
from emotion_agent.schemas import EmotionModelOutput
from emotion_agent.settings import LLMSettings
from intent_agent.recognizer import IntentRecognizer, StructuredIntentModel
from intent_agent.schemas import IntentModelOutput


def create_analysis_agent_from_env(
    settings: LLMSettings | None = None,
) -> ConversationAnalysisAgent:
    """使用同一套环境配置创建两个独立模型分支。"""
    resolved = settings or LLMSettings.from_env()
    emotion_model = create_openai_compatible_structured_model(
        EmotionModelOutput,
        resolved,
        include_raw=True,
        include_logprobs=True,
    )
    intent_model = create_openai_compatible_structured_model(
        IntentModelOutput,
        resolved,
        include_raw=True,
    )
    return ConversationAnalysisAgent(
        emotion_recognizer=EmotionRecognizer(
            cast(StructuredEmotionModel, emotion_model),
            max_schema_retries=resolved.schema_max_retries,
        ),
        intent_recognizer=IntentRecognizer(
            cast(StructuredIntentModel, intent_model),
            max_schema_retries=resolved.schema_max_retries,
        ),
    )
