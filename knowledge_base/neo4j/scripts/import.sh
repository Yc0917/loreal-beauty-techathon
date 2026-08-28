#!/usr/bin/env bash
set -euo pipefail

# 导入脚本采用 MERGE，可在更新 CSV 后重复执行。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NEO4J_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ ! -L /tmp/loreal_neo4j_workspace || "$(readlink /tmp/loreal_neo4j_workspace)" != "${PROJECT_NEO4J_ROOT}" ]]; then
  echo "未找到当前项目的 Neo4j 英文路径别名，请先执行 start.sh。"
  exit 1
fi
NEO4J_ROOT=/tmp/loreal_neo4j_workspace
export NEO4J_HOME="${NEO4J_ROOT}/runtime/neo4j"
export NEO4J_CONF="${NEO4J_ROOT}/conf"
export JAVA_HOME="${NEO4J_ROOT}/runtime/java/Contents/Home"

"${NEO4J_HOME}/bin/cypher-shell" -a neo4j://127.0.0.1:7687 --format plain -f "${SCRIPT_DIR}/import.cypher"
