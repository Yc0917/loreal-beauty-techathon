"""Knowledge Agent的环境组装工厂。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from emotion_agent.factory import create_emotion_agent_from_env
from knowledge_agent.agent import KnowledgeAgent
from knowledge_agent.graph import create_knowledge_graph
from knowledge_agent.settings import KnowledgeSettings
from knowledge_agent.tools import (
    create_dynamic_data_tool,
    create_graphrag_tool,
    create_neo4j_tool,
)


def _create_chat_model(settings: KnowledgeSettings) -> ChatOpenAI:
    """创建同时支持Tool Calling和结构化输出的模型。"""
    llm_settings = settings.llm
    extra_body = (
        {"enable_thinking": llm_settings.enable_thinking}
        if llm_settings.enable_thinking is not None
        else None
    )
    return ChatOpenAI(
        api_key=llm_settings.api_key,
        model=llm_settings.model,
        base_url=llm_settings.base_url,
        temperature=0,
        timeout=llm_settings.timeout_seconds,
        max_retries=llm_settings.max_retries,
        extra_body=extra_body,
    )


def create_knowledge_agent_from_env(
    settings: KnowledgeSettings | None = None,
) -> KnowledgeAgent:
    """从项目环境变量构建可复用的Knowledge Agent。"""
    resolved = settings or KnowledgeSettings.from_env()
    llm = _create_chat_model(resolved)
    emotion_agent = create_emotion_agent_from_env(resolved.llm)
    tools = [
        create_neo4j_tool(resolved, llm),
        create_graphrag_tool(resolved),
        create_dynamic_data_tool(),
    ]
    graph = create_knowledge_graph(
        settings=resolved,
        llm=llm,
        emotion_recognizer=emotion_agent.recognizer,
        tools=tools,
    )
    return KnowledgeAgent(graph=graph, settings=resolved)
