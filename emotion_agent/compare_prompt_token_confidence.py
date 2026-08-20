"""在同一批会话样本上比较四分类与五分类 Prompt 的 Token 置信度。"""

from __future__ import annotations

import argparse
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import fmean, median
from typing import Any, Literal, get_args

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from emotion_agent.prompts import (
    EMOTION_ANALYSIS_SYSTEM_PROMPT as FOUR_CLASS_SYSTEM_PROMPT,
)
from emotion_agent.prompts import PROMPT_VERSION as FOUR_CLASS_PROMPT_VERSION
from emotion_agent.prompts import build_conversation_prompt as build_four_class_prompt
from emotion_agent.prompts_five_class import (
    EMOTION_ANALYSIS_SYSTEM_PROMPT as FIVE_CLASS_SYSTEM_PROMPT,
)
from emotion_agent.prompts_five_class import PROMPT_VERSION as FIVE_CLASS_PROMPT_VERSION
from emotion_agent.prompts_five_class import (
    build_conversation_prompt as build_five_class_prompt,
)
from emotion_agent.providers import create_openai_compatible_structured_model
from emotion_agent.recognizer import (
    _calculate_token_confidence,
    _extract_emotion_token_logprobs,
)
from emotion_agent.schemas import (
    ConversationMessage,
    EmotionModelOutput,
    EmotionTrend,
)
from emotion_agent.settings import LLMSettings


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "emotion_agent/outputs/emotion_turn_cases.jsonl"
JSON_MODE_TRANSPORT_INSTRUCTION = (
    "<transport_format>\n输出格式必须是 json 对象。\n</transport_format>"
)
FiveEmotionLabel = Literal[
    "neutral",
    "positive",
    "anxious",
    "dissatisfied",
    "angry",
]


class FiveClassEmotionModelOutput(BaseModel):
    """历史五分类 Prompt 对应的结构化输出。"""

    model_config = ConfigDict(extra="forbid")

    emotion: FiveEmotionLabel
    confidence: float = Field(ge=0, le=1)
    trend: EmotionTrend
    evidence: list[str] = Field(min_length=1, max_length=3)
    summary: str = Field(min_length=1, max_length=120)


def _safe_name(value: str) -> str:
    """将模型名转换为安全的文件名片段。"""
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-") or "model"


def _load_cases(path: Path) -> list[dict[str, Any]]:
    """读取逐买家轮次样本，不读取任何 Ground Truth。"""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _unpack_raw_result(
    result: object,
    schema: type[BaseModel],
) -> tuple[str, BaseMessage]:
    """从 include_raw 返回值中取出情绪标签和原始消息。"""
    if not isinstance(result, dict):
        raise TypeError(f"模型返回类型不正确：{type(result).__name__}")
    parsed = result.get("parsed")
    raw = result.get("raw")
    if not isinstance(raw, BaseMessage):
        raise ValueError("模型未返回原始消息")

    if parsed is not None:
        validated = parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        emotion = str(getattr(validated, "emotion"))
    else:
        # 比较任务只需要标签 Token；summary 等字段越界不应丢弃有效 logprobs。
        if not isinstance(raw.content, str):
            raise ValueError("模型原始输出不是文本")
        payload = json.loads(raw.content)
        emotion = payload.get("emotion")
        allowed_labels = set(get_args(schema.model_fields["emotion"].annotation))
        if not isinstance(emotion, str) or emotion not in allowed_labels:
            raise ValueError("模型原始输出的 emotion 标签无效")
    return emotion, raw


