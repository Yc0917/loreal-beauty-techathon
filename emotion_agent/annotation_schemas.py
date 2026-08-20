"""LLM 轮次标注的数据结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emotion_agent.schemas import EmotionTrend, MessageRole


AnnotationStatus = Literal["pending", "completed", "failed"]
AnnotationEmotionLabel = Literal[
    "neutral",
    "anxious",
    "dissatisfied",
    "angry",
]


class AnnotationMessage(BaseModel):
    """保留原消息序号的标注上下文消息。"""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    role: MessageRole
    text: str = Field(min_length=1, max_length=500)


class AnnotationCase(BaseModel):
    """一条以当前买家消息为终点的递增上下文样本。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^S\d+_B\d{2}$")
    conversation_id: str = Field(pattern=r"^S\d+$")
    buyer_turn: int = Field(ge=1)
    target_message_seq: int = Field(ge=1)
    target_text: str = Field(min_length=1, max_length=500)
    messages: list[AnnotationMessage] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target_message(self) -> "AnnotationCase":
        """确保上下文以当前买家消息结束，防止未来信息泄漏。"""
        target = self.messages[-1]
        if target.role != "买家":
            raise ValueError("标注上下文必须以买家消息结束")
        if target.seq != self.target_message_seq or target.text != self.target_text:
            raise ValueError("target 字段必须与 messages 最后一条消息一致")
        return self


class LLMAnnotation(BaseModel):
    """标注模型只允许输出的四个字段。"""

    model_config = ConfigDict(extra="forbid")

    # V1.1 标注任务独立使用四分类，不影响主流程现有 Schema。
    emotion: AnnotationEmotionLabel
    confidence: float = Field(ge=0, le=1)
    trend: EmotionTrend
    reason: str = Field(min_length=1, max_length=300)


class AnnotationRecord(AnnotationCase):
    """包含运行信息和 LLM 结果的最终标注记录。"""

    llm_annotation: LLMAnnotation | None = None
    annotation_model: str
    annotation_prompt_version: str
    annotation_status: AnnotationStatus
    annotation_attempts: int = Field(default=1, ge=1)
    schema_repaired: bool = False
    repair_errors: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "AnnotationRecord":
        """保证成功记录有标注，失败记录有错误信息。"""
        if self.annotation_status == "completed" and self.llm_annotation is None:
            raise ValueError("completed 记录必须包含 llm_annotation")
        if self.annotation_status == "failed" and not self.error:
            raise ValueError("failed 记录必须包含 error")
        return self
