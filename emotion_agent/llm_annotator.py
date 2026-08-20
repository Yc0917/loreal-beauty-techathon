"""逐买家轮次调用 LLM 并生成可断点续跑的标注结果。"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from emotion_agent.annotation_prompt import (
    ANNOTATION_PROMPT_VERSION,
    ANNOTATION_SYSTEM_PROMPT,
    build_annotation_prompt,
)
from emotion_agent.annotation_schemas import (
    AnnotationCase,
    AnnotationRecord,
    LLMAnnotation,
)
from emotion_agent.providers import create_openai_compatible_structured_model
from emotion_agent.settings import LLMSettings


class StructuredAnnotationResult(TypedDict):
    """include_raw=True 时 LangChain 返回的结构。"""

    raw: BaseMessage
    parsed: LLMAnnotation | dict[str, Any] | None
    parsing_error: Exception | None


class StructuredAnnotationModel(Protocol):
    """标注模型的最小调用协议。"""

    def invoke(self, input: Sequence[BaseMessage]) -> StructuredAnnotationResult:
        """返回原始消息、解析结果和校验错误。"""


class StructuredOutputRetryError(ValueError):
    """结构化输出在定向重试后仍未通过校验。"""

    def __init__(self, attempts: int, errors: list[str]) -> None:
        self.attempts = attempts
        self.errors = errors
        latest_error = errors[-1] if errors else "未返回可解析结果"
        super().__init__(
            f"结构化输出连续失败 {attempts} 次：{latest_error}"
        )


def create_annotation_model() -> tuple[StructuredAnnotationModel, str, int]:
    """使用可选的 ANNOTATION_MODEL 创建独立标注模型。"""
    settings = LLMSettings.from_env()
    annotation_model = os.getenv("ANNOTATION_MODEL", "").strip() or settings.model
    resolved = replace(settings, model=annotation_model)
    model = create_openai_compatible_structured_model(
        LLMAnnotation,
        resolved,
        include_raw=True,
    )
    return (
        model,  # type: ignore[return-value]
        annotation_model,
        resolved.schema_max_retries,
    )


def _coerce_annotation(value: LLMAnnotation | dict[str, Any]) -> LLMAnnotation:
    """统一校验兼容模型返回结果。"""
    if isinstance(value, LLMAnnotation):
        return value
    return LLMAnnotation.model_validate(value)


def _raw_message_text(raw_message: BaseMessage | None) -> str:
    """将 LangChain 原始消息内容转换为可反馈给模型的文本。"""
    if raw_message is None:
        return ""
    content = raw_message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def build_repair_prompt(raw_output: str, parsing_error: Exception | None) -> str:
    """根据上一次原始输出和校验错误生成定向修复指令。"""
    error_text = str(parsing_error or "模型未返回可解析的结构")
    schema_text = json.dumps(
        LLMAnnotation.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
上一次输出没有通过 JSON Schema 校验。请根据错误重新输出完整 JSON，不要输出 Markdown 或其他文字。

<invalid_output>
{raw_output}
</invalid_output>

<validation_error>
{error_text}
</validation_error>

<required_schema>
{schema_text}
</required_schema>

修复要求：
1. 只能包含 emotion、confidence、trend、reason 四个字段。
2. emotion 只能是 neutral、anxious、dissatisfied、angry。
3. trend 只能是 improving、stable、worsening、unknown，禁止将 improving 写成 improing。
4. reason 内部引用原话时使用中文引号“”，不要使用未转义的英文双引号。
5. 保留上一次输出中合理的语义，只修正字段、类型、枚举、拼写和 JSON 格式问题。
""".strip()