def _run_prompt(
    *,
    model: Any,
    output_schema: type[BaseModel],
    system_prompt: str,
    user_prompt: str,
    max_retries: int,
) -> dict[str, Any]:
    """执行一个 Prompt，且只以标签 Token logprobs 计算置信度。"""
    errors: list[str] = []
    for attempt in range(1, max_retries + 2):
        try:
            result = model.invoke(
                [
                    # 两个版本追加完全相同的传输层指令，仅用于满足 JSON mode。
                    SystemMessage(
                        content=(
                            f"{system_prompt}\n\n"
                            f"{JSON_MODE_TRANSPORT_INSTRUCTION}"
                        )
                    ),
                    HumanMessage(content=user_prompt),
                ]
            )
            emotion, raw = _unpack_raw_result(result, output_schema)
            tokens = _extract_emotion_token_logprobs(raw, emotion)
            return {
                "emotion": emotion,
                "token_confidence": _calculate_token_confidence(tokens),
                "emotion_tokens": [item.model_dump() for item in tokens],
                "attempts": attempt,
                "error": None,
            }
        except Exception as error:  # noqa: BLE001 - 需在结果中保留每次失败。
            errors.append(f"{type(error).__name__}: {error}")
    return {
        "emotion": None,
        "token_confidence": None,
        "emotion_tokens": [],
        "attempts": max_retries + 1,
        "error": errors[-1] if errors else "未知错误",
    }


def _percentile(values: list[float], proportion: float) -> float | None:
    """使用线性插值计算指定分位数。"""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _confidence_stats(values: list[float]) -> dict[str, float | int | None]:
    """生成 Token 置信度的统计摘要。"""
    return {
        "count": len(values),
        "mean": fmean(values) if values else None,
        "median": median(values) if values else None,
        "min": min(values) if values else None,
        "p10": _percentile(values, 0.10),
        "p25": _percentile(values, 0.25),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
        "max": max(values) if values else None,
    }


def _variant_summary(
    records: list[dict[str, Any]],
    key: str,
) -> dict[str, Any]:
    """按版本和预测标签汇总 Token 置信度。"""
    successful = [row[key] for row in records if row[key]["error"] is None]
    labels = sorted({str(item["emotion"]) for item in successful})
    return {
        "successful": len(successful),
        "failed": len(records) - len(successful),
        "overall": _confidence_stats(
            [float(item["token_confidence"]) for item in successful]
        ),
        "per_predicted_label": {
            label: _confidence_stats(
                [
                    float(item["token_confidence"])
                    for item in successful
                    if item["emotion"] == label
                ]
            )
            for label in labels
        },
    }


