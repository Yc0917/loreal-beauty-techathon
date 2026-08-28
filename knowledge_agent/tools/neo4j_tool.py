"""Neo4j商品图谱只读检索工具。"""

from __future__ import annotations

import json
import re
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from neo4j import AsyncGraphDatabase, READ_ACCESS
from pydantic import BaseModel, ConfigDict, Field
from langgraph.prebuilt import ToolRuntime

from knowledge_agent.prompts import TEXT2CYPHER_SYSTEM_PROMPT
from knowledge_agent.schemas import Evidence, ToolEnvelope
from knowledge_agent.settings import KnowledgeSettings
from knowledge_agent.tools.common import dump_envelope, evidence_id


QueryKind = Literal[
    "auto",
    "product_overview",
    "skus",
    "ingredients",
    "faq",
    "comparison",
    "text2cypher",
]


GRAPH_SCHEMA = """
(:Product {product_id,name,version_name,positioning,texture,usage,filing_number})
-[:HAS_SKU]->(:SKU {sku_id,capacity_ml,style,price_cny,stock,stock_status,promotion,gift,barcode})
(:Product)-[:CONTAINS {position,role,is_key}]->(:Ingredient {ingredient_id,inci_name,display_name,function,risk_note})
(:Product)-[:HAS_FAQ]->(:FAQ {faq_id,intent,question,answer,risk_level,expected_route})
(:Product)-[:SUITABLE_FOR]->(:SkinType {name})
(:Product)-[:HAS_EFFECT]->(:Effect {name})
(:Product)-[:BRANDED_BY]->(:Brand {name})
(:Product)-[:IN_CATEGORY]->(:Category {name})
""".strip()


PREDEFINED_QUERIES: dict[str, str] = {
    "product_overview": """
        MATCH (p:Product)
        WHERE p.product_id IN $product_ids
        OPTIONAL MATCH (p)-[:SUITABLE_FOR]->(skin:SkinType)
        OPTIONAL MATCH (p)-[:HAS_EFFECT]->(effect:Effect)
        RETURN p.product_id AS product_id, p.name AS name,
               p.version_name AS version_name, p.positioning AS positioning,
               p.texture AS texture, p.usage AS usage,
               p.filing_number AS filing_number,
               collect(DISTINCT skin.name) AS skin_types,
               collect(DISTINCT effect.name) AS effects
        LIMIT 50
    """,
    "skus": """
        MATCH (p:Product)-[:HAS_SKU]->(s:SKU)
        WHERE p.product_id IN $product_ids
          AND ($capacity_ml IS NULL OR s.capacity_ml = $capacity_ml)
        RETURN p.product_id AS product_id, p.name AS product_name,
               s.sku_id AS sku_id, s.capacity_ml AS capacity_ml,
               s.style AS style, s.price_cny AS price_cny,
               s.stock AS stock, s.stock_status AS stock_status,
               s.promotion AS promotion, s.gift AS gift, s.barcode AS barcode
        ORDER BY p.product_id, s.capacity_ml
        LIMIT 50
    """,
    "ingredients": """
        MATCH (p:Product)-[r:CONTAINS]->(i:Ingredient)
        WHERE p.product_id IN $product_ids
          AND ($ingredient IS NULL
               OR toLower(i.display_name) CONTAINS toLower($ingredient)
               OR toLower(i.inci_name) CONTAINS toLower($ingredient))
        RETURN p.product_id AS product_id, p.name AS product_name,
               i.ingredient_id AS ingredient_id,
               i.display_name AS ingredient, i.inci_name AS inci_name,
               i.function AS function, i.risk_note AS risk_note,
               r.position AS position, r.role AS role, r.is_key AS is_key
        ORDER BY p.product_id, r.position
        LIMIT 50
    """,
    "faq": """
        MATCH (p:Product)-[:HAS_FAQ]->(f:FAQ)
        WHERE p.product_id IN $product_ids
          AND ($intent IS NULL OR f.intent = $intent)
        RETURN p.product_id AS product_id, p.name AS product_name,
               f.faq_id AS faq_id, f.intent AS intent,
               f.question AS question, f.answer AS answer,
               f.risk_level AS risk_level, f.expected_route AS expected_route
        LIMIT 50
    """,
    "comparison": """
        MATCH (p:Product)
        WHERE p.product_id IN $product_ids
        OPTIONAL MATCH (p)-[:SUITABLE_FOR]->(skin:SkinType)
        OPTIONAL MATCH (p)-[:HAS_EFFECT]->(effect:Effect)
        OPTIONAL MATCH (p)-[:CONTAINS]->(ingredient:Ingredient)
        RETURN p.product_id AS product_id, p.name AS name,
               p.positioning AS positioning, p.texture AS texture,
               p.usage AS usage,
               collect(DISTINCT skin.name) AS skin_types,
               collect(DISTINCT effect.name) AS effects,
               collect(DISTINCT ingredient.display_name) AS ingredients
        LIMIT 50
    """,
}


