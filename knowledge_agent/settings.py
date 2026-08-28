"""Knowledge Agent的数据库、GraphRAG和运行配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from emotion_agent.settings import LLMSettings


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须大于0")
    return value


@dataclass(frozen=True)
class KnowledgeSettings:
    """组装Knowledge Agent所需的配置。"""

    llm: LLMSettings
    max_tool_iterations: int
    graphrag_timeout_seconds: int
    graphrag_root: Path
    graphrag_python: Path
    graphrag_query_script: Path
    neo4j_uri: str
    neo4j_database: str
    neo4j_username: str | None
    neo4j_password: str | None

    @classmethod
    def from_env(cls) -> "KnowledgeSettings":
        load_dotenv(PROJECT_ROOT / ".env")
        graphrag_root = PROJECT_ROOT / "knowledge_base" / "graphrag"
        return cls(
            llm=LLMSettings.from_env(),
            max_tool_iterations=_positive_int(
                "KNOWLEDGE_AGENT_MAX_TOOL_ITERATIONS", 3
            ),
            graphrag_timeout_seconds=_positive_int(
                "KNOWLEDGE_AGENT_GRAPHRAG_TIMEOUT_SECONDS", 180
            ),
            graphrag_root=graphrag_root,
            graphrag_python=graphrag_root / ".venv" / "bin" / "python",
            graphrag_query_script=graphrag_root / "scripts" / "query.py",
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687").strip(),
            neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j").strip(),
            neo4j_username=os.getenv("NEO4J_USERNAME", "").strip() or None,
            neo4j_password=os.getenv("NEO4J_PASSWORD", "").strip() or None,
        )
