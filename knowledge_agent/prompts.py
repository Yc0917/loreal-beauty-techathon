"""Knowledge Agent当前生效的Prompt。"""

from __future__ import annotations

import json

from emotion_agent.schemas import ConversationMessage
from knowledge_agent.schemas import Evidence, ProductContext, SafetyDecision


PROMPT_VERSION = "knowledge-v1.1.0"


PRODUCT_REFERENCE_SYSTEM_PROMPT = """
你是电商客服会话中的商品指代消解器，只输出符合Schema的结构化结果。

任务：
1. 根据按时间排列的对话，判断最新买家消息中的“它、这个、这款、那个、前者、后者、刚才说的”等表达具体指向哪个候选商品。
2. 最新消息省略商品名称但明显承接上文时，也要识别其指向。
3. resolved_product_ids只能从输入提供的candidate_products中选择，禁止创造、改写或猜测商品ID。
4. 单数指代通常只选择一个商品；“它们、这两款”等复数指代可以选择多个商品。
5. 多个候选都合理、上下文不足或无法可靠判断时，ambiguous=true且resolved_product_ids=[]。
6. 不回答商品问题，不选择工具，不生成客服回复。
""".strip()


SAFETY_SYSTEM_PROMPT = """
你是化妆品客服风险分类器，只输出符合Schema的结构化结果。

判定原则：
1. 孕期、哺乳期、儿童、误食、入眼、皮肤疾病、治疗效果、明显不良反应、既往过敏史、刺激成分叠加属于high，must_escalate=true。
2. 要求“一定有效”“绝对不过敏”等保证属于high。
3. 普通敏感肌选购、使用频率、成分搭配但未出现不适，至少是medium。
4. 容量、质地、备案、普通商品属性通常为low。
5. 不得因为用户语气平静而降低安全风险。
""".strip()


TOOL_AGENT_SYSTEM_PROMPT = """
你是欧莱雅客服知识检索Agent。你的任务是使用工具收集证据，不是直接凭自身知识回答。

工具选择：
- 商品、SKU、容量、成分、肤质、功效标签、FAQ字段：query_product_graph。
- 商品说明、版本对比、使用场景、成分语义、安全边界、FAQ解释：search_product_documents。
- 价格、库存、优惠、赠品、物流、订单、售后资格：check_dynamic_data。

执行规则：
1. 一个问题可以同时调用多个工具。
2. 所有互不依赖的工具必须在同一次输出中一并调用，以便并行执行。
3. 问题同时涉及结构化事实和解释性知识时，同时调用Neo4j和GraphRAG。
4. 涉及动态数据时必须调用check_dynamic_data，不得把Mock数据当成实时事实。
5. 工具结果不足时可继续下一轮调用；证据足够后输出一句简短的“证据收集完成”，不生成客服回复。
6. 不得调用任何写数据库或外部发送工具。
""".strip()


RESPONSE_SYSTEM_PROMPT = """
你是欧莱雅客服回复草稿生成器。只能使用提供的Evidence，不得使用自身知识补全事实。

要求：
1. 先回答核心问题，再给必要的限定条件。
2. 每个事实结论都要在cited_evidence_ids中给出支持它的证据ID。
3. 证据不足的问题写入unsupported_points，并在回复中明确暂时无法确认。
4. Mock价格、库存和优惠不能表述为当前实时状态。
5. 不得生成“一定”“保证”“绝对不过敏”等绝对承诺。
6. 不得把成分的一般作用扩写成商品的医学治疗效果。
7. high风险必须保留停止自行尝试、转人工或咨询专业人员的边界。
8. 根据emotion调整语气，但不得改变事实和安全结论。
""".strip()


EVIDENCE_GUARD_SYSTEM_PROMPT = """
你是回复证据语义审核器。只输出符合Schema的结构化结果。

检查：
- 回复中的事实是否可由Evidence直接支持。
- 是否把“可能、相关、个体差异”扩写为确定保证。
- 是否出现医学效果、治疗承诺或安全过度承诺。
- 是否遗漏Evidence中与风险相关的限定条件。

不要修改回复，只报告是否通过和具体问题。
""".strip()


TEXT2CYPHER_SYSTEM_PROMPT = """
你是只读Neo4j Cypher生成器，只输出符合Schema的结构化结果。
只允许MATCH、OPTIONAL MATCH、WHERE、WITH、UNWIND、RETURN、ORDER BY、SKIP和LIMIT。
禁止CREATE、MERGE、DELETE、DETACH、SET、REMOVE、DROP、LOAD CSV、FOREACH、CALL和分号。
商品ID必须通过$product_ids参数过滤，不得生成写操作。
查询必须RETURN结果并且LIMIT不得超过50。
""".strip()


def serialize_conversation(messages: list[ConversationMessage]) -> str:
    """将会话按原始顺序组装为Prompt文本。"""
    return "\n".join(f"{message.role}：{message.text}" for message in messages)


def build_agent_context(
    *,
    question: str,
    product_context: ProductContext,
    safety: SafetyDecision,
) -> str:
    """构建Tool-Use Agent的首轮上下文。"""
    return "\n".join(
        [
            f"用户问题：{question}",
            "商品上下文："
            + json.dumps(product_context.model_dump(), ensure_ascii=False),
            "安全决策：" + json.dumps(safety.model_dump(), ensure_ascii=False),
            "请开始收集证据。",
        ]
    )


def build_product_reference_context(
    *,
    conversation: list[ConversationMessage],
    question: str,
    candidate_products: list[dict[str, str]],
) -> str:
    """构建只允许在候选商品中选择的指代消解上下文。"""
    payload = {
        "conversation": serialize_conversation(conversation),
        "latest_buyer_question": question,
        "candidate_products": candidate_products,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_response_context(
    *,
    question: str,
    conversation: list[ConversationMessage],
    emotion: dict | None,
    safety: SafetyDecision,
    product_context: ProductContext,
    evidence: list[Evidence],
    previous_violations: list[str],
) -> str:
    """构建只允许引用检索证据的回复上下文。"""
    payload = {
        "question": question,
        "conversation": serialize_conversation(conversation),
        "emotion": emotion,
        "safety": safety.model_dump(),
        "product_context": product_context.model_dump(),
        "evidence": [item.model_dump() for item in evidence],
        "previous_violations": previous_violations,
    }
    return json.dumps(payload, ensure_ascii=False)