FORBIDDEN_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|CALL)\b",
    re.IGNORECASE,
)
COMMENT_PATTERN = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
PROPERTY_PATTERN = re.compile(r"\b[a-zA-Z_]\w*\.([a-zA-Z_]\w*)")
LABEL_PATTERN = re.compile(r"\([^)]*:\s*([A-Za-z_]\w*)")
RELATIONSHIP_PATTERN = re.compile(r"\[[^]]*:\s*([A-Za-z_]\w*)")

ALLOWED_LABELS = {
    "Product", "SKU", "Ingredient", "FAQ", "SkinType", "Effect", "Brand", "Category"
}
ALLOWED_RELATIONSHIPS = {
    "HAS_SKU", "CONTAINS", "HAS_FAQ", "SUITABLE_FOR", "HAS_EFFECT",
    "BRANDED_BY", "IN_CATEGORY",
}
ALLOWED_PROPERTIES = {
    "product_id", "name", "version_name", "positioning", "texture", "usage",
    "filing_number", "sku_id", "capacity_ml", "style", "price_cny", "stock",
    "stock_status", "promotion", "gift", "barcode", "ingredient_id", "inci_name",
    "display_name", "function", "risk_note", "position", "role", "is_key",
    "faq_id", "intent", "question", "answer", "risk_level", "expected_route",
}


class GeneratedCypher(BaseModel):
    """Text2Cypher模型只能返回一条查询。"""

    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=5000)


def _strip_code_fence(statement: str) -> str:
    cleaned = statement.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:cypher)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def validate_read_only_cypher(statement: str) -> str:
    """在执行前用确定性Hook拒绝写操作和越界Schema。"""
    cleaned = _strip_code_fence(statement)
    without_comments = COMMENT_PATTERN.sub(" ", cleaned)
    if ";" in without_comments:
        raise ValueError("禁止执行多条Cypher语句")
    forbidden = FORBIDDEN_CLAUSES.search(without_comments)
    if forbidden:
        raise ValueError(f"禁止的Cypher子句：{forbidden.group(0)}")
    if not re.match(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND)\b", without_comments, re.I):
        raise ValueError("Cypher必须以只读子句开始")
    if not re.search(r"\bRETURN\b", without_comments, re.I):
        raise ValueError("Cypher必须返回查询结果")
    if "$product_ids" not in without_comments:
        raise ValueError("Text2Cypher必须使用$product_ids限定商品")

    labels = set(LABEL_PATTERN.findall(without_comments))
    relationships = set(RELATIONSHIP_PATTERN.findall(without_comments))
    properties = set(PROPERTY_PATTERN.findall(without_comments))
    if labels - ALLOWED_LABELS:
        raise ValueError(f"出现未允许的节点标签：{sorted(labels - ALLOWED_LABELS)}")
    if relationships - ALLOWED_RELATIONSHIPS:
        raise ValueError(
            f"出现未允许的关系：{sorted(relationships - ALLOWED_RELATIONSHIPS)}"
        )
    if properties - ALLOWED_PROPERTIES:
        raise ValueError(f"出现未允许的属性：{sorted(properties - ALLOWED_PROPERTIES)}")

    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", without_comments, re.I)
    if limit_match and int(limit_match.group(1)) > 50:
        raise ValueError("Cypher返回数量不得超过50")
    if not limit_match:
        cleaned = f"{cleaned}\nLIMIT 50"
    return cleaned


def _select_query_kind(question: str, requested: QueryKind) -> QueryKind:
    if requested != "auto":
        return requested
    if any(word in question for word in ("容量", "ml", "ML", "规格", "SKU", "库存", "价格", "多少钱")):
        return "skus"
    if any(word in question for word in ("成分", "A醇", "视黄醇", "水杨酸")):
        return "ingredients"
    if any(word in question for word in ("对比", "区别", "哪个", "选哪")):
        return "comparison"
    if any(word in question for word in ("FAQ", "怎么用", "能用吗", "适合")):
        return "faq"
    return "product_overview"


