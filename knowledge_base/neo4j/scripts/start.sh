#!/usr/bin/env bash
set -euo pipefail

# 所有运行时均固定在欧莱雅项目目录，避免使用或修改外部 Demo。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NEO4J_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Neo4j 5.26 启动器会错误转义中文路径，因此使用临时英文别名；实际文件仍在项目目录。
if [[ ! -e /tmp/loreal_neo4j_workspace ]]; then
  ln -s "${PROJECT_NEO4J_ROOT}" /tmp/loreal_neo4j_workspace
elif [[ "$(readlink /tmp/loreal_neo4j_workspace)" != "${PROJECT_NEO4J_ROOT}" ]]; then
  echo "/tmp/loreal_neo4j_workspace 已指向其他目录，停止启动。"
  exit 1
fi

NEO4J_ROOT=/tmp/loreal_neo4j_workspace
export NEO4J_HOME="${NEO4J_ROOT}/runtime/neo4j"
export NEO4J_CONF="${NEO4J_ROOT}/conf"
export JAVA_HOME="${NEO4J_ROOT}/runtime/java/Contents/Home"

if [[ ! -x "${NEO4J_HOME}/bin/neo4j" ]]; then
  echo "未找到 Neo4j 运行时：${NEO4J_HOME}"
  exit 1
fi

if [[ ! -x "${JAVA_HOME}/bin/java" ]]; then
  echo "未找到 Java 运行时：${JAVA_HOME}"
  exit 1
fi

"${NEO4J_HOME}/bin/neo4j" start
