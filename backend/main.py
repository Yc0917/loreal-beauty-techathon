"""数据共情客服工作台的本地 API。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analysis_agent import (
    ConversationAnalysisAgent,
    ConversationAnalysisResult,
    ConversationAnalysisRetryError,
    create_analysis_agent_from_env,
)
from backend.database import (
    add_simulation_message,
    get_conversation_detail,
    initialize_database,
    list_conversations,
    reset_simulation_messages,
)
from emotion_agent import (
    ConversationMessage,
    EmotionRecognitionRetryError,
    EmotionResult,
    create_emotion_agent_from_env,
)
from emotion_agent.graph import EmotionRecognitionAgent
from intent_agent import (
    IntentRecognitionAgent,
    IntentRecognitionRetryError,
    IntentResult,
    create_intent_agent_from_env,
)


logger = logging.getLogger(__name__)


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    role: str = Field(default="客服", pattern="^(客服|买家)$")


class EmotionAnalyzeRequest(BaseModel):
    """前端在每个买家轮次结束后提交的可见对话上下文。"""

    scene: str = Field(default="未知", max_length=100)
    messages: list[ConversationMessage] = Field(min_length=1, max_length=100)


class IntentAnalyzeRequest(BaseModel):
    """只提交当前时点可见的对话，不传入场景或既有工单标签。"""

    messages: list[ConversationMessage] = Field(min_length=1, max_length=100)


class ConversationAnalyzeRequest(BaseModel):
    """统一分析接口所需的当前可见对话。"""

    scene: str = Field(default="未知", max_length=100)
    messages: list[ConversationMessage] = Field(min_length=1, max_length=100)


@lru_cache(maxsize=1)
def get_emotion_agent() -> EmotionRecognitionAgent:
    """按进程复用模型与 LangGraph，避免每次请求重复初始化。"""
    return create_emotion_agent_from_env()


@lru_cache(maxsize=1)
def get_intent_agent() -> IntentRecognitionAgent:
    """按进程复用用户意图模型与 LangGraph。"""
    return create_intent_agent_from_env()


@lru_cache(maxsize=1)
def get_analysis_agent() -> ConversationAnalysisAgent:
    """按进程复用统一的并行会话分析 Graph。"""
    return create_analysis_agent_from_env()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="数据共情客服工作台 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/conversations")
def conversations(
    query: str = Query(default="", max_length=50),
    scene: str = Query(default="", max_length=50),
) -> dict[str, object]:
    items = list_conversations(query=query.strip(), scene=scene.strip())
    return {"items": items, "total": len(items)}


@app.get("/api/conversations/{session_id}")
def conversation_detail(session_id: str) -> dict[str, object]:
    detail = get_conversation_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@app.post("/api/emotion/analyze", response_model=EmotionResult)
def analyze_emotion(payload: EmotionAnalyzeRequest) -> EmotionResult:
    """调用四分类情绪 Agent；全部重试失败时返回统一错误。"""
    try:
        return get_emotion_agent().analyze(
            messages=payload.messages,
            scene=payload.scene.strip() or "未知",
        )
    except EmotionRecognitionRetryError as error:
        logger.error(
            "情绪识别失败：attempts=%s errors=%s",
            error.attempts,
            error.errors,
        )
        raise HTTPException(status_code=502, detail="识别失败") from error
    except Exception:
        logger.exception("情绪识别发生未预期异常")
        raise HTTPException(status_code=502, detail="识别失败")


@app.post("/api/intent/analyze", response_model=IntentResult)
def analyze_intent(payload: IntentAnalyzeRequest) -> IntentResult:
    """调用点击触发的五分类工单意图 Agent；失败时返回统一错误。"""
    try:
        return get_intent_agent().analyze(messages=payload.messages)
    except IntentRecognitionRetryError as error:
        logger.error(
            "用户意图识别失败：attempts=%s errors=%s",
            error.attempts,
            error.errors,
        )
        raise HTTPException(status_code=502, detail="识别失败") from error
    except Exception:
        logger.exception("用户意图识别发生未预期异常")
        raise HTTPException(status_code=502, detail="识别失败")


@app.post("/api/analysis/analyze", response_model=ConversationAnalysisResult)
def analyze_conversation(
    payload: ConversationAnalyzeRequest,
) -> ConversationAnalysisResult:
    """一次请求进入统一 Graph，并行执行情绪和用户意图识别。"""
    try:
        return get_analysis_agent().analyze(
            messages=payload.messages,
            scene=payload.scene.strip() or "未知",
        )
    except ConversationAnalysisRetryError as error:
        logger.error("统一会话分析全部失败：%s", error)
        raise HTTPException(status_code=502, detail="识别失败") from error
    except Exception:
        logger.exception("统一会话分析发生未预期异常")
        raise HTTPException(status_code=502, detail="识别失败")


@app.post("/api/conversations/{session_id}/messages", status_code=status.HTTP_201_CREATED)
def create_message(session_id: str, payload: MessageCreate) -> dict[str, object]:
    try:
        return add_simulation_message(session_id, payload.text.strip(), payload.role)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error


@app.delete("/api/simulation", status_code=status.HTTP_200_OK)
def reset_simulation() -> dict[str, int]:
    return {"deleted": reset_simulation_messages()}
