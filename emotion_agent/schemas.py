"""用户情绪识别模块的输入与输出结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# 运行时与 V1.1 标注口径保持一致，只保留四类核心情绪。
EmotionLabel = Literal[
    "neutral",
    "anxious",
    "dissatisfied",
    "angry",
]
EmotionTrend = Literal["improving", "stable", "worsening", "unknown"]
ConfidenceSource = Literal["token_logprob"]
MessageRole = Literal["买家", "客服", "系统推送"]


class ConversationMessage(BaseModel):
    """一条按时间正序传入的会话消息。"""

    model_config = ConfigDict(extra="forbid")

    role: MessageRole = Field(description="消息发送方")
    text: str = Field(min_length=1, max_length=500, description="消息正文")

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """清理多余空白，避免空消息或异常换行干扰 Prompt。"""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


class EmotionModelOutput(BaseModel):
    """模型需要直接生成的结构化字段。"""

    model_config = ConfigDict(extra="forbid")

    emotion: EmotionLabel = Field(description="最新时点买家的主导情绪")
    trend: EmotionTrend = Field(description="对比买家前后表达得到的情绪趋势")
    evidence: list[str] = Field(
        min_length=1,
        max_length=3,
        description="来自买家原话的连续短语，不得改写",
    )
    summary: str = Field(
        min_length=1,
        max_length=120,
        description="简短、可审核的情绪判断说明",
    )


class EmotionTokenLogprob(BaseModel):
    """情绪标签中一个输出 Token 的概率信息。"""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(description="模型输出的原始 Token")
    logprob: float = Field(description="Token 的自然对数概率")
    probability: float = Field(ge=0, le=1, description="exp(logprob) 得到的 Token 概率")


class EmotionResult(EmotionModelOutput):
    """情绪识别模块的最终输出。"""

    confidence: float = Field(
        ge=0,
        le=1,
        description="情绪标签所有 Token 的联合概率",
    )
    confidence_source: ConfidenceSource = Field(
        default="token_logprob",
        description="置信度固定来自输出 Token 的 logprob",
    )
    emotion_token_logprobs: list[EmotionTokenLogprob] = Field(
        min_length=1,
        description="用于计算置信度的情绪标签 Token",
    )
    prompt_version: str = Field(description="本次识别所使用的 Prompt 版本")
    attempts: int = Field(default=1, ge=1, description="本次识别实际调用次数")
