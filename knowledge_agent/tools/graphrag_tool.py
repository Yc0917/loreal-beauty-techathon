"""Microsoft GraphRAG索引检索工具。"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from knowledge_agent.schemas import Evidence, ToolEnvelope
from knowledge_agent.settings import KnowledgeSettings
from knowledge_agent.tools.common import dump_envelope, evidence_id


SearchMethod = Literal["local", "drift", "global", "basic"]
RESULT_MARKER = "GRAPHRAG_RESULT="


def create_graphrag_tool(settings: KnowledgeSettings):
    """创建通过GraphRAG独立虚拟环境运行的检索工具。"""

    @tool
    async def search_product_documents(
        question: str,
        product_ids: list[str],
        method: SearchMethod = "local",
        runtime: ToolRuntime = None,
    ) -> str:
        """检索商品说明、版本对比、使用方法、成分语义、安全边界和FAQ文档。"""
        allowed_product_ids = set(
            runtime.state.get("product_context", {}).product_ids
            if runtime is not None
            and hasattr(runtime.state.get("product_context"), "product_ids")
            else []
        )
        if not product_ids or not set(product_ids).issubset(allowed_product_ids):
            return dump_envelope(
                ToolEnvelope(
                    tool="search_product_documents",
                    ok=False,
                    error="工具参数包含State未确认的商品ID，已拒绝检索",
                    metadata={"method": method},
                )
            )
        if not settings.graphrag_python.is_file():
            return dump_envelope(
                ToolEnvelope(
                    tool="search_product_documents",
                    ok=False,
                    error=f"未找到GraphRAG虚拟环境：{settings.graphrag_python}",
                )
            )
        if not settings.graphrag_query_script.is_file():
            return dump_envelope(
                ToolEnvelope(
                    tool="search_product_documents",
                    ok=False,
                    error=f"未找到GraphRAG查询适配器：{settings.graphrag_query_script}",
                )
            )

        augmented_question = question
        if product_ids:
            augmented_question += "\n已确定商品ID：" + "、".join(product_ids)
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(settings.graphrag_python),
                str(settings.graphrag_query_script),
                "--root",
                str(settings.graphrag_root),
                "--method",
                method,
                "--question",
                augmented_question,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.graphrag_timeout_seconds,
            )
            if process.returncode != 0:
                error_text = stderr.decode("utf-8", errors="replace")[-2000:]
                raise RuntimeError(error_text or f"GraphRAG进程退出码{process.returncode}")

            output_lines = stdout.decode("utf-8", errors="replace").splitlines()
            result_line = next(
                (line for line in reversed(output_lines) if line.startswith(RESULT_MARKER)),
                None,
            )
            if result_line is None:
                raise ValueError("GraphRAG适配器未返回可解析结果")
            payload = json.loads(result_line[len(RESULT_MARKER):])
            response = payload.get("response")
            content = (
                response
                if isinstance(response, str)
                else json.dumps(response, ensure_ascii=False, default=str)
            )
            source_id = f"graphrag:{method}"
            context = payload.get("context")
            context_sections = sorted(context) if isinstance(context, dict) else []
            item = Evidence(
                evidence_id=evidence_id("graphrag", source_id, content),
                source_type="graphrag",
                source_id=source_id,
                product_ids=product_ids,
                fact_type=f"graphrag_{method}",
                content=content,
                risk_level="medium" if method in {"drift", "global"} else "low",
                is_mock=True,
                is_realtime=False,
                metadata={
                    "method": method,
                    "community_level": payload.get("community_level"),
                    "context_sections": context_sections,
                },
            )
            return dump_envelope(
                ToolEnvelope(
                    tool="search_product_documents",
                    ok=True,
                    evidence=[item],
                    metadata={"method": method},
                )
            )
        except asyncio.TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            error = f"GraphRAG查询超过{settings.graphrag_timeout_seconds}秒"
        except Exception as exception:
            error = f"{type(exception).__name__}: {exception}"

        return dump_envelope(
            ToolEnvelope(
                tool="search_product_documents",
                ok=False,
                error=error,
                metadata={"method": method},
            )
        )

    return search_product_documents
