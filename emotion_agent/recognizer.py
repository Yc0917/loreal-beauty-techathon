"""与具体模型供应商解耦的情绪识别核心。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from emotion_agent.prompts import (
    EMOTION_ANALYSIS_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_conversation_prompt,
)
from emotion_agent.schemas import (
    ConversationMessage,
    EmotionModelOutput,
    EmotionResult,
    EmotionTokenLogprob,
)


MAX_CONTEXT_MESSAGES = 12
EMOTION_VALUE_PATTERN = re.compile(
    rb'"emotion"\s*:\s*"(?P<emotion>neutral|positive|anxious|dissatisfied|angry)"'
)


class StructuredEmotionModel(Protocol):
    """外部结构化模型需要满足的最小协议。"""

    def invoke(
        self,
        input: Sequence[BaseMessage],
    ) -> object:
        """接收消息列表并返回符合 EmotionModelOutput 的结果。"""


class StructuredEmotionResult(TypedDict):
    """include_raw=True 时 LangChain 返回的结构。"""

    raw: BaseMessage
    parsed: EmotionModelOutput | dict[str, Any] | None
    parsing_error: Exception | None


class EmotionRecognitionRetryError(ValueError):
    """情绪识别在达到最大定向重试次数后仍然失败。"""

    def __init__(self, attempts: int, errors: list[str]) -> None:
        self.attempts = attempts
        self.errors = errors
        latest_error = errors[-1] if errors else "未返回可解析结果"
        super().__init__(f"情绪识别连续失败 {attempts} 次：{latest_error}")


def _validate_messages(
    messages: Sequence[ConversationMessage | dict[str, str]],
) -> list[ConversationMessage]:
    """校验会话并截取最近消息，控制上下文长度。"""
    validated = [
        message
        if isinstance(message, ConversationMessage)
        else ConversationMessage.model_validate(message)
        for message in messages
    ]
    if not validated:
        raise ValueError("至少需要一条对话消息")
    if not any(message.role == "买家" for message in validated):
        raise ValueError("对话中至少需要一条买家消息")
    return validated[-MAX_CONTEXT_MESSAGES:]


def _validate_model_output(
    value: EmotionModelOutput | dict[str, Any],
) -> EmotionModelOutput:
    """将不同模型适配器的返回值统一为 Pydantic 对象。"""
    if isinstance(value, EmotionModelOutput):
        return value
    return EmotionModelOutput.model_validate(value)


def _check_evidence(
    output: EmotionModelOutput,
    messages: list[ConversationMessage],
) -> None:
    """保证每条证据都能逐字回溯到买家原话。"""
    buyer_texts = [message.text for message in messages if message.role == "买家"]
    unsupported = [
        evidence
        for evidence in output.evidence
        if not any(evidence in buyer_text for buyer_text in buyer_texts)
    ]
    if unsupported:
        raise ValueError(f"模型返回了无法回溯到买家原话的证据：{unsupported}")


def _raw_message_text(raw_message: BaseMessage | None) -> str:
    """将模型原始消息转换为可反馈的文本。"""
    if raw_message is None:
        return ""
    if isinstance(raw_message.content, str):
        return raw_message.content
    return json.dumps(raw_message.content, ensure_ascii=False)


def _extract_emotion_token_logprobs(
    raw_message: BaseMessage | None,
    emotion: str,
) -> list[EmotionTokenLogprob]:
    """从原始模型响应中精确取出情绪标签所覆盖的 Token。"""
    if raw_message is None:
        raise ValueError("模型未返回原始响应，无法计算 Token 置信度")
    if not isinstance(raw_message.content, str):
        raise ValueError("模型原始输出不是文本，无法定位情绪标签")

    logprobs = raw_message.response_metadata.get("logprobs")
    if not isinstance(logprobs, dict):
        raise ValueError("模型响应未包含 logprobs")
    token_items = logprobs.get("content")
    if not isinstance(token_items, list) or not token_items:
        raise ValueError("模型响应未包含可用的 Token logprobs")

    raw_bytes = raw_message.content.encode("utf-8")
    match = EMOTION_VALUE_PATTERN.search(raw_bytes)
    if match is None:
        raise ValueError("无法在模型原始 JSON 中定位 emotion 字段")
    label_start, label_end = match.span("emotion")
    if raw_bytes[label_start:label_end].decode("utf-8") != emotion:
        raise ValueError("原始输出的情绪标签与解析结果不一致")

    selected: list[EmotionTokenLogprob] = []
    rebuilt = bytearray()
    cursor = 0
    for item in token_items:
        if not isinstance(item, dict):
            raise ValueError("Token logprobs 的返回格式不正确")
        token_bytes_value = item.get("bytes")
        token = item.get("token")
        logprob = item.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)):
            raise ValueError("Token logprobs 缺少 token 或 logprob")
        if isinstance(token_bytes_value, list) and all(
            isinstance(value, int) and 0 <= value <= 255
            for value in token_bytes_value
        ):
            token_bytes = bytes(token_bytes_value)
        else:
            token_bytes = token.encode("utf-8")

        token_start = cursor
        token_end = cursor + len(token_bytes)
        rebuilt.extend(token_bytes)
        cursor = token_end
        if token_end <= label_start or token_start >= label_end:
            continue
        # Token 若同时包含引号等 JSON 语法，就无法把其概率当作纯标签概率。
        if token_start < label_start or token_end > label_end:
            raise ValueError("情绪标签与 JSON 语法共用 Token，无法精确计算置信度")
        selected.append(
            EmotionTokenLogprob(
                token=token,
                logprob=float(logprob),
                probability=math.exp(float(logprob)),
            )
        )

    # 某些服务会把标签之后的生僻中文字节替换为 U+FFFD。
    # 只校验 emotion 字段之前和标签本身，避免后续文本影响标签概率。
    if bytes(rebuilt[:label_end]) != raw_bytes[:label_end]:
        raise ValueError("emotion 字段前的 Token bytes 与原始输出不一致")
    if not selected:
        raise ValueError("未找到情绪标签对应的 Token logprobs")
    selected_bytes = "".join(item.token for item in selected).encode("utf-8")
    if selected_bytes != raw_bytes[label_start:label_end]:
        raise ValueError("情绪标签 Token 未能完整覆盖标签文本")
    return selected


def _calculate_token_confidence(tokens: list[EmotionTokenLogprob]) -> float:
    """将情绪标签的各 Token 对数概率相加，再还原为联合概率。"""
    return min(1.0, max(0.0, math.exp(sum(item.logprob for item in tokens))))


def _unpack_model_result(
    result: object,
) -> tuple[EmotionModelOutput | dict[str, Any] | None, BaseMessage | None, Exception | None]:
    """兼容直接结构化结果和 include_raw=True 的 LangChain 结果。"""
    if isinstance(result, EmotionModelOutput):
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


def build_repair_prompt(raw_output: str, error: Exception) -> str:
    """把上一次输出和具体错误反馈给模型，要求只修复结构化结果。"""
    schema_text = json.dumps(
        EmotionModelOutput.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
上一次情绪识别输出未通过校验。请根据错误重新输出完整 JSON，不要输出 Markdown 或其他文字。

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
1. emotion 只能是 neutral、anxious、dissatisfied、angry，禁止输出 positive。
2. trend 只能是 improving、stable、worsening、unknown。
3. evidence 必须是买家原话中真实存在的连续短语。
4. 只修复 JSON 格式、字段、枚举、类型或证据问题，不生成客服建议。
""".strip()