def invoke_with_schema_retry(
    model: StructuredAnnotationModel,
    prompt_messages: Sequence[BaseMessage],
    max_schema_retries: int,
) -> tuple[LLMAnnotation, int, list[str]]:
    """校验失败时向模型反馈原始输出与错误，最多定向重试指定次数。"""
    messages = list(prompt_messages)
    errors: list[str] = []

    for attempt_index in range(max_schema_retries + 1):
        result = model.invoke(messages)
        parsed = result.get("parsed")
        parsing_error = result.get("parsing_error")
        raw_message = result.get("raw")

        if parsed is not None:
            return _coerce_annotation(parsed), attempt_index + 1, errors

        error_text = str(parsing_error or "模型未返回可解析的结构")
        errors.append(error_text)
        attempts = attempt_index + 1
        if attempt_index >= max_schema_retries:
            raise StructuredOutputRetryError(attempts, errors)

        raw_output = _raw_message_text(raw_message)
        messages.extend(
            [
                AIMessage(content=raw_output),
                HumanMessage(
                    content=build_repair_prompt(raw_output, parsing_error)
                ),
            ]
        )

    raise RuntimeError("结构化定向重试流程异常结束")


def annotate_case(
    case: AnnotationCase,
    model: StructuredAnnotationModel,
    model_name: str,
    max_schema_retries: int,
) -> AnnotationRecord:
    """标注单个买家轮次样本。"""
    prompt_messages = [
        SystemMessage(content=ANNOTATION_SYSTEM_PROMPT),
        HumanMessage(content=build_annotation_prompt(case.messages)),
    ]
    annotation, attempts, repair_errors = invoke_with_schema_retry(
        model,
        prompt_messages,
        max_schema_retries,
    )
    return AnnotationRecord(
        **case.model_dump(),
        llm_annotation=annotation,
        annotation_model=model_name,
        annotation_prompt_version=ANNOTATION_PROMPT_VERSION,
        annotation_status="completed",
        annotation_attempts=attempts,
        schema_repaired=attempts > 1,
        repair_errors=repair_errors,
    )


def _load_existing_records(path: Path) -> dict[str, AnnotationRecord]:
    """读取已有结果，保留每个 case_id 最后一次记录。"""
    if not path.exists():
        return {}
    records: dict[str, AnnotationRecord] = {}
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                record = AnnotationRecord.model_validate_json(line)
                records[record.case_id] = record
    return records


def _write_records_atomically(
    records: dict[str, AnnotationRecord],
    ordered_case_ids: list[str],
    output_path: Path,
) -> None:
    """原子写入当前进度，避免中断造成结果文件损坏。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        for case_id in ordered_case_ids:
            record = records.get(case_id)
            if record is None:
                continue
            payload = record.model_dump(exclude_none=True)
            file.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    temp_path.replace(output_path)


def annotate_cases(
    cases: list[AnnotationCase],
    output_path: str | Path,
    limit: int | None = None,
) -> dict[str, int]:
    """顺序标注全部样本，成功样本自动跳过并支持断点续跑。"""
    path = Path(output_path)
    records = _load_existing_records(path)
    ordered_case_ids = [case.case_id for case in cases]
    model, model_name, max_schema_retries = create_annotation_model()
    attempted = 0

    for index, case in enumerate(cases, start=1):
        existing = records.get(case.case_id)
        if existing and existing.annotation_status == "completed":
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            records[case.case_id] = annotate_case(
                case,
                model,
                model_name,
                max_schema_retries,
            )
        except StructuredOutputRetryError as exc:
            records[case.case_id] = AnnotationRecord(
                **case.model_dump(),
                annotation_model=model_name,
                annotation_prompt_version=ANNOTATION_PROMPT_VERSION,
                annotation_status="failed",
                annotation_attempts=exc.attempts,
                repair_errors=exc.errors,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:  # 网络或接口错误不反馈给模型，只记录并继续批处理。
            records[case.case_id] = AnnotationRecord(
                **case.model_dump(),
                annotation_model=model_name,
                annotation_prompt_version=ANNOTATION_PROMPT_VERSION,
                annotation_status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        _write_records_atomically(records, ordered_case_ids, path)
        print(f"[{index}/{len(cases)}] {case.case_id}: {records[case.case_id].annotation_status}")

    completed = sum(
        record.annotation_status == "completed" for record in records.values()
    )
    failed = sum(record.annotation_status == "failed" for record in records.values())
    return {
        "total_cases": len(cases),
        "attempted_this_run": attempted,
        "completed": completed,
        "failed": failed,
        "remaining": len(cases) - completed,
    }
