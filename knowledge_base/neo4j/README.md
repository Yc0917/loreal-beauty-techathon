# Neo4j 商品知识图谱

## 图模型

- `Product`：商品版本。
- `SKU`：容量、价格、库存、优惠、赠品和条码。
- `Ingredient`：成分及风险说明。
- `FAQ`：问题、答案、意图、风险等级和预期路由。
- `Brand`、`Category`、`SkinType`、`Effect`、`SourcePage`：商品关联实体。

主要关系包括 `HAS_SKU`、`CONTAINS`、`HAS_FAQ`、`SUITABLE_FOR`、`HAS_EFFECT`、`BRANDED_BY`、`IN_CATEGORY` 和 `FROM_SOURCE`。

## 本地目录

- `runtime/neo4j/`：Neo4j Community 运行时。
- `runtime/java/`：项目内 Java 运行时。
- `data/`：Neo4j 数据库文件。
- `import/`：用于 `LOAD CSV` 的结构化数据。
- `conf/neo4j.conf`：仅监听本机的数据库配置。
- `scripts/import.cypher`：幂等图谱导入脚本。

由于 Neo4j 5.26 启动器不能正确处理当前项目路径中的中文字符，启动脚本会创建 `/tmp/loreal_neo4j_workspace` 英文软链接。该链接仅解决启动器路径兼容问题，运行时、配置、导入文件和数据库仍实际保存在欧莱雅项目的 `knowledge_base/neo4j/` 下。

## 当前数据规模

- 节点：61 个。
- 关系：82 条。
- 商品：4 个。
- SKU：12 个。
- 成分：14 个。
- FAQ：12 个。
- 商品成分关系：22 条。

数据库仅绑定 `127.0.0.1`，开发环境关闭身份认证。该配置不能直接用于生产环境。

## 生产环境权限要求

以下配置只在生产环境执行，不改动当前本地开发数据库：

1. 开启 Neo4j 身份认证。
2. 为 Knowledge Agent 建立独立只读账号。
3. 只授予查询需要的读取权限，不授予写入和管理权限。
4. Agent 不得使用 Neo4j 管理员账号。
5. 配置查询超时、结果数量限制和审计日志。

应用层仍保留Cypher只读Hook和只读事务，数据库权限作为最终兜底。
