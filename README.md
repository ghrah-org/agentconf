# agentconf

agentconf 是一个静态配置 SDK：保存模型连接、请求模型标识、模型能力目录与 MCP Server 连接，并分别解析模型端点和 MCP。它基于 Pydantic 与 SQLite，不发送模型请求、不连接 MCP、不规定 Agent 的组合或授权方式。

当前版本 **0.3.0a1** 是候选版本，数据库 schema 5。模型接口与旧 Agent 聚合 API 已发生破坏性变化；消费者须配套迁移，不能直接替换仍调用 resolve_agent 的运行环境。

## 安装和打开

Python ≥3.11。开发环境使用 uv：

```sh
uv sync
uv run agentconf --db-path ./config.db
# 或只初始化离线数据库
uv run agentconf init --db-path ./config.db
```

裸命令打开 TUI。首次创建数据库离线写入随包能力种子；已存在的目录不随启动、包更新或清空而重新播种。默认路径为 `~/.agentconf/config.db`。已有旧库需要先显式迁移，普通打开不会自动升级。

## 四种资源与职责

| 资源 | 保存什么 |
| --- | --- |
| Provider | 服务商/兼容家族、URL、可空密钥、连接选项 |
| ModelEndpoint | 具名端点、实际请求模型名、用途、接口格式、Provider 引用与可选能力引用 |
| CapabilityProfile | 固定发布模型的原始能力声明、来源及派生关系 |
| MCPServer | 独立的 transport 与连接设置，不经过 Provider/模型表 |

Provider → ModelEndpoint → 可选 CapabilityProfile。MCP 独立管理，允许与模型端点同名，通过不同 API 查找。删除被端点引用的 Provider 或能力档案会被拒绝，不级联删除配置。

`provider_type` 是服务商或兼容家族，`model_type` 是用途，`api_format` 是调用契约。三者使用开放字符串，不能互相推断。例如一个网关同时提供 `openai.chat_completions`、`openai.responses`、`openai.embeddings` 或专门音频接口，各端点分别声明格式。

## 模型端点 SDK

下面使用临时库，不修改个人配置：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from agentconf import AgentsConfig

with TemporaryDirectory() as directory:
    with AgentsConfig(Path(directory) / "config.db") as cfg:
        cfg.create_provider(
            "gateway", "openai", "https://api.example.com/v1", "example-key"
        )
        endpoint = cfg.create_model_endpoint(
            "gateway", "chat-main", "actual-request-model", "openai.responses",
            model_type="llm", capabilities_key="example-VLA",
        )
        result = cfg.resolve_model_endpoint("chat-main")
        assert result.model.model_name == "actual-request-model"
        assert result.capabilities.max_context_length == 1_000_000
        assert result.metadata.model_key == "example-VLA"
        assert result.connection.api_format == "openai.responses"
        # 只取得描述；使用者自行选择 SDK、配置请求参数并发起调用
        url = result.connection.base_url.get_secret_value()
        key = result.connection.api_key.get_secret_value() if result.connection.api_key else None
```

解析结果固定分为 connection、必有的 model、可空 metadata、有效 capabilities 与 capability_sources。没有能力引用时按用途补默认值，metadata 为 None；默认值不写回目录。LLM 默认上下文 1,000,000，支持工具与推理；非 LLM/未知用途不套用 LLM 默认。

SDK hint 只是建议包名，不导入或安装该包。内置提示覆盖 Chat Completions、Responses、Embedding、音频转录/合成、Anthropic Messages、Google GenerateContent、Ollama Chat。未知格式原样返回，sdk_hint 为 None。DeepSeek 可声明 openai.chat_completions，同时保留 provider_type=deepseek 供下游处理厂商差异。

不接受端点级连接覆盖、采样参数或 Agent 主备关系；temperature、max_tokens、工具授权等由下游保存。

## MCP SDK

已知 transport 按独立设置模型严格校验；无效已知设置不会退回 Generic：

```python
cfg.create_mcp_server("filesystem", "stdio", {
    "command": "python", "args": ["-m", "my_mcp_server"], "env": {}, "cwd": None,
})
cfg.create_mcp_server("remote-tools", "streamable_http", {
    "url": "https://tools.example.com/mcp",
    "headers": {}, "credential_ref": "my-host/tools-service",
    "protocol_version_hint": None,
})
server = cfg.resolve_mcp_server("filesystem")
assert server.transport == "stdio"
command = server.connection.command.get_secret_value()
```

stdio 支持 command、args、env、cwd；streamable_http 支持 url、headers、credential_ref、protocol_version_hint。两者可设置正数 connect_timeout。未知 transport 保存任意 JSON 对象，解析为 GenericMCPConnection.options；是否支持由宿主决定。旧 SSE 不内置专用适配，可作为自定义 transport 保存。

凭据引用是宿主可识别的描述，本库不读环境/文件或凭据服务。协议版本提示不是协商结果。没有 trusted、工具名单、审批、角色、会话、发现结果或健康状态字段；进程、网络、协议处理与授权都由宿主负责。

## CRUD、并发与批量解析

| 资源 | SDK 入口 |
| --- | --- |
| Provider | create/get/get_by_id/list/update/delete_provider，provider_references |
| 模型端点 | create/get/get_by_id/list/update/delete_model_endpoint，resolve_model_endpoint(s) |
| MCP | create/get/get_by_id/list/update/delete_mcp_server，resolve_mcp_server(s) |
| 能力目录 | catalog_create/get/list/edit/delete、derive/clone/variants/diff/rebuild/relocate、import/update 预览及应用 |

方法名称具体为 get_provider_by_id、list_providers、get_model_endpoint_by_id、list_model_endpoints、get_mcp_server_by_id、list_mcp_servers 等。

```python
old = cfg.get_model_endpoint("chat-main")
changed = cfg.update_model_endpoint(
    "chat-main", expected_revision=old.revision, expected_id=old.id,
    api_format="openai.chat_completions",
)
# 改名保留 ID；外部按名称引用的配置需要使用者同步
cfg.update_model_endpoint("chat-main", expected_revision=changed.revision, name="chat-new")

