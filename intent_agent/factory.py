"""用户意图 Agent 的模型组装入口。"""

from __future__ import annotations

from typing import cast

from emotion_agent.providers import create_openai_compatible_structured_model
from emotion_agent.settings import LLMSettings
from intent_agent.graph import IntentRecognitionAgent
from intent_agent.recognizer import StructuredIntentModel
from intent_agent.schemas import IntentModelOutput


def create_intent_agent_from_env(
    settings: LLMSettings | None = None,
) -> IntentRecognitionAgent:
    """使用项目现有 OpenAI 兼容配置创建用户意图 Agent。"""
    resolved = settings or LLMSettings.from_env()
    model = create_openai_compatible_structured_model(
        IntentModelOutput,
        resolved,
        include_raw=True,
    )
    return IntentRecognitionAgent(
        model=cast(StructuredIntentModel, model),
        max_schema_retries=resolved.schema_max_retries,
    )
