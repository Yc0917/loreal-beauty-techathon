"""工单意图识别模块的结构化输入与输出。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 点击识别即表示客服准备建单，因此只保留五类真实工单。
IntentLabel = Literal[
    "reship_exchange",
    "offline_payment",
    "logistics_ticket",
    "adverse_reaction",
    "after_sales_return",
]


class IntentEvidence(BaseModel):
    """一条可按角色逐字回溯的工单判断证据。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["买家", "客服"] = Field(description="证据原话的发送角色")
    text: str = Field(
        min_length=1,
        max_length=200,
        description="对应角色原话中的连续短语，不得改写",
    )


class IntentModelOutput(BaseModel):
    """模型需要直接生成的工单意图字段。"""

    model_config = ConfigDict(extra="forbid")

    intent: IntentLabel = Field(description="客服应创建的工单类型")
    need_ticket: Literal[True] = Field(
        default=True,
        description="点击触发场景中始终为 true，仅为兼容现有接口保留",
    )
    confidence: float = Field(ge=0, le=1, description="模型对意图判断的置信度")
    evidence: list[IntentEvidence] = Field(
        min_length=1,
        max_length=4,
        description="来自买家或客服原话的带角色证据，至少包含一条买家证据",
    )
    summary: str = Field(
        min_length=1,
        max_length=120,
        description="简短且可审核的意图判断说明",
    )


class IntentResult(IntentModelOutput):
    """工单意图 Agent 的最终输出。"""

    prompt_version: str = Field(description="本次使用的 Prompt 版本")
    attempts: int = Field(default=1, ge=1, description="本次识别实际调用次数")
