# 配置契约样例

这里的模型、域名、标识和凭据均为虚构值，不能用于请求服务。

- `resolved_endpoints.json`：Chat Completions、Responses 和保留 DeepSeek 家族标识的三个 `ResolvedModelEndpoint` 结果。为便于消费者读取，凭据字段使用显式导出形式，值固定为 `EXAMPLE_ONLY`；普通 SDK 序列化会将这些字段隐藏。
- `agent_export_v1.json`：两个旧 Agent 引用同一个端点，同时保留各自不同的请求参数。`checksum` 按移除该字段后的规范 JSON 计算 SHA-256；使用 `agentconf.migrations.checksum` 复核。源摘要和 migration_id 是示意值，不能用于实际迁移。
- `schema_v5.sql`：最终四表的空数据库结构，不含随包能力种子。
- `schema_v2.sql`、`schema_v3.sql`、`schema_v4.sql`：显式迁移测试使用的旧库结构。

公开结果类型在 `agentconf.models.resolved`，创建与修改类型分别在 `provider`、`model_endpoint`、`mcp_server` 模块，并从 `agentconf` 顶层导出。连接秘密通过 `get_secret_value()` 显式读取；`metadata` 可空，`model` 必有。资源自身与 Provider 的 revision 分开，调用者不能把端点 revision 当作整个解析结果的全局版本。
