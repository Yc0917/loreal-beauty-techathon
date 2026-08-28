# 欧莱雅商品知识库

本目录独立承载欧莱雅项目的两套知识检索基础设施，不依赖也不修改 `客服agent/` Demo。

## 目录

- `neo4j/`：商品、SKU、成分、肤质、功效和 FAQ 的结构化知识图谱。
- `graphrag/`：商品说明、版本对比、成分安全和 FAQ 的 Markdown GraphRAG 工作区。

两套数据通过统一的 `product_id` 关联。价格、库存、容量等确定性问题优先查询 Neo4j；商品差异、适用场景、成分关系和综合说明交给 GraphRAG；孕期、过敏、疾病和高风险成分叠加问题必须先执行安全路由。

运行时检索由项目根目录的 `knowledge_agent/` 负责。Agent使用 LangGraph Tool-Use Loop，在同一轮内并行执行互不依赖的 Neo4j、GraphRAG 和动态数据检查工具，并将工具结果转换为统一 Evidence。

## 公开仓库边界

当前商品、SKU、成分、FAQ和GraphRAG Markdown均为本地Mock开发数据，不提交公开Git仓库。公开仓库只保留图模型说明、配置模板、Prompt和构建脚本；以下目录及文件由`.gitignore`排除：

- `neo4j/import/`、`neo4j/data/`、`neo4j/runtime/`及图可视化导出文件。
- `graphrag/input/`、`graphrag/output/`、缓存、日志、虚拟环境和真实`.env`。
- Knowledge Agent评测数据、评测报告和其他由Mock数据生成的产物。

这些忽略规则只阻止Git上传，不会删除本地文件。
