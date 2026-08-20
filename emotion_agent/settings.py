"""OpenAI 兼容模型的环境变量配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv


StructuredOutputMethod = Literal["function_calling", "json_schema", "json_mode"]


def _read_positive_float(name: str, default: float) -> float:
    """读取正浮点数配置，并在配置错误时给出明确提示。"""
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _read_non_negative_int(name: str, default: int) -> int:
    """读取非负整数配置。"""
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if value < 0:
        raise ValueError(f"{name} 不能小于 0")
    return value


def _read_optional_bool(name: str) -> bool | None:
    """读取可选布尔配置，未设置时不向其他 OpenAI 兼容服务传参。"""
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return None
    if raw_value in {"true", "1", "yes", "on"}:
        return True
    if raw_value in {"false", "0", "no", "off"}:
        return False
    raise ValueError(
        f"{name} 只支持 true/false、1/0、yes/no 或 on/off"
    )


@dataclass(frozen=True)
class LLMSettings:
    """创建 OpenAI 兼容模型所需的最小配置。"""

    api_key: str
    model: str
    base_url: str | None
    structured_method: StructuredOutputMethod
    enable_thinking: bool | None
    timeout_seconds: float
    max_retries: int
    schema_max_retries: int

    @classmethod
    def from_env(cls) -> "LLMSettings":
        """从项目根目录的 .env 或系统环境变量加载配置。"""
        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(dotenv_path=env_path)

        api_key = os.getenv("LLM_API_KEY", "").strip()
        if not api_key:
            raise ValueError("缺少 LLM_API_KEY，请在项目根目录的 .env 中配置")

        model = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
        if not model:
            raise ValueError("LLM_MODEL 不能为空")

        base_url = os.getenv("LLM_BASE_URL", "").strip() or None
        structured_method = os.getenv(
            "LLM_STRUCTURED_METHOD",
            "json_mode",
        ).strip()
        allowed_methods = {"function_calling", "json_schema", "json_mode"}
        if structured_method not in allowed_methods:
            raise ValueError(
                "LLM_STRUCTURED_METHOD 只支持 function_calling、json_schema 或 json_mode"
            )

        return cls(
            api_key=api_key,
            model=model,
            base_url=base_url,
            structured_method=cast(StructuredOutputMethod, structured_method),
            enable_thinking=_read_optional_bool("LLM_ENABLE_THINKING"),
            timeout_seconds=_read_positive_float("LLM_TIMEOUT_SECONDS", 45),
            max_retries=_read_non_negative_int("LLM_MAX_RETRIES", 2),
            schema_max_retries=_read_non_negative_int(
                "LLM_SCHEMA_MAX_RETRIES",
                3,
            ),
        )