async def _execute_query(
    settings: KnowledgeSettings,
    statement: str,
    parameters: dict,
) -> list[dict]:
    """使用Neo4j只读会话，先EXPLAIN再执行。"""
    auth = (
        (settings.neo4j_username, settings.neo4j_password or "")
        if settings.neo4j_username
        else None
    )
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=auth,
        connection_timeout=3,
        max_transaction_retry_time=3,
    )
    try:
        async with driver.session(
            database=settings.neo4j_database,
            default_access_mode=READ_ACCESS,
        ) as session:
            async def run_read(tx):
                # EXPLAIN只生成计划，在真实读取前发现语法或Schema错误。
                explain_result = await tx.run(f"EXPLAIN {statement}", parameters)
                await explain_result.consume()
                result = await tx.run(statement, parameters)
                return [record.data() async for record in result]

            return await session.execute_read(run_read)
    finally:
        await driver.close()


def _records_to_evidence(
    *,
    records: list[dict],
    query_kind: str,
    statement: str,
    product_ids: list[str],
) -> list[Evidence]:
    items: list[Evidence] = []
    for index, record in enumerate(records):
        content = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        source_id = str(
            record.get("faq_id")
            or record.get("sku_id")
            or record.get("ingredient_id")
            or record.get("product_id")
            or f"neo4j-row-{index + 1}"
        )
        risk = str(record.get("risk_level", "low"))
        if risk not in {"low", "medium", "high"}:
            risk = "low"
        items.append(
            Evidence(
                evidence_id=evidence_id("neo4j", source_id, content),
                source_type="neo4j",
                source_id=source_id,
                product_ids=[str(record.get("product_id"))]
                if record.get("product_id")
                else product_ids,
                fact_type=query_kind,
                content=content,
                risk_level=risk,
                is_mock=True,
                is_realtime=False,
                metadata={},
            )
        )
    return items


def create_neo4j_tool(settings: KnowledgeSettings, llm: BaseChatModel):
    """创建带受控Text2Cypher兜底的Neo4j工具。"""
    structured_cypher_llm = llm.with_structured_output(
        GeneratedCypher,
        method="function_calling",
    )

    @tool
    async def query_product_graph(
        question: str,
        product_ids: list[str],
        query_kind: QueryKind = "auto",
        capacity_ml: int | None = None,
        ingredient: str | None = None,
        intent: str | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """只读查询商品、SKU、成分、肤质、功效和FAQ等Neo4j结构化数据。"""
        allowed_product_ids = set(
            runtime.state.get("product_context", {}).product_ids
            if runtime is not None
            and hasattr(runtime.state.get("product_context"), "product_ids")
            else []
        )
        if not product_ids or not set(product_ids).issubset(allowed_product_ids):
            return dump_envelope(
                ToolEnvelope(
                    tool="query_product_graph",
                    ok=False,
                    error="工具参数包含State未确认的商品ID，已拒绝查询",
                )
            )
        selected_kind = _select_query_kind(question, query_kind)
        parameters = {
            "product_ids": product_ids,
            "capacity_ml": capacity_ml,
            "ingredient": ingredient,
            "intent": intent,
        }
        try:
            if selected_kind == "text2cypher":
                generated = await structured_cypher_llm.ainvoke(
                    [
                        SystemMessage(content=TEXT2CYPHER_SYSTEM_PROMPT),
                        HumanMessage(
                            content=f"图Schema：\n{GRAPH_SCHEMA}\n\n用户问题：{question}"
                        ),
                    ]
                )
                statement = validate_read_only_cypher(generated.statement)
            else:
                statement = PREDEFINED_QUERIES[selected_kind]
                # 预定义查询也经过同一只读Hook，避免后续维护误加写操作。
                statement = validate_read_only_cypher(statement)
            records = await _execute_query(settings, statement, parameters)
            evidence = _records_to_evidence(
                records=records,
                query_kind=selected_kind,
                statement=statement,
                product_ids=product_ids,
            )
            return dump_envelope(
                ToolEnvelope(
                    tool="query_product_graph",
                    ok=True,
                    evidence=evidence,
                    metadata={
                        "query_kind": selected_kind,
                        "record_count": len(records),
                        "statement": statement,
                    },
                )
            )
        except Exception as error:
            return dump_envelope(
                ToolEnvelope(
                    tool="query_product_graph",
                    ok=False,
                    error=f"{type(error).__name__}: {error}",
                    metadata={"query_kind": selected_kind},
                )
            )

    return query_product_graph
