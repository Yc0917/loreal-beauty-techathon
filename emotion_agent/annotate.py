"""构建逐轮样本并执行 LLM 情绪标注的命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from emotion_agent.dataset_builder import build_annotation_cases, write_cases_jsonl
from emotion_agent.llm_annotator import annotate_cases


DEFAULT_SOURCE = Path("赛题 1：数据共情者-业务数据.xlsx")
DEFAULT_CASES_OUTPUT = Path("emotion_agent/outputs/emotion_turn_cases.jsonl")
DEFAULT_ANNOTATIONS_OUTPUT = Path("emotion_agent/outputs/llm_annotations.jsonl")


def parse_args() -> argparse.Namespace:
    """解析批量标注参数。"""
    parser = argparse.ArgumentParser(description="逐买家消息构建并标注情绪数据")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--cases-output", type=Path, default=DEFAULT_CASES_OUTPUT)
    parser.add_argument(
        "--annotations-output",
        type=Path,
        default=DEFAULT_ANNOTATIONS_OUTPUT,
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="只生成未标注样本，不调用 LLM",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="本次最多调用的样本数，便于先做小批量检查",
    )
    return parser.parse_args()


def main() -> None:
    """构建样本，并按参数决定是否调用 LLM。"""
    args = parse_args()
    cases = build_annotation_cases(args.source)
    write_cases_jsonl(cases, args.cases_output)
    print(f"已生成 {len(cases)} 条轮次样本：{args.cases_output}")

    if args.build_only:
        return
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit 必须大于 0")
    summary = annotate_cases(cases, args.annotations_output, args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
