"""并行情绪与用户意图分析的统一输出结构。"""

from __future__ import annotations

from pydantic import BaseModel

from emotion_agent.schemas import EmotionResult
from intent_agent.schemas import IntentResult


class ConversationAnalysisResult(BaseModel):
    """同一个 Graph 中两个并行分支的聚合结果。"""

    emotion: EmotionResult | None = None
    intent: IntentResult | None = None
    emotion_error: str | None = None
    intent_error: str | None = None
