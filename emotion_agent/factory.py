"""情绪识别 Agent 的组装入口。"""

from __future__ import annotations

from emotion_agent.graph import EmotionRecognitionAgent
from emotion_agent.providers import create_openai_compatible_structured_model
from emotion_agent.schemas import EmotionModelOutput
from emotion_agent.settings import LLMSettings


def create_emotion_agent_from_env(
    settings: LLMSettings | None = None,
) -> EmotionRecognitionAgent:
    """用环境变量中的 OpenAI 兼容配置创建情绪识别 Agent。"""
    resolved = settings or LLMSettings.from_env()
    # 保留原始输出和解析错误，供运行时 Agent 执行带反馈的定向重试。
    model = create_openai_compatible_structured_model(
        EmotionModelOutput,
        resolved,
        include_raw=True,
        include_logprobs=True,
    )
    return EmotionRecognitionAgent(
        model=model,
        max_schema_retries=resolved.schema_max_retries,
    )
