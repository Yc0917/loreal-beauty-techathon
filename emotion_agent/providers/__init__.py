"""情绪识别模块的模型供应商适配器。"""

from emotion_agent.providers.openai_compatible import (
    create_openai_compatible_model,
    create_openai_compatible_structured_model,
)

__all__ = [
    "create_openai_compatible_model",
    "create_openai_compatible_structured_model",
]
