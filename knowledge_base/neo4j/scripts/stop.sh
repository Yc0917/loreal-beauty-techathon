#!/usr/bin/env bash
set -euo pipefail

# 使用项目内运行时停止本地 Neo4j。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NEO4J_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ ! -L /tmp/loreal_neo4j_workspace || "$(readlink /tmp/loreal_neo4j_workspace)" != "${PROJECT_NEO4J_ROOT}" ]]; then
  echo "未找到当前项目的 Neo4j 英文路径别名。"
  exit 1
fi
NEO4J_ROOT=/tmp/loreal_neo4j_workspace
export NEO4J_HOME="${NEO4J_ROOT}/runtime/neo4j"
export NEO4J_CONF="${NEO4J_ROOT}/conf"
export JAVA_HOME="${NEO4J_ROOT}/runtime/java/Contents/Home"

"${NEO4J_HOME}/bin/neo4j" stop
