"""Knowledge Agent 的输入、证据和输出结构。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from emotion_agent.schemas import EmotionResult


RiskLevel = Literal["low", "medium", "high"]
ReplyPolicy = Literal["auto_send", "draft", "escalate"]
EvidenceSource = Literal["neo4j", "graphrag", "dynamic_checker"]


class ProductContext(BaseModel):
    """从会话和用户问题中解析出的商品上下文。"""

    model_config = ConfigDict(extra="forbid")

    product_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    ambiguous: bool = False
    clarification: str | None = None
    resolution_method: Literal["request", "alias", "llm_reference", "unresolved"] = (
        "unresolved"
    )
    reference_text: str | None = None
    resolution_reason: str | None = None
    resolution_confidence: float | None = Field(default=None, ge=0, le=1)
    resolver_error: str | None = None


class ProductReferenceModelOutput(BaseModel):
    """LLM在候选商品范围内完成上下文指代消解。"""

    model_config = ConfigDict(extra="forbid")

    resolved_product_ids: list[str] = Field(default_factory=list, max_length=8)
    reference_text: str | None = Field(default=None, max_length=80)
    ambiguous: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class SafetyModelOutput(BaseModel):
    """LLM对化妆品风险的语义识别结果。"""

    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel
    categories: list[str] = Field(default_factory=list, max_length=8)
    blocked_claims: list[str] = Field(default_factory=list, max_length=8)
    must_escalate: bool
    reason: str = Field(min_length=1, max_length=300)


class SafetyDecision(SafetyModelOutput):
    """确定性规则与LLM判断合并后的安全决策。"""

    matched_rules: list[str] = Field(default_factory=list)
    classifier_error: str | None = None


class Evidence(BaseModel):
    """不同检索工具返回的统一证据。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_type: EvidenceSource
    source_id: str
    product_ids: list[str] = Field(default_factory=list)
    fact_type: str
    content: str
    risk_level: RiskLevel = "low"
    is_mock: bool = True
    is_realtime: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolEnvelope(BaseModel):
    """工具通过ToolMessage返回给Agent的JSON信封。"""

    model_config = ConfigDict(extra="forbid")

    tool: str
    ok: bool
    evidence: list[Evidence] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftModelOutput(BaseModel):
    """回复模型产生的带证据引用草稿。"""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=800)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    unsupported_points: list[str] = Field(default_factory=list)


class SemanticGuardOutput(BaseModel):
    """LLM证据含义检查的结构化输出。"""

    model_config = ConfigDict(extra="forbid")

    supported: bool
    unsupported_claims: list[str] = Field(default_factory=list, max_length=8)
    medical_overclaim: bool
    missing_qualifications: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=300)


class ToolCallRecord(BaseModel):
    """对外返回的工具调用摘要。"""

    name: str
    call_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = None
    error: str | None = None


class KnowledgeReplyResult(BaseModel):
    """Knowledge Agent返回给FastAPI的完整可审核结果。"""

    reply: str
    reply_policy: ReplyPolicy
    emotion: EmotionResult | None = None
    emotion_error: str | None = None
    safety: SafetyDecision
    product_context: ProductContext
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    unsupported_points: list[str] = Field(default_factory=list)
    guard_violations: list[str] = Field(default_factory=list)
    prompt_version: str
    tracing_enabled: bool = False
    trace_id: str | None = None
    trace_url: str | None = None