class EmotionRecognizer:
    """只负责组装 Prompt、调用模型和校验识别结果。"""

    def __init__(
        self,
        model: StructuredEmotionModel,
        max_schema_retries: int = 0,
    ):
        self.model = model
        self.max_schema_retries = max_schema_retries

    def recognize(
        self,
        messages: Sequence[ConversationMessage | dict[str, str]],
        scene: str = "未知",
    ) -> EmotionResult:
        """识别最新时点买家的主导情绪。"""
        context_messages = _validate_messages(messages)
        prompt_messages: list[BaseMessage] = [
            SystemMessage(content=EMOTION_ANALYSIS_SYSTEM_PROMPT),
            HumanMessage(
                content=build_conversation_prompt(
                    scene.strip() or "未知",
                    context_messages,
                )
            ),
        ]
        errors: list[str] = []

        for attempt_index in range(self.max_schema_retries + 1):
            raw_message: BaseMessage | None = None
            try:
                result = self.model.invoke(prompt_messages)
                parsed, raw_message, parsing_error = _unpack_model_result(result)
                if parsed is None:
                    raise parsing_error or ValueError("模型未返回可解析的结构")
                output = _validate_model_output(parsed)
                _check_evidence(output, context_messages)
                emotion_tokens = _extract_emotion_token_logprobs(
                    raw_message,
                    output.emotion,
                )
                return EmotionResult(
                    **output.model_dump(),
                    confidence=_calculate_token_confidence(emotion_tokens),
                    confidence_source="token_logprob",
                    emotion_token_logprobs=emotion_tokens,
                    prompt_version=PROMPT_VERSION,
                    attempts=attempt_index + 1,
                )
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
                attempts = attempt_index + 1
                if attempt_index >= self.max_schema_retries:
                    raise EmotionRecognitionRetryError(attempts, errors) from error

                # 有原始输出时进行定向纠错；网络错误等无输出异常则重复原请求。
                raw_output = _raw_message_text(raw_message)
                if raw_output:
                    prompt_messages.extend(
                        [
                            AIMessage(content=raw_output),
                            HumanMessage(content=build_repair_prompt(raw_output, error)),
                        ]
                    )

        raise RuntimeError("情绪识别重试流程异常结束")
