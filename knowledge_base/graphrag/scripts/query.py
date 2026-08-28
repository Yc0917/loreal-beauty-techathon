#!/usr/bin/env python3
"""使用GraphRAG 2.1.0 Python API查询已构建索引。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

import graphrag.api as api
from graphrag.config.load_config import load_config
from graphrag.storage.file_pipeline_storage import FilePipelineStorage
from graphrag.utils.storage import load_table_from_storage


RESULT_MARKER = "GRAPHRAG_RESULT="


def _json_safe(value: Any) -> Any:
    """将GraphRAG上下文中的DataFrame和NumPy值转为精简JSON。"""
    if isinstance(value, pd.DataFrame):
        return value.head(5).where(pd.notnull(value), None).to_dict(orient="records")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value[:10]]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def _load_tables(storage: FilePipelineStorage) -> dict[str, pd.DataFrame]:
    names = [
        "entities",
        "communities",
        "community_reports",
        "text_units",
        "relationships",
    ]
    values = await asyncio.gather(
        *(load_table_from_storage(name, storage) for name in names)
    )
    return dict(zip(names, values, strict=True))


async def query(root: Path, method: str, question: str) -> dict[str, Any]:
    """根据选定方法查询GraphRAG索引。"""
    load_dotenv(root / ".env")
    config = load_config(root)
    output_dir = Path(config.output.base_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    storage = FilePipelineStorage(root_dir=str(output_dir))
    tables = await _load_tables(storage)

    levels = tables["communities"].get("level")
    community_level = int(levels.min()) if levels is not None and len(levels) else 0
    response_type = "一段简洁、可核验的中文客服知识说明"

    if method == "local":
        response, context = await api.local_search(
            config=config,
            entities=tables["entities"],
            communities=tables["communities"],
            community_reports=tables["community_reports"],
            text_units=tables["text_units"],
            relationships=tables["relationships"],
            covariates=None,
            community_level=community_level,
            response_type=response_type,
            query=question,
        )
    elif method == "drift":
        response, context = await api.drift_search(
            config=config,
            entities=tables["entities"],
            communities=tables["communities"],
            community_reports=tables["community_reports"],
            text_units=tables["text_units"],
            relationships=tables["relationships"],
            community_level=community_level,
            response_type=response_type,
            query=question,
        )
    elif method == "global":
        response, context = await api.global_search(
            config=config,
            entities=tables["entities"],
            communities=tables["communities"],
            community_reports=tables["community_reports"],
            community_level=community_level,
            dynamic_community_selection=False,
            response_type=response_type,
            query=question,
        )
    elif method == "basic":
        response, context = await api.basic_search(
            config=config,
            text_units=tables["text_units"],
            query=question,
        )
    else:
        raise ValueError(f"不支持的GraphRAG查询方法：{method}")

    return {
        "method": method,
        "response": _json_safe(response),
        "context": _json_safe(context),
        "community_level": community_level,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--method", choices=["local", "drift", "global", "basic"], required=True)
    parser.add_argument("--question", required=True)
    args = parser.parse_args()
    result = asyncio.run(query(args.root.resolve(), args.method, args.question))
    # 固定前缀便于主进程忽略依赖库可能产生的普通日志。
    print(RESULT_MARKER + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
