"""实时数据可用性检查工具。"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from knowledge_agent.schemas import Evidence, ToolEnvelope
from knowledge_agent.tools.common import dump_envelope, evidence_id


DynamicKind = Literal[
    "price",
    "stock",
    "promotion",
    "gift",
    "logistics",
    "order",
    "after_sales",
]


def create_dynamic_data_tool():
    """创建当前不会访问真实千牛的动态数据检查工具。"""

    @tool
    async def check_dynamic_data(
        kind: DynamicKind,
        product_ids: list[str],
        question: str,
        runtime: ToolRuntime = None,
    ) -> str:
        """检查价格、库存、优惠、订单等数据是否有实时来源。"""
        allowed_product_ids = set(
            runtime.state.get("product_context", {}).product_ids
            if runtime is not None
            and hasattr(runtime.state.get("product_context"), "product_ids")
            else []
        )
        if product_ids and not set(product_ids).issubset(allowed_product_ids):
            return dump_envelope(
                ToolEnvelope(
                    tool="check_dynamic_data",
                    ok=False,
                    error="工具参数包含State未确认的商品ID，已拒绝检查",
                )
            )
        content = (
            f"问题需要实时{kind}数据，当前未接入千牛或店铺实时接口。"
            "Neo4j中的价格、库存、优惠和赠品均为开发Mock数据，"
            "不得当作当前店铺状态。"
        )
        source_id = f"dynamic:{kind}"
        item = Evidence(
            evidence_id=evidence_id("dynamic_checker", source_id, content),
            source_type="dynamic_checker",
            source_id=source_id,
            product_ids=product_ids,
            fact_type=kind,
            content=content,
            risk_level="medium",
            is_mock=False,
            is_realtime=False,
            metadata={"available": False, "question": question},
        )
        return dump_envelope(
            ToolEnvelope(
                tool="check_dynamic_data",
                ok=True,
                evidence=[item],
                metadata={"available": False},
            )
        )

    return check_dynamic_data
