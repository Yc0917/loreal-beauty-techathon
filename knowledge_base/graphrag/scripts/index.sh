#!/usr/bin/env bash
set -euo pipefail

# 始终使用 GraphRAG 独立虚拟环境和独立工作目录。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRAPHRAG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${GRAPHRAG_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "未找到 GraphRAG 独立虚拟环境：${PYTHON_BIN}"
  exit 1
fi

if [[ ! -f "${GRAPHRAG_ROOT}/.env" ]]; then
  echo "缺少 ${GRAPHRAG_ROOT}/.env，请根据 .env.example 配置模型。"
  exit 1
fi

# 读取本工作区的模型配置，不读取客服 agent Demo 的任何环境文件。
set -a
source "${GRAPHRAG_ROOT}/.env"
set +a

required_vars=(
  GRAPHRAG_CHAT_API_KEY
  GRAPHRAG_CHAT_API_BASE
  GRAPHRAG_CHAT_MODEL
  GRAPHRAG_EMBEDDING_API_KEY
  GRAPHRAG_EMBEDDING_API_BASE
  GRAPHRAG_EMBEDDING_MODEL
)

for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "环境变量 ${name} 不能为空。"
    exit 1
  fi
done

# Token/Coding Plan 仅限交互式工具，不能用于后端批量索引。
if [[ "${GRAPHRAG_CHAT_API_BASE}" == *"token-plan"* || "${GRAPHRAG_CHAT_API_BASE}" == *"coding.dashscope"* ]]; then
  echo "聊天模型地址属于 Token/Coding Plan，不能用于 GraphRAG 后端索引。"
  exit 1
fi

if [[ "${GRAPHRAG_EMBEDDING_API_BASE}" == *"token-plan"* || "${GRAPHRAG_EMBEDDING_API_BASE}" == *"coding.dashscope"* ]]; then
  echo "向量模型地址属于 Token/Coding Plan，不能用于 GraphRAG 后端索引。"
  exit 1
fi

document_count="$(find "${GRAPHRAG_ROOT}/input" -maxdepth 1 -type f -name '*.md' | wc -l | tr -d ' ')"
if [[ "${document_count}" != "7" ]]; then
  echo "预期 7 份 Markdown，实际找到 ${document_count} 份。"
  exit 1
fi

"${GRAPHRAG_ROOT}/.venv/bin/graphrag" index --root "${GRAPHRAG_ROOT}"