def _build_summary(
    records: list[dict[str, Any]],
    model_name: str,
) -> dict[str, Any]:
    """比较两个 Prompt 的整体、配对及标签切换情况。"""
    paired = [
        row
        for row in records
        if row["four_class"]["error"] is None
        and row["five_class"]["error"] is None
    ]
    same_label = [
        row
        for row in paired
        if row["four_class"]["emotion"] == row["five_class"]["emotion"]
    ]
    changed_label = [row for row in paired if row not in same_label]

    def delta(row: dict[str, Any]) -> float:
        return float(row["five_class"]["token_confidence"]) - float(
            row["four_class"]["token_confidence"]
        )

    deltas = [delta(row) for row in paired]
    same_label_deltas = [delta(row) for row in same_label]
    switch_counts: dict[str, int] = {}
    switch_rows: dict[str, list[dict[str, Any]]] = {}
    for row in changed_label:
        switch = (
            f"{row['four_class']['emotion']} -> "
            f"{row['five_class']['emotion']}"
        )
        switch_counts[switch] = switch_counts.get(switch, 0) + 1
        switch_rows.setdefault(switch, []).append(row)

    same_label_rows: dict[str, list[dict[str, Any]]] = {}
    for row in same_label:
        same_label_rows.setdefault(str(row["four_class"]["emotion"]), []).append(row)

    def paired_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """同时汇总两版置信度及五分类减四分类的差值。"""
        return {
            "count": len(rows),
            "four_class": _confidence_stats(
                [float(row["four_class"]["token_confidence"]) for row in rows]
            ),
            "five_class": _confidence_stats(
                [float(row["five_class"]["token_confidence"]) for row in rows]
            ),
            "five_minus_four": _confidence_stats([delta(row) for row in rows]),
        }

    epsilon = 1e-12
    return {
        "model": model_name,
        "input_samples": len(records),
        "ground_truth_used": False,
        "confidence_formula": "exp(sum(emotion_label_token_logprobs))",
        "shared_transport_instruction": JSON_MODE_TRANSPORT_INSTRUCTION,
        "four_class": {
            "prompt_version": FOUR_CLASS_PROMPT_VERSION,
            **_variant_summary(records, "four_class"),
        },
        "five_class": {
            "prompt_version": FIVE_CLASS_PROMPT_VERSION,
            **_variant_summary(records, "five_class"),
        },
        "paired_comparison": {
            "paired_successful": len(paired),
            "same_label_count": len(same_label),
            "changed_label_count": len(changed_label),
            "same_label_rate": len(same_label) / len(paired) if paired else None,
            "five_minus_four_delta": _confidence_stats(deltas),
            "same_label_five_minus_four_delta": _confidence_stats(
                same_label_deltas
            ),
            "four_higher_count": sum(value < -epsilon for value in deltas),
            "five_higher_count": sum(value > epsilon for value in deltas),
            "tie_count": sum(abs(value) <= epsilon for value in deltas),
            "label_switch_counts": dict(
                sorted(switch_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
            "same_label_per_label": {
                label: paired_stats(rows)
                for label, rows in sorted(same_label_rows.items())
            },
            "label_switch_confidence": {
                switch: paired_stats(rows)
                for switch, rows in sorted(
                    switch_rows.items(),
                    key=lambda item: (-len(item[1]), item[0]),
                )
            },
        },
    }


def parse_args() -> argparse.Namespace:
    """读取命令行参数。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "emotion_agent/outputs")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--suffix", default="20260819")
    return parser.parse_args()


def main() -> int:
    """运行双 Prompt 配对比较并保存结果。"""
    args = parse_args()
    if args.workers < 1:
        raise ValueError("--workers 必须大于 0")
    cases = _load_cases(args.input)
    if args.limit is not None:
        cases = cases[: args.limit]

    settings = LLMSettings.from_env()
    four_model = create_openai_compatible_structured_model(
        EmotionModelOutput,
        settings,
        include_raw=True,
        include_logprobs=True,
    )
    five_model = create_openai_compatible_structured_model(
        FiveClassEmotionModelOutput,
        settings,
        include_raw=True,
        include_logprobs=True,
    )

    stem = (
        "prompt_token_confidence_compare_"
        f"{FOUR_CLASS_PROMPT_VERSION}_vs_{FIVE_CLASS_PROMPT_VERSION}_"
        f"{_safe_name(settings.model)}_{args.suffix}"
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / f"{stem}.jsonl"
    summary_path = args.output_dir / f"{stem}_summary.json"
    if result_path.exists() or summary_path.exists():
        raise FileExistsError(f"比较结果已存在：{stem}")

    def evaluate(case: dict[str, Any]) -> dict[str, Any]:
        messages = [
            ConversationMessage.model_validate(
                {"role": message["role"], "text": message["text"]}
            )
            for message in case["messages"]
        ]
        scene = str(case.get("scene") or "未知")
        four_result = _run_prompt(
            model=four_model,
            output_schema=EmotionModelOutput,
            system_prompt=FOUR_CLASS_SYSTEM_PROMPT,
            user_prompt=build_four_class_prompt(scene, messages),
            max_retries=settings.schema_max_retries,
        )
        five_result = _run_prompt(
            model=five_model,
            output_schema=FiveClassEmotionModelOutput,
            system_prompt=FIVE_CLASS_SYSTEM_PROMPT,
            user_prompt=build_five_class_prompt(scene, messages),
            max_retries=settings.schema_max_retries,
        )
        return {
            "case_id": case["case_id"],
            "conversation_id": case["conversation_id"],
            "buyer_turn": case["buyer_turn"],
            "target_text": case["target_text"],
            "four_class": four_result,
            "five_class": five_result,
        }

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(evaluate, case): case for case in cases}
        for completed, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(
                f"[{completed}/{len(cases)}] {record['case_id']} "
                f"four={record['four_class']['emotion']} "
                f"five={record['five_class']['emotion']}",
                flush=True,
            )

    records.sort(key=lambda row: row["case_id"])
    with result_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = _build_summary(records, settings.model)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"RESULT_PATH={result_path}")
    print(f"SUMMARY_PATH={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
