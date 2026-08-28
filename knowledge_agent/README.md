# 欧莱雅 Knowledge Agent

Knowledge Agent 使用 LangGraph 实现带安全门控的 Tool-Use Loop，从 Neo4j 商品图谱和 Microsoft GraphRAG 索引收集证据，再生成可审核客服回复草稿。它不会向千牛发送消息。

公开仓库只提交Agent源码、配置模板、Prompt和构建脚本。Mock商品数据、GraphRAG输入、数据库、索引、评测数据和评测报告保留在本地并由`.gitignore`排除。

## 工作流

```text
START
  ├─ 情绪识别
  ├─ 安全风险判断
  └─ 商品识别
       ↓
Tool-Use Agent
  ↔ ToolNode 并行执行 Neo4j / GraphRAG / 动态数据检查
       ↓
证据聚合 → 回复生成 → 硬规则 + LLM 语义检查 → 回复策略
```

同一轮中模型返回的多个互不依赖 Tool Call 由 `ToolNode` 并行执行。后续查询如果依赖上一轮证据，则进入下一次 Tool-Use Loop。

当前一条完整回复通常会经历安全分类、工具规划、证据检索、回复生成和Evidence Guard语义审核。情绪识别、安全判断和商品识别从起点并行执行，但工具规划、回复生成和语义审核之间仍存在串行依赖。商品指代不明确、工具证据不足或Guard不通过时，还可能增加模型调用轮次。

## 意图理解与检索路由

当前没有独立的固定类别“业务意图分类器”，也不会先把问题强制归入某一个FAQ意图。Tool-Use Agent直接结合用户问题、商品上下文和安全决策选择检索工具。

Neo4j查询类型固定为7种：`auto`、`product_overview`、`skus`、`ingredients`、`faq`、`comparison`、`text2cypher`。动态数据检查固定为7种：`price`、`stock`、`promotion`、`gift`、`logistics`、`order`、`after_sales`。安全判断固定输出`low`、`medium`、`high`三级风险；安全类别说明由模型按问题生成，不是固定枚举。

当前Neo4j FAQ数据中的`intent`共有10类：容量、成分搭配、孕期、价格、商品对比、产品功效、适用肤质、库存、质地和使用方法。这些标签用于FAQ过滤和数据管理，不代表线上流程存在一个10分类意图模型。

## 商品识别与指代消解

当前知识 Prompt 版本为 `knowledge-v1.1.0`。商品卡片携带的商品 ID 优先级最高；最新买家问题包含明确商品名时使用别名规则，不调用 LLM。只有当前问题省略主体，或使用“它、这款、前者、后者”等表达，且历史对话存在候选商品时，才调用结构化商品指代模型。

LLM 只能从历史对话提取出的候选商品 ID 中选择，输出候选范围外的 ID 会被代码拒绝。多个历史商品遇到没有位置线索的模糊单数指代时直接追问，不允许模型高置信度猜测。识别结果、解析方式、指代文本、置信度和失败原因均写入 `product_context`。

## 只读图查询

Neo4j 工具按以下顺序执行：

1. 高频问题使用预定义参数化 Cypher。
2. 长尾问题才允许受控 Text2Cypher。
3. 所有查询在执行前经过确定性只读 Hook。
4. Hook 拒绝写子句、多语句、`CALL`、未知 Schema 和过大 `LIMIT`。
5. 查询先用 `EXPLAIN` 验证，再通过 Neo4j 只读会话执行。

Prompt 中的“只读”仅是引导，不是安全边界。

### 生产环境要求（当前开发环境不执行）

生产环境必须开启 Neo4j 身份认证，为 Agent 使用独立只读账号，只授予查询所需的读取权限，不得使用管理员账号，并必须设置查询超时和返回数量限制。数据库权限是 Prompt、应用层 Hook 和只读事务之后的最终兜底。

当前本地 Neo4j 仅监听 `127.0.0.1` 且关闭认证，只能用于开发演示，不得直接部署到生产环境。

## Evidence Guard

Evidence Guard 由两层组成：

- 脚本硬规则：检查证据ID、动态数据时效、高风险转人工、绝对承诺和医学表述。
- LLM语义检查：判断回复是否受Evidence支持，是否扩大了成分功效或遗漏限定条件。

首次检查不通过时最多重新生成一次。仍不通过时设为 `escalate`，LLM不能覆盖脚本硬规则。

## 回复策略

`generate_reply`只返回回复内容和策略，不会向千牛或其他外部渠道发送消息。当前策略字段有三种：

