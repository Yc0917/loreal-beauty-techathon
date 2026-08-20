"""与具体模型供应商解耦的用户意图识别核心。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from emotion_agent.schemas import ConversationMessage
from intent_agent.prompts import (
    INTENT_ANALYSIS_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_conversation_prompt,
)
from intent_agent.schemas import IntentModelOutput, IntentResult


MAX_CONTEXT_MESSAGES = 12


class StructuredIntentModel(Protocol):
    """外部结构化模型需要满足的最小调用协议。"""

    def invoke(self, input: Sequence[BaseMessage]) -> object:
        """接收消息列表并返回结构化识别结果。"""


class IntentRecognitionRetryError(ValueError):
    """用户意图识别达到最大定向重试次数后仍然失败。"""

    def __init__(self, attempts: int, errors: list[str]) -> None:
        self.attempts = attempts
        self.errors = errors
        latest_error = errors[-1] if errors else "未返回可解析结果"
        super().__init__(f"用户意图识别连续失败 {attempts} 次：{latest_error}")


def _validate_messages(
    messages: Sequence[ConversationMessage | dict[str, str]],
) -> list[ConversationMessage]:
    """校验并截取最近的对话消息，控制模型上下文长度。"""
    validated = [
        message
        if isinstance(message, ConversationMessage)
        else ConversationMessage.model_validate(message)
        for message in messages
    ]
    if not validated or not any(message.role == "买家" for message in validated):
        raise ValueError("对话中至少需要一条买家消息")
    return validated[-MAX_CONTEXT_MESSAGES:]


def _unpack_result(
    result: object,
) -> tuple[IntentModelOutput | dict[str, Any] | None, BaseMessage | None, Exception | None]:
    """兼容直接结果与 include_raw=True 的 LangChain 返回结构。"""
    if isinstance(result, IntentModelOutput):
        return result, None, None
    if isinstance(result, dict) and (
        "parsed" in result or "parsing_error" in result or "raw" in result
    ):
        raw = result.get("raw")
        return (
            result.get("parsed"),
            raw if isinstance(raw, BaseMessage) else None,
            result.get("parsing_error"),
        )
    if isinstance(result, dict):
        return result, None, None
    raise TypeError(f"模型返回了不支持的结果类型：{type(result).__name__}")


def _raw_text(raw: BaseMessage | None) -> str:
    """把模型原始消息转换为可反馈的文本。"""
    if raw is None:
        return ""
    if isinstance(raw.content, str):
        return raw.content
    return json.dumps(raw.content, ensure_ascii=False)


def _validate_output(
    value: IntentModelOutput | dict[str, Any],
    messages: list[ConversationMessage],
) -> IntentModelOutput:
    """校验 Schema、固定建单标记以及带角色的原文证据。"""
    output = (
        value
        if isinstance(value, IntentModelOutput)
        else IntentModelOutput.model_validate(value)
    )
    if output.need_ticket is not True:
        raise ValueError("点击触发的工单意图识别中 need_ticket 必须为 true")

    if not any(evidence.role == "买家" for evidence in output.evidence):
        raise ValueError("evidence 至少需要一条买家原话证据")

    unsupported = [
        evidence.model_dump()
        for evidence in output.evidence
        if not any(
            evidence.role == message.role and evidence.text in message.text
            for message in messages
        )
    ]
    if unsupported:
        raise ValueError(f"模型返回了无法按角色回溯到原话的证据：{unsupported}")
    return output


def build_repair_prompt(raw_output: str, error: Exception) -> str:
    """将具体错误反馈给模型，执行定向结构修复。"""
    schema_text = json.dumps(
        IntentModelOutput.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
上一次工单意图识别输出未通过校验。请根据错误重新输出完整 JSON，不要输出 Markdown 或其他文字。

<invalid_output>
{raw_output}
</invalid_output>

<validation_error>
{error}
</validation_error>

<required_schema>
{schema_text}
</required_schema>

修复要求：
1. intent 只能是规定的五个工单标签之一，不存在无需工单类别。
2. need_ticket 必须始终为 true。
3. evidence 每项必须包含 role 和 text，role 只能是买家或客服，text 必须能在该角色原话中逐字找到。
4. evidence 至少包含一条买家证据；使用客服流程信息时应同时引用客服原话。
5. 只修复 JSON、字段、枚举、类型或证据问题，不生成客服建议。
""".strip()


class IntentRecognizer:
    """负责组装 Prompt、调用模型和校验工单意图结果。"""

    def __init__(self, model: StructuredIntentModel, max_schema_retries: int = 0):
        self.model = model
        self.max_schema_retries = max_schema_retries

    def recognize(
        self,
        messages: Sequence[ConversationMessage | dict[str, str]],
    ) -> IntentResult:
        """识别客服点击后应创建的工单类型。"""
        context_messages = _validate_messages(messages)
        prompt_messages: list[BaseMessage] = [
            SystemMessage(content=INTENT_ANALYSIS_SYSTEM_PROMPT),
            HumanMessage(content=build_conversation_prompt(context_messages)),
        ]
        errors: list[str] = []

        for attempt_index in range(self.max_schema_retries + 1):
            raw_message: BaseMessage | None = None
            try:
                result = self.model.invoke(prompt_messages)
                parsed, raw_message, parsing_error = _unpack_result(result)
                if parsed is None:
                    raise parsing_error or ValueError("模型未返回可解析的结构")
                output = _validate_output(parsed, context_messages)
                return IntentResult(
                    **output.model_dump(),
                    prompt_version=PROMPT_VERSION,
                    attempts=attempt_index + 1,
                )
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
                attempts = attempt_index + 1
                if attempt_index >= self.max_schema_retries:
                    raise IntentRecognitionRetryError(attempts, errors) from error

                # 有原始输出时反馈具体错误；网络异常等无输出情况直接重试原请求。
                raw_output = _raw_text(raw_message)
                if raw_output:
                    prompt_messages.extend(
                        [
                            AIMessage(content=raw_output),
                            HumanMessage(content=build_repair_prompt(raw_output, error)),
                        ]
                    )

        raise RuntimeError("用户意图识别重试流程异常结束")
