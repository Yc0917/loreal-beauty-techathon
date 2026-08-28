# 欧莱雅 GraphRAG

本目录是独立于 `客服agent/` Demo 的 Microsoft GraphRAG 2.1.0 工作区。

## 数据与输出

- `input/`：7 份 Markdown 商品知识文档。
- `prompts/`：GraphRAG 初始化生成的提示词。
- `prompt_versions/graphrag_v1_0_0/`：当前提示词的只读完整基线快照。
- `PROMPT_VERSION`：当前生效版本，现为 `graphrag-v1.0.0`。
- `settings.yaml`：Markdown 输入、模型、分块、LanceDB 和索引配置。
- `output/`：实体、关系、社区、社区报告、文本单元和 LanceDB 向量索引。
- `cache/`、`logs/`：索引缓存和日志。

官方 GraphRAG 2.1.0 将 Markdown 作为文本输入读取，使用 token 分块。Demo 中的 `strategy: markdown` 是其自定义扩展，本项目不修改也不复制 Demo 运行时代码。

当前 Prompt 是 GraphRAG 2.1.0 初始化生成的首个基线版本，尚未进行领域化改写。后续修改必须先存档当前版本、升级版本号并保留独立评测结果。

## 模型配置

复制 `.env.example` 为 `.env`，填写允许后端服务调用的聊天模型和向量模型凭证。当前根目录配置的是 Token Plan，按照服务使用范围不能用于 GraphRAG 后端批量索引，因此不会自动复用。

当前使用同一阿里云百炼业务空间的 OpenAI 兼容地址，聊天模型为 `qwen3.7-flash`，向量模型为 `qwen3.7-text-embedding`。向量维度保持为默认的 1024 维。

## 索引

配置凭证后执行：

```bash
knowledge_base/graphrag/scripts/index.sh
```

索引脚本会先检查 7 份 Markdown、必要环境变量和接口地址；Token Plan 或 Coding Plan 地址会被拒绝。