- `auto_send`：低风险、存在证据且未命中其他限制时由当前代码返回。它只是策略标记，当前项目仍不会实际发送。
- `draft`：中风险、动态数据不可用、商品对比、宽范围GraphRAG或存在未支持结论时返回草稿。
- `escalate`：高风险、商品歧义、缺少证据或Evidence Guard未通过时转人工处理。

当前业务目标是“Agent只生成客服草稿，不自动发送”。因此Demo回归集把普通问题的预期行为也设置为`draft`。现有代码仍可能返回`auto_send`，这是当前实现与目标策略之间已知的差异，不能把`auto_send`直接接到千牛发送接口。

## Langfuse

配置 `LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY` 后，每次 Graph 运行都会记录为 `knowledge-agent-reply` Trace。未配置时 Agent 正常运行，只是返回 `tracing_enabled=false`。

Trace 包含模型调用、工具调用、耗时、错误和 Prompt 版本。会话ID以哈希值上传，手机号、邮箱和长订单号在 OpenTelemetry Span 导出前脱敏。

批量评测是否上传Trace取决于运行时是否提供Langfuse密钥及采样设置。2026-08-28的50条Demo回复测试明确关闭了Langfuse上传，因此该批次不能从Langfuse取得逐条精确耗时。

## 当前性能与优化方向

2026-08-28的50条Demo测试以并发3运行，总耗时约6分钟，并且每条测试在Agent生成后还额外调用了一次回复内容审核模型。按执行槽粗略折算约为22秒/条；由于当时没有记录逐条开始和结束时间，当前只能估算实际客服请求通常约15～25秒，不能将该数字视为精确P50或P95。

不同路径的粗略耗时范围如下：

- 仅查询Neo4j的简单问题：约8～15秒。
- Neo4j与GraphRAG并行的解释性问题：约15～25秒。
- 多轮工具调用、Guard重试或异常兜底：约25～60秒。

对于千牛客服插件，该速度偏慢。期望目标是普通商品问题3～6秒、Neo4j与GraphRAG复杂问题6～10秒、高风险或异常问题不超过15秒。后续优化顺序建议如下，但这些优化尚未实现：

1. 明确商品名称或商品卡片时继续跳过LLM指代消解。
2. 容量、规格、质地、已知成分等高频简单问题由规则直接路由到Neo4j预定义查询，跳过Tool Agent规划。
3. GraphRAG只用于解释、对比、使用场景和复杂成分搭配，不在简单结构化问题中调用。
4. 低风险问题优先使用脚本Evidence Guard，仅在高风险、无证据或规则命中时调用LLM语义审核。
5. 评估将情绪识别与安全分类合并为一次模型调用；无法合并时继续保持并行。
6. 缓存商品概览、成分、使用方法和常见FAQ等稳定检索结果。
7. 在Agent入口和每个模型、Neo4j、GraphRAG、Guard节点记录耗时，通过Langfuse统计P50、P95和超时比例。

计划中的快速路径为：`规则路由 → Neo4j/FAQ → 小模型生成草稿`；复杂路径保留为：`安全识别 → Neo4j + GraphRAG并行 → 回复生成 → Evidence Guard`。

## API

```http
POST /api/knowledge/reply-draft
```

返回回复草稿、回复策略、情绪、安全决策、商品上下文、Evidence、Tool Call记录和Langfuse Trace URL。

首轮实现验收结果见 `reports/knowledge_agent_smoke_knowledge-v1.0.0_qwen3.6-flash_mock-kb_20260824.md`。商品指代消解评测见 `reports/product_reference_eval_knowledge-v1.1.0_qwen3.6-flash_reference-cases_20260825.md`。

多轮知识客服泛化数据集位于 `evaluation/`，当前包含150条开发集、50条验证集和100条锁定测试集；Ground truth状态为待业务双人审核。

当前Demo回归优先使用`evaluation/demo-regression-v1.0.0.jsonl`的50条轻量样本：普通问题只要求生成`draft`，商品歧义要求`clarify`，真正高风险问题才要求`escalate`。

2026-08-28回复内容测试共50条：综合通过23条、不通过27条；单看回复语义内容通过48条。27条综合失败全部涉及回复策略不符合预期，其中20条普通问题返回`auto_send`、6条普通问题被升级为`escalate`、1条应升级的问题返回`auto_send`；另有1条安全等级错误和2条内容问题，检查项之间存在重叠。复核版报告见`reports/demo_response_eval_knowledge-v1.1.0_qwen3.6-flash_demo-regression-v1.0.0_20260828_reviewed.md`。
