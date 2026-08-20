"""OpenAI 兼容 Chat Completions 模型适配器。"""

from __future__ import annotations

from typing import TypeVar, cast

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from emotion_agent.recognizer import StructuredEmotionModel
from emotion_agent.schemas import EmotionModelOutput
from emotion_agent.settings import LLMSettings


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


def create_openai_compatible_structured_model(
    output_schema: type[StructuredOutput],
    settings: LLMSettings | None = None,
    *,
    include_raw: bool = False,
    include_logprobs: bool = False,
) -> StructuredEmotionModel:
    """为指定 Pydantic Schema 创建 OpenAI 兼容结构化模型。"""
    resolved = settings or LLMSettings.from_env()
    # enable_thinking 是百炼的非 OpenAI 标准参数，仅在明确配置时传入。
    extra_body = (
        {"enable_thinking": resolved.enable_thinking}
        if resolved.enable_thinking is not None
        else None
    )
    # 只有情绪识别分支需要 Token 概率，避免改变意图识别和标注任务的请求。
    logprob_options = (
        {"logprobs": True, "top_logprobs": 5}
        if include_logprobs
        else {}
    )
    llm = ChatOpenAI(
        api_key=resolved.api_key,
        model=resolved.model,
        base_url=resolved.base_url,
        temperature=0,
        timeout=resolved.timeout_seconds,
        max_retries=resolved.max_retries,
        extra_body=extra_body,
        **logprob_options,
    )

    structured_model = llm.with_structured_output(
        output_schema,
        method=resolved.structured_method,
        include_raw=include_raw,
    )
    return cast(StructuredEmotionModel, structured_model)


def create_openai_compatible_model(
    settings: LLMSettings | None = None,
) -> StructuredEmotionModel:
    """根据环境变量创建具备结构化输出能力的模型。"""
    return create_openai_compatible_structured_model(
        EmotionModelOutput,
        settings,
        include_raw=True,
        include_logprobs=True,
    )
