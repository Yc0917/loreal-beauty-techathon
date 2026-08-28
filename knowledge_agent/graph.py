"""带安全门控、并行ToolNode和Evidence Guard的LangGraph。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from emotion_agent.recognizer import EmotionRecognizer
from knowledge_agent.prompts import (
    EVIDENCE_GUARD_SYSTEM_PROMPT,
    PRODUCT_REFERENCE_SYSTEM_PROMPT,
    RESPONSE_SYSTEM_PROMPT,
    SAFETY_SYSTEM_PROMPT,
    TOOL_AGENT_SYSTEM_PROMPT,
    build_agent_context,
    build_product_reference_context,
    build_response_context,
)
from knowledge_agent.schemas import (
    DraftModelOutput,
    Evidence,
    ProductContext,
    ProductReferenceModelOutput,
    SafetyDecision,
    SafetyModelOutput,
    SemanticGuardOutput,
    ToolCallRecord,
    ToolEnvelope,
)
from knowledge_agent.settings import KnowledgeSettings
from knowledge_agent.state import KnowledgeAgentState


PRODUCT_ALIASES: dict[str, list[str]] = {
    "超水光精华": ["LOREAL_SUPER_GLOW"],
    "超水光": ["LOREAL_SUPER_GLOW"],
    "黑金精华": ["LOREAL_BLACK_GOLD"],
    "黑金": ["LOREAL_BLACK_GOLD"],
    "第四代黑精华": ["LOREAL_GEN4_BLACK"],
    "四代黑精华": ["LOREAL_GEN4_BLACK"],
    "四代": ["LOREAL_GEN4_BLACK"],
    "注白瓶": ["LOREAL_WHITE_BOTTLE"],
    # “黑精华”可能指四代或黑金，无商品卡片时不擅自选择。
    "黑精华": ["LOREAL_BLACK_GOLD", "LOREAL_GEN4_BLACK"],
}
PRODUCT_NAMES = {
    "LOREAL_SUPER_GLOW": "欧莱雅超水光精华",
    "LOREAL_BLACK_GOLD": "欧莱雅黑金精华",
    "LOREAL_GEN4_BLACK": "欧莱雅第四代黑精华",
    "LOREAL_WHITE_BOTTLE": "欧莱雅注白瓶",
}
PRODUCT_REFERENCE_CONFIDENCE_THRESHOLD = 0.75
VAGUE_PRODUCT_REFERENCES = ("它", "这个", "这款", "那个", "那款", "该款", "这瓶", "那瓶")
POSITIONAL_PRODUCT_REFERENCES = (
    "前者",
    "后者",
    "第一款",
    "第二款",
    "第一个",
    "第二个",
    "前一款",
    "后一款",
)

RISK_RANK = {"low": 0, "medium": 1, "high": 2}
HIGH_RISK_RULES: dict[str, tuple[str, ...]] = {
    "pregnancy": ("孕期", "怀孕", "孕妇", "哺乳", "喂奶"),
    "adverse_reaction": ("脸红", "红肿", "刺痛", "灼热", "发痒", "过敏", "烂脸"),
    "medical_claim": ("治疗", "治好", "黄褐斑", "皮肤病", "湿疹", "皮炎"),
    "ingredient_stacking": ("刷酸", "叠加", "一起用"),
    "special_population": ("儿童", "宝宝", "误食", "入眼"),
    "absolute_guarantee": ("肯定不过敏", "绝对不过敏", "一定有效", "保证有效"),
}
MEDIUM_RISK_WORDS = ("敏感肌", "敏感皮", "每天用", "使用频率", "搭配")
DYNAMIC_WORDS = ("价格", "多少钱", "库存", "有货", "缺货", "优惠", "活动", "赠品", "物流", "订单", "退款")
ABSOLUTE_CLAIMS = re.compile(r"一定|保证|绝对不过敏|百分之百|肯定有效")
MEDICAL_CLAIMS = re.compile(r"治疗|治愈|药效|治好皮肤病")


def _clean_customer_reply(reply: str) -> str:
    """清理仅供内部审核的Evidence和GraphRAG引用标记。"""
    cleaned = re.sub(r"\s*\[[^\]]*EV-[A-F0-9]{12}[^\]]*\]", "", reply)
    cleaned = re.sub(r"\s*\[Data:[^\]]+\]", "", cleaned)
    return cleaned.strip()


def _latest_buyer_question(state: KnowledgeAgentState) -> str:
    if state.get("question"):
        return state["question"]
    for message in reversed(state.get("conversation", [])):
        if message.role == "买家":
            return message.text
    return ""


def _deterministic_safety(question: str) -> tuple[str, list[str]]:
    matched = [
        name
        for name, words in HIGH_RISK_RULES.items()
        if any(word in question for word in words)
    ]
    if matched:
        return "high", matched
    if any(word in question for word in MEDIUM_RISK_WORDS):
        return "medium", ["sensitive_or_usage"]
    return "low", []


def _match_product_aliases(text: str) -> tuple[list[str], list[str]]:
    """按长别名优先提取商品，避免短别名覆盖完整商品名。"""
    matches: list[tuple[int, str, list[str]]] = []
    occupied: list[tuple[int, int]] = []
    for alias in sorted(PRODUCT_ALIASES, key=len, reverse=True):
        for found in re.finditer(re.escape(alias), text):
            if any(found.start() < end and found.end() > start for start, end in occupied):
                continue
            occupied.append((found.start(), found.end()))
            matches.append((found.start(), alias, PRODUCT_ALIASES[alias]))
    # 长别名只用于消除重叠，最终候选顺序必须保持原文提及顺序。
    matches.sort(key=lambda item: item[0])
    aliases = list(dict.fromkeys(alias for _, alias, _ in matches))
    product_ids = list(dict.fromkeys(pid for _, _, ids in matches for pid in ids))
    return aliases, product_ids


def _clarification_for_candidates(candidate_ids: list[str]) -> str:
    """根据候选数量生成确定性追问，避免模型直接面向买家作答。"""
    names = [PRODUCT_NAMES[pid] for pid in candidate_ids if pid in PRODUCT_NAMES]
    if len(names) == 1:
        return f"请确认您说的是{names[0]}吗？"
    if names:
        return "请确认您指的是" + "、".join(names) + "中的哪一款。"
    return "请提供具体商品名称或发送商品卡片。"


def _historical_product_candidates(
    state: KnowledgeAgentState,
) -> tuple[list[str], list[dict[str, str]]]:
    """只从最新买家问题之前的对话提取候选商品。"""
    conversation = state.get("conversation", [])
    question = _latest_buyer_question(state)
    latest_buyer_index = next(
        (
            index
            for index in range(len(conversation) - 1, -1, -1)
            if conversation[index].role == "买家"
            and conversation[index].text == question
        ),
        len(conversation),
    )
    historical_text = "\n".join(
        message.text for message in conversation[:latest_buyer_index]
    )
    aliases, candidate_ids = _match_product_aliases(historical_text)
    candidates = [
        {"product_id": pid, "name": PRODUCT_NAMES.get(pid, pid)}
        for pid in candidate_ids
    ]
    return aliases, candidates


def _create_product_resolver_node(product_reference_model):
    async def resolve_products(state: KnowledgeAgentState) -> dict[str, ProductContext]:
        """规则识别明确商品，必要时调用LLM消解上下文指代。"""
        requested = list(dict.fromkeys(state.get("requested_product_ids", [])))
        if requested:
            return {
                "product_context": ProductContext(
                    product_ids=requested,
                    aliases=[
                        PRODUCT_NAMES[pid] for pid in requested if pid in PRODUCT_NAMES
                    ],
                    resolution_method="request",
                    resolution_reason="请求已携带商品ID，优先使用商品上下文。",
                    resolution_confidence=1.0,
                )
            }

        question = _latest_buyer_question(state)
        current_aliases, current_ids = _match_product_aliases(question)
        current_ambiguous = any(
            len(PRODUCT_ALIASES[alias]) > 1 for alias in current_aliases
        )
        if current_ids and not current_ambiguous:
            return {
                "product_context": ProductContext(
                    product_ids=current_ids,
                    aliases=current_aliases,
                    resolution_method="alias",
                    resolution_reason="最新买家问题包含明确商品别名。",
                    resolution_confidence=1.0,
                )
            }
        if current_ambiguous:
            return {
                "product_context": ProductContext(
                    aliases=current_aliases,
                    ambiguous=True,
                    clarification=_clarification_for_candidates(current_ids),
                    resolution_method="unresolved",
                    reference_text=current_aliases[0] if current_aliases else None,
                    resolution_reason="最新买家问题中的商品别名对应多个商品。",
                )
            }

        historical_aliases, candidates = _historical_product_candidates(state)
        candidate_ids = [item["product_id"] for item in candidates]
        if not candidates:
            return {
                "product_context": ProductContext(
                    ambiguous=True,
                    clarification=_clarification_for_candidates([]),
                    resolution_method="unresolved",
                    resolution_reason="当前问题没有明确商品，历史对话也没有候选商品。",
                )
            }

        # 多个候选加模糊单数指代时，模型容易高置信度猜测，必须先追问。
        has_vague_reference = any(word in question for word in VAGUE_PRODUCT_REFERENCES)
        has_positional_reference = any(
            word in question for word in POSITIONAL_PRODUCT_REFERENCES
        )
        if len(candidate_ids) > 1 and has_vague_reference and not has_positional_reference:
            return {
                "product_context": ProductContext(
                    aliases=historical_aliases,
                    ambiguous=True,
                    clarification=_clarification_for_candidates(candidate_ids),
                    resolution_method="unresolved",
                    reference_text=next(
                        word for word in VAGUE_PRODUCT_REFERENCES if word in question
                    ),
                    resolution_reason="历史对话存在多个商品，当前单数指代缺少明确位置线索。",
                )
            }

        try:
            result: ProductReferenceModelOutput = await product_reference_model.ainvoke(
                [
                    SystemMessage(content=PRODUCT_REFERENCE_SYSTEM_PROMPT),
                    HumanMessage(
                        content=build_product_reference_context(
                            conversation=state.get("conversation", []),
                            question=question,
                            candidate_products=candidates,
                        )
                    ),
                ]
            )
            resolved_ids = list(dict.fromkeys(result.resolved_product_ids))
            invalid_ids = set(resolved_ids) - set(candidate_ids)
            unresolved = (
                bool(invalid_ids)
                or result.ambiguous
                or not resolved_ids
                or result.confidence < PRODUCT_REFERENCE_CONFIDENCE_THRESHOLD
            )
            if unresolved:
                return {
                    "product_context": ProductContext(
                        aliases=historical_aliases,
                        ambiguous=True,
                        clarification=_clarification_for_candidates(candidate_ids),
                        resolution_method="unresolved",
                        reference_text=result.reference_text,
                        resolution_reason=(
                            "模型返回了候选范围外的商品ID。"
                            if invalid_ids
                            else result.reason[:300]
                        ),
                        resolution_confidence=result.confidence,
                    )
                }
            return {
                "product_context": ProductContext(
                    product_ids=resolved_ids,
                    aliases=[PRODUCT_NAMES.get(pid, pid) for pid in resolved_ids],
                    resolution_method="llm_reference",
                    reference_text=result.reference_text,
                    # 原始模型说明可能过长，State仅保留可审核摘要。
                    resolution_reason=result.reason[:300],
                    resolution_confidence=result.confidence,
                )
            }
        except Exception as error:
            return {
                "product_context": ProductContext(
                    aliases=historical_aliases,
                    ambiguous=True,
                    clarification=_clarification_for_candidates(candidate_ids),
                    resolution_method="unresolved",
                    resolution_reason="商品指代模型失败，未自动选择商品。",
                    resolver_error=f"{type(error).__name__}: {error}",
                )
            }

    return resolve_products


def _create_emotion_node(recognizer: EmotionRecognizer):
    async def recognize_emotion(state: KnowledgeAgentState) -> dict:
        try:
            result = await asyncio.to_thread(
                recognizer.recognize,
                state.get("conversation", []),
                state.get("scene", "未知"),
            )
            return {"emotion": result}
        except Exception as error:
            return {"emotion_error": f"{type(error).__name__}: {error}"}

    return recognize_emotion


def _create_safety_node(
    safety_model,
) -> Callable[[KnowledgeAgentState], object]:
    async def classify_safety(state: KnowledgeAgentState) -> dict:
        question = _latest_buyer_question(state)
        rule_level, matched_rules = _deterministic_safety(question)
        try:
            model_result: SafetyModelOutput = await safety_model.ainvoke(
                [
                    SystemMessage(content=SAFETY_SYSTEM_PROMPT),
                    HumanMessage(content=question),
                ]
            )
            final_level = max(
                (rule_level, model_result.risk_level),
                key=lambda value: RISK_RANK[value],
            )
            return {
                "safety": SafetyDecision(
                    **model_result.model_dump(exclude={"risk_level", "must_escalate"}),
                    risk_level=final_level,
                    must_escalate=(
                        rule_level == "high"
                        or model_result.must_escalate
                        or final_level == "high"
                    ),
                    matched_rules=matched_rules,
                )
            }
        except Exception as error:
            return {
                "safety": SafetyDecision(
                    risk_level=rule_level,
                    categories=matched_rules,
                    blocked_claims=[],
                    must_escalate=rule_level == "high",
                    reason="安全分类模型失败，保留确定性规则结果。",
                    matched_rules=matched_rules,
                    classifier_error=f"{type(error).__name__}: {error}",
                )
            }

    return classify_safety


def _prepare_agent(state: KnowledgeAgentState) -> dict:
    product_context = state.get("product_context", ProductContext())
    safety = state.get(
        "safety",
        SafetyDecision(
            risk_level="medium",
            must_escalate=False,
            reason="未完成安全判断",
        ),
    )
    return {
        "question": _latest_buyer_question(state),
        "agent_messages": [
            SystemMessage(content=TOOL_AGENT_SYSTEM_PROMPT),
            HumanMessage(
                content=build_agent_context(
                    question=_latest_buyer_question(state),
                    product_context=product_context,
                    safety=safety,
                )
            ),
        ],
        "tool_iterations": 0,
        "evidence": [],
        "tool_calls": [],
        "tool_errors": [],
        "generation_attempts": 0,
        "retrieval_violations": [],
        "guard_violations": [],
    }


def _create_agent_node(tool_model):
    async def call_agent(state: KnowledgeAgentState) -> dict:
        response: AIMessage = await tool_model.ainvoke(state.get("agent_messages", []))
        increment = 1 if response.tool_calls else 0
        return {
            "agent_messages": [response],
            "tool_iterations": state.get("tool_iterations", 0) + increment,
        }

    return call_agent


def _route_after_agent(
    state: KnowledgeAgentState,
    max_iterations: int,
) -> Literal["tools", "collect_evidence"]:
    messages = state.get("agent_messages", [])
    last = messages[-1] if messages else None
    if (
        isinstance(last, AIMessage)
        and last.tool_calls
        and state.get("tool_iterations", 0) <= max_iterations
    ):
        return "tools"
    return "collect_evidence"


def _route_after_prepare(
    state: KnowledgeAgentState,
) -> Literal["tool_use_agent", "collect_evidence"]:
    if state.get("product_context", ProductContext()).ambiguous:
        return "collect_evidence"
    return "tool_use_agent"


def _route_after_tools(
    state: KnowledgeAgentState,
    max_iterations: int,
) -> Literal["tool_use_agent", "collect_evidence"]:
    if state.get("tool_iterations", 0) >= max_iterations:
        return "collect_evidence"
    return "tool_use_agent"


def _parse_tool_messages(state: KnowledgeAgentState) -> dict:
    evidence_by_id: dict[str, Evidence] = {}
    call_records: list[ToolCallRecord] = []
    errors: list[str] = []
    result_by_call_id: dict[str, ToolEnvelope] = {}

    for message in state.get("agent_messages", []):
        if not isinstance(message, ToolMessage):
            continue
        try:
            envelope = ToolEnvelope.model_validate_json(str(message.content))
            result_by_call_id[message.tool_call_id] = envelope
            for item in envelope.evidence:
                evidence_by_id[item.evidence_id] = item
            if not envelope.ok and envelope.error:
                errors.append(f"{envelope.tool}: {envelope.error}")
        except Exception as error:
            errors.append(f"工具结果无法解析：{type(error).__name__}: {error}")

    for message in state.get("agent_messages", []):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            envelope = result_by_call_id.get(call["id"])
            call_records.append(
                ToolCallRecord(
                    name=call["name"],
                    call_id=call["id"],
                    arguments=call.get("args", {}),
                    ok=envelope.ok if envelope else None,
                    error=envelope.error if envelope else None,
                )
            )

    evidence = sorted(
        evidence_by_id.values(),
        key=lambda item: (item.source_type, item.source_id, item.evidence_id),
    )
    return {"evidence": evidence, "tool_calls": call_records, "tool_errors": errors}


def _pre_generation_guard(state: KnowledgeAgentState) -> dict:
    violations: list[str] = []
    evidence = state.get("evidence", [])
    product_context = state.get("product_context", ProductContext())
    if product_context.ambiguous:
        violations.append("商品指代存在歧义，需要追问用户")
    if not evidence:
        violations.append("没有可用的检索证据")
    if state.get("tool_errors"):
        violations.extend(state["tool_errors"])
    if any(word in state.get("question", "") for word in DYNAMIC_WORDS):
        has_dynamic_check = any(
            item.source_type == "dynamic_checker" for item in evidence
        )
        if not has_dynamic_check:
            violations.append("问题涉及动态数据，但未执行实时数据检查")
    return {"retrieval_violations": violations, "guard_violations": violations}


def _create_response_node(response_model):
    async def generate_response(state: KnowledgeAgentState) -> dict:
        emotion = state.get("emotion")
        result: DraftModelOutput = await response_model.ainvoke(
            [
                SystemMessage(content=RESPONSE_SYSTEM_PROMPT),
                HumanMessage(
                    content=build_response_context(
                        question=state.get("question", ""),
                        conversation=state.get("conversation", []),
                        emotion=emotion.model_dump() if emotion else None,
                        safety=state["safety"],
                        product_context=state.get("product_context", ProductContext()),
                        evidence=state.get("evidence", []),
                        previous_violations=state.get("guard_violations", []),
                    )
                ),
            ]
        )
        # Evidence ID和GraphRAG内部Data引用只用于审核，不直接展示给客户。
        cleaned_reply = _clean_customer_reply(result.reply)
        result = result.model_copy(update={"reply": cleaned_reply.strip()})
        return {
            "draft": result,
            "generation_attempts": state.get("generation_attempts", 0) + 1,
        }

    return generate_response


def _script_guard(state: KnowledgeAgentState) -> list[str]:
    # 只保留检索阶段无法通过重新生成修复的硬错误。
    # 上一轮草稿的语义违规仅作为修复反馈，不自动延续到新草稿。
    violations = list(state.get("retrieval_violations", []))
    draft = state.get("draft")
    if draft is None:
        return violations + ["回复模型未生成草稿"]
    evidence_ids = {item.evidence_id for item in state.get("evidence", [])}
    unknown_ids = set(draft.cited_evidence_ids) - evidence_ids
    if unknown_ids:
        violations.append(f"回复引用了不存在的证据ID：{sorted(unknown_ids)}")
    if evidence_ids and not draft.cited_evidence_ids:
        violations.append("回复没有引用任何检索证据")
    absolute_check_text = re.sub(r"无法保证|不能保证|不保证|不会保证", "", draft.reply)
    if ABSOLUTE_CLAIMS.search(absolute_check_text):
        violations.append("回复包含绝对承诺")
    medical_check_text = re.sub(
        r"(?:不涉及|不能|不得|并非|不是)[^。；，]{0,12}(?:治疗|治愈|药效)",
        "",
        draft.reply,
    )
    if MEDICAL_CLAIMS.search(medical_check_text):
        violations.append("回复包含医学治疗表述")
    if state["safety"].must_escalate and not re.search(
        r"人工|医生|专业人员|暂停|停止", draft.reply
    ):
        violations.append("高风险回复缺少转人工或专业咨询边界")
    unavailable_dynamic = any(
        item.source_type == "dynamic_checker" and not item.is_realtime
        for item in state.get("evidence", [])
    )
    if unavailable_dynamic:
        for sentence in re.split(r"[。！？\n]", draft.reply):
            claims_current_value = re.search(
                r"现价|今天.*元|当前.*(?:价格|库存|有货|缺货)",
                sentence,
            )
            contains_boundary = re.search(
                r"无法确认|不能确认|无法提供|未接入|Mock|不代表|以.*为准",
                sentence,
                re.IGNORECASE,
            )
            if claims_current_value and not contains_boundary:
                violations.append("将非实时数据表述为当前状态")
                break
    return list(dict.fromkeys(violations))


def _create_semantic_guard_node(guard_model):
    async def validate_response(state: KnowledgeAgentState) -> dict:
        script_violations = _script_guard(state)
        payload = {
            "question": state.get("question", ""),
            "reply": state["draft"].reply,
            "evidence": [item.model_dump() for item in state.get("evidence", [])],
            "safety": state["safety"].model_dump(),
        }
        try:
            semantic: SemanticGuardOutput = await guard_model.ainvoke(
                [
                    SystemMessage(content=EVIDENCE_GUARD_SYSTEM_PROMPT),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ]
            )
        except Exception as error:
            semantic = SemanticGuardOutput(
                supported=False,
                unsupported_claims=[],
                medical_overclaim=False,
                missing_qualifications=[],
                reason=f"语义证据检查失败：{type(error).__name__}: {error}",
            )
        if not semantic.supported:
            script_violations.extend(semantic.unsupported_claims or [semantic.reason])
        if semantic.medical_overclaim:
            script_violations.append("语义检查发现医学效果过度承诺")
        script_violations.extend(semantic.missing_qualifications)
        return {
            "semantic_guard": semantic,
            "guard_violations": list(dict.fromkeys(script_violations)),
        }

    return validate_response


def _route_after_guard(
    state: KnowledgeAgentState,
) -> Literal["generate_response", "finalize"]:
    if state.get("guard_violations") and state.get("generation_attempts", 0) < 2:
        return "generate_response"
    return "finalize"


def _finalize(state: KnowledgeAgentState) -> dict:
    safety = state["safety"]
    violations = state.get("guard_violations", [])
    evidence = state.get("evidence", [])
    draft = state["draft"]
    has_unavailable_dynamic = any(
        item.source_type == "dynamic_checker" and not item.is_realtime
        for item in evidence
    )
    is_comparison = len(state.get("product_context", ProductContext()).product_ids) > 1
    uses_broad_graphrag = any(
        item.fact_type in {"graphrag_drift", "graphrag_global"}
        for item in evidence
    )
    product_context = state.get("product_context", ProductContext())
    if product_context.ambiguous:
        clarification = product_context.clarification or "请先确认具体商品版本。"
        draft = DraftModelOutput(
            reply=clarification,
            cited_evidence_ids=[],
            unsupported_points=["商品版本尚未确认"],
        )
        policy = "escalate"
    elif not evidence:
        draft = DraftModelOutput(
            reply="抱歉，当前没有检索到可靠的商品资料，请转人工核实后再回复。",
            cited_evidence_ids=[],
            unsupported_points=["缺少可靠检索证据"],
        )
        policy = "escalate"
    elif safety.must_escalate or violations:
        policy = "escalate"
    elif (
        safety.risk_level == "medium"
        or has_unavailable_dynamic
        or is_comparison
        or uses_broad_graphrag
        or draft.unsupported_points
    ):
        policy = "draft"
    elif evidence:
        policy = "auto_send"
    else:
        policy = "draft"
    return {"reply_policy": policy, "draft": draft}


def create_knowledge_graph(
    *,
    settings: KnowledgeSettings,
    llm: BaseChatModel,
    emotion_recognizer: EmotionRecognizer,
    tools: list[BaseTool],
):
    """构建完整Knowledge Agent LangGraph。"""
    safety_model = llm.with_structured_output(
        SafetyModelOutput,
        method="function_calling",
    )
    response_model = llm.with_structured_output(
        DraftModelOutput,
        method="function_calling",
    )
    guard_model = llm.with_structured_output(
        SemanticGuardOutput,
        method="function_calling",
    )
    product_reference_model = llm.with_structured_output(
        ProductReferenceModelOutput,
        method="function_calling",
    )
    # 模型可在同一轮返回多个Tool Call，ToolNode会并行执行它们。
    tool_model = llm.bind_tools(tools, parallel_tool_calls=True)
    tool_node = ToolNode(
        tools,
        messages_key="agent_messages",
        handle_tool_errors=True,
    )

    builder = StateGraph(KnowledgeAgentState)
    builder.add_node("emotion_recognition", _create_emotion_node(emotion_recognizer))
    builder.add_node("safety_guard", _create_safety_node(safety_model))
    builder.add_node(
        "product_resolver",
        _create_product_resolver_node(product_reference_model),
    )
    builder.add_node("prepare_agent", _prepare_agent)
    builder.add_node("tool_use_agent", _create_agent_node(tool_model))
    builder.add_node("tools", tool_node)
    builder.add_node("collect_evidence", _parse_tool_messages)
    builder.add_node("pre_generation_guard", _pre_generation_guard)
    builder.add_node("generate_response", _create_response_node(response_model))
    builder.add_node("evidence_guard", _create_semantic_guard_node(guard_model))
    builder.add_node("finalize", _finalize)

    builder.add_edge(START, "emotion_recognition")
    builder.add_edge(START, "safety_guard")
    builder.add_edge(START, "product_resolver")
    builder.add_edge(
        ["emotion_recognition", "safety_guard", "product_resolver"],
        "prepare_agent",
    )
    builder.add_conditional_edges(
        "prepare_agent",
        _route_after_prepare,
        {
            "tool_use_agent": "tool_use_agent",
            "collect_evidence": "collect_evidence",
        },
    )
    builder.add_conditional_edges(
        "tool_use_agent",
        lambda state: _route_after_agent(state, settings.max_tool_iterations),
        {"tools": "tools", "collect_evidence": "collect_evidence"},
    )
    builder.add_conditional_edges(
        "tools",
        lambda state: _route_after_tools(state, settings.max_tool_iterations),
        {
            "tool_use_agent": "tool_use_agent",
            "collect_evidence": "collect_evidence",
        },
    )
    builder.add_edge("collect_evidence", "pre_generation_guard")
    builder.add_edge("pre_generation_guard", "generate_response")
    builder.add_edge("generate_response", "evidence_guard")
    builder.add_conditional_edges(
        "evidence_guard",
        _route_after_guard,
        {"generate_response": "generate_response", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)
    return builder.compile()