models = cfg.resolve_model_endpoints(["chat-new"])
servers = cfg.resolve_mcp_servers(["filesystem", "remote-tools"])
```

更新/删除必须提供 expected_revision。可同时提供 expected_id，防止同名资源删除重建后被过时预览误写；TUI 和同次 CLI 预览/应用会自动携带身份。批量解析按输入顺序返回名称映射，在同一只读事务内读取；缺项整批报错，列出全部缺失名称。

ProviderUpdate、ModelEndpointUpdate、MCPServerUpdate 可表达部分修改；使用 exclude_unset 区分“未提供”和“显式清空”：

```python
from agentconf import ProviderUpdate
patch = ProviderUpdate(api_key=None)
cfg.update_provider("gateway", expected_revision=cfg.get_provider("gateway").revision,
    **patch.model_dump(exclude_unset=True, context={"show_secrets": True}))
```

API key 可用 None 清空，headers/env/args/连接选项用空对象或空数组清空；必需字段不能清空。嵌套设置按完整对象替换，不隐藏合并请求参数。

## 敏感字段与编辑

URL、API key、连接选项、MCP command/args/env/cwd、headers、凭据引用和自定义对象默认受保护，repr、model_dump、JSON、CLI/TUI 详情与预览不会直接展示。URL 整体隐藏，避免 userinfo 或查询字符串泄漏。SDK 使用 SecretStr 或 Secret[T]，只有 get_secret_value 才显式取原值。

```python
safe = cfg.get_provider("gateway").model_dump(mode="json")
# 主动导出真实值；请自行保护导出内容
raw = cfg.get_provider("gateway").model_dump(mode="json", context={"show_secrets": True})
```

编辑中的 `**********` 表示保留旧秘密，不作为新值写回；无法对应旧敏感字段的掩码会被拒绝。None/空集合按字段语义清空，提供真实新值则替换。展示数据不能直接当作新建配置；主动导出后也只取所需输入字段。

秘密在 SQLite 中仍以明文保存；展示保护不等于磁盘加密。迁移备份和 Agent 导出包含敏感内容，使用私密权限保存。

## CLI 和 TUI

各命令组默认预览，--apply 才写；JSON 输入文件避免密钥进入命令历史。--db-path 可放在命令前或操作后。

```sh
agentconf provider create --file /tmp/provider.json --apply
agentconf provider list
agentconf provider show gateway
agentconf provider references gateway
agentconf endpoint create --file /tmp/endpoint.json --apply
agentconf endpoint list --type llm --api-format openai.responses
agentconf endpoint resolve chat-main
agentconf mcp create --file /tmp/mcp.json --apply
agentconf mcp list --transport stdio
agentconf mcp resolve filesystem
agentconf provider update gateway --file /tmp/patch.json
agentconf provider update gateway --file /tmp/patch.json --expected-revision 1 --apply
agentconf mcp delete filesystem --expected-revision 1 --apply
# 显式展示秘密，只有需要时使用
agentconf provider show gateway --show-secrets
```

create 的输入字段对应 ProviderCreate、ModelEndpointCreate、MCPServerCreate。update 文件只包含要改的字段，show 的 input 对象可作为编辑模板；--expected-id 可进一步锁定身份。模型端点文件示例：

```json
{
  "name": "chat-main", "provider_name": "gateway",
  "model_name": "actual-request-model", "model_type": "llm",
  "api_format": "openai.responses", "capabilities_key": "example-VLA"
}
```

TUI 导航为 Provider、模型端点、MCP、能力目录、迁移帮助。三类连接资源支持列表、详情、创建、编辑、删除，模型和 MCP 有独立解析预览。MCP 根据 transport 切换 stdio/HTTP 表单，自定义 transport 使用 JSON。编辑先预览再确认，取消不写入，默认隐藏已保存的秘密。

## 能力目录与手动更新

origin 分四类：upstream 原件和 example 示例只读，derived 是一层完整人工派生，local 是本地声明。source 表示来历，不能作为维护权限。派生不继承上游后续变更，只通过语义指纹提示 stale；说明和时间不影响指纹，扩展数组顺序保留意义。

```sh
agentconf catalog list --origin upstream --type llm
agentconf catalog show zhipuai/glm-5.3 --with-variants
agentconf catalog derive zhipuai/glm-5.3 --as local/tuned --apply
agentconf catalog clone example-embedding --as local/embedding --apply
agentconf catalog variants zhipuai/glm-5.3
agentconf catalog diff local/tuned
agentconf catalog rebuild local/tuned --expected-revision 1 --apply
agentconf catalog create --file /tmp/profile.json --apply
agentconf catalog edit local/tuned --file /tmp/profile.json --expected-revision 2 --apply
```

真实种子来自 [models.dev 的独立 models.json](https://models.dev/models.json)，当前快照仍为 607 条真实模型与 7 条虚构示例。示例包括 example-VLA（text+image→text，上下文 1M、输出 242000、工具/推理支持）、LLM、Embedding、TTS、STT、Rerank 和图像生成组合，不是真实请求模型。

```sh
agentconf catalog import --plan-out /tmp/import.json
agentconf catalog import --plan /tmp/import.json --apply-all
agentconf catalog update --plan-out /tmp/update.json
agentconf catalog update --plan /tmp/update.json --apply-all --types llm,embedding
```

只有显式预览才下载目录；apply 使用同一快照，不重取。import 只新增；update 只更新同源 upstream，保护人工/派生/示例，保留上游缺项。用途/来源/内容或引用变化会导致整批回滚。旧规范化/schema 的计划须重新预览。

人工档案占用上游键时，默认冲突。可显式 relocate 至 local/，或在预览时给出 relocations 映射，把搬迁和导入放进同一事务，旧端点继续绑定原人工声明。能力 CRUD/删除保护、原始 nullable 声明及有效默认值的规则保持。

## 旧库迁移、Agent 导出与重试

旧 0/2/3/4 库必须显式迁到 5。在备份副本先演练，并停止其它配置写者：

```sh
agentconf migrate prepare --db-path /path/config.db --plan-out /tmp/migration.json
# custom / 无法证明格式的非 LLM 必须明确映射；例如 {"binding":"openai.responses"}
agentconf migrate prepare --db-path /path/config.db --formats /tmp/formats.json --plan-out /tmp/migration.json
agentconf migrate apply --plan /tmp/migration.json
```

prepare 只读，冻结源路径、版本、完整摘要、接口格式、能力键搬迁和 Agent→端点映射，不包含采样参数值/凭据。已知旧 LLM 格式为 openai→Chat Completions、anthropic→Messages、deepseek→Chat Completions。其它不猜测，unresolved 非空不能 apply。可用 --relocations 指定 local 能力键搬迁映射。

apply 获取写锁后重验，在同一事务完成全部转换，不提交 schema 4 中间态。先用 SQLite backup 保存包含 WAL 的 0600 原库备份，再持久化 0600 Agent 导出，才移除旧 Agent 表。保留 Provider/模型绑定 ID、时间、能力声明和引用；已有库不重新灌种子。旧非 NULL 覆盖/映射、自定义结构、损坏引用或未来版本仍会阻止迁移，不能自动清空旧值。

Agent 导出格式 export_version=1，包含 migration_id、源 schema/摘要、checksum、完整 agents 列表。每项含 agent_id/name、endpoint_id/name、model_name、创建/更新时间及 request_params（temperature、top_p、top_k、max_tokens、extra_params，包含显式 0/False/None）。下游据此转换名称和请求参数；多个旧 Agent 可映射同一端点。

迁移同时保留 prepared/result 文件，记录目标摘要、备份及导出校验和。中断后使用同一冻结计划重试：未提交则重新转换，已提交但结果记录未完成则核验并补全；不覆盖已有不同内容的导出。目标库后来变动或导出损坏时停止，不重复删表。回滚必须停写并恢复旧 SDK 与对应旧库/下游配置，不能直接覆盖运行中的 WAL 主文件。

旧 create_model/update_model/resolve_model、Agent CRUD/resolve_agent 和 catalog_set_management 只给迁移诊断，旧 Agent 模型不再公开。主备、采样、MCP 授权和运行均由下游负责。

## 开发验证与许可

```sh
uv run pytest -q
uv run ruff check src tests scripts
uv build
```

来源、许可与固定快照重建命令见 [随包能力种子](src/agentconf/data/README.md)。真实模型数据使用 models.dev MIT 许可，代码与虚构示例使用 Apache-2.0。发行包不依赖 OpenAI、Anthropic 或 MCP 客户端。
