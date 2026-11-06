# agentconf

agentconf 是一个静态配置 SDK：保存模型连接、请求模型标识、模型能力目录与 MCP Server 连接，并分别解析模型端点和 MCP。它基于 Pydantic 与 SQLite，不发送模型请求、不连接 MCP、不规定 Agent 的组合或授权方式。

当前开发版本 **0.4.0**，数据库 schema 6。模型接口与旧 Agent 聚合 API 已发生破坏性变化；消费者须配套迁移，不能直接替换仍调用 resolve_agent 的运行环境。

## 安装和打开

Python ≥3.11。开发环境使用 uv：

```sh
uv sync
uv run agentconf --db-path ./config.db
# 或只初始化离线数据库
uv run agentconf init --db-path ./config.db
```

裸命令打开 TUI。首次创建数据库离线写入随包能力种子；已存在的目录不随启动、包更新或清空而重新播种。默认路径为 `~/.agentconf/config.db`。已有旧库需要先显式迁移，普通打开不会自动升级。

## 五种资源与职责

| 资源 | 保存什么 |
| --- | --- |
| Provider | 服务商/兼容家族、URL、可空密钥、连接选项 |
| ModelEndpoint | 具名端点、实际请求模型名、用途、接口格式、Provider 引用与可选能力引用 |
| CapabilityProfile | 固定发布模型的原始能力声明、来源及派生关系 |
| MCPProvider | 独立的 MCP API Key 与默认认证方式，供多个 MCP Server 共享 |
| MCPServer | 具体 transport、URL 或进程设置，通过稳定 ID 引用 MCP Provider，可显式覆盖认证方式 |

Provider → ModelEndpoint → 可选 CapabilityProfile。模型 Provider 与 MCP Provider 分域管理；MCP Server 引用 MCP Provider 的 UUID，改名不改变关联。不同域允许同名，通过不同 API 查找。删除被端点引用的 Provider 或能力档案会被拒绝，不级联删除配置。

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

## MCP Provider 与服务 SDK

MCP Provider 表示某厂商账号／项目的一套 Key 和共享认证方式，不需要填写模型服务 URL 或接口格式。同一厂商有不同账号、项目或 Key 时分别建记录；一套身份可以被多个 MCP 服务引用。

```python
provider = cfg.create_mcp_provider(
    "vendor/work-account", vendor="vendor", api_key="example-mcp-key",
    default_binding={"target": "http_header", "name": "Authorization",
                     "value_template": "Bearer {{auth}}"},
)
for name, url in [("search", "https://tools.example.com/search/mcp"),
                  ("fetch", "https://tools.example.com/fetch/mcp")]:
    cfg.create_mcp_server(name, "streamable_http", {"url": url}, auth={
        "mode": "provider", "provider_id": provider.id, "binding_mode": "inherit",
    })
resolved = cfg.resolve_mcp_server("search")
assert resolved.auth.provider_id == provider.id
# 解析仅有无密钥描述。宿主通过自己的调用授权后，再核对快照并读取 Key：
key = cfg.read_mcp_credential(
    resolved.server_id, expected_server_revision=resolved.revision,
    expected_provider_id=resolved.auth.provider_id,
    expected_provider_revision=resolved.auth.provider_revision,
)
```

Server 的 auth.mode 明确区分 none、provider、inline、external。provider 必须选择 inherit 或 override；覆盖只改变认证目标和格式，不复制或覆盖共享 Key。http_header 支持 Header 名称与单个 `{{auth}}` 模板，stdio_env 支持环境变量名与模板；有效绑定必须适配 transport，不能与连接 headers／env 同名冲突。被引用的 MCP Provider 不能删除，修改默认认证也会检查所有继承服务。

stdio 保存 command、args、env、cwd；HTTP 保存 url、headers、protocol_version_hint；两者可设置正数 connect_timeout。自定义 transport 保存任意 JSON，是否执行由宿主决定。inline 显式保存静态连接认证，external 保存不透明宿主引用与绑定；本库不访问外部凭据服务。

```python
cfg.create_mcp_server("filesystem", "stdio", {
    "command": "python", "args": ["-m", "my_mcp_server"], "env": {}, "cwd": None,
})
```

本库不连接 MCP、不启动命令；没有工具授权、审批、会话、发现或健康状态字段。ghrah 的当前宿主仅执行 streamable HTTP，并明确拒绝 external 和 stdio 执行；配置可保存不等于所有宿主均支持执行。

## CRUD、并发与批量解析

| 资源 | SDK 入口 |
| --- | --- |
| Provider | create/get/get_by_id/list/update/delete_provider，provider_references |
| 模型端点 | create/get/get_by_id/list/update/delete_model_endpoint，resolve_model_endpoint(s) |
| MCP Provider | create/get/get_by_id/list/update/delete_mcp_provider，mcp_provider_references |
| MCP | create/get/get_by_id/list/update/delete_mcp_server，resolve_mcp_server(s)、read_mcp_credential |
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
agentconf mcp-provider create --file /tmp/mcp-provider.json --apply
agentconf mcp-provider references vendor/work-account
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

TUI 分为模型 Provider、模型端点、MCP Provider、MCP 服务、能力目录和迁移帮助。MCP transport 必须通过下拉选择 stdio／Streamable HTTP／自定义，Provider 下拉显示名称、保存 UUID。认证可继承或显式覆盖，常规流程不需要手写占位符。headers／env 使用键值编辑，args 逐项添加／删除，同时保留高级 JSON。掩码按项保留；删除一项不清掉其他秘密。先预览再确认，取消不写入，短终端通过滚动和 Tab 访问完整表单。

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

旧 0/2/3/4/5 库必须显式迁到 6。在备份副本先演练，并停止其它配置写者：

```sh
agentconf migrate prepare --db-path /path/config.db --plan-out /tmp/migration.json
# custom / 无法证明格式的非 LLM 必须明确映射；例如 {"binding":"openai.responses"}
agentconf migrate prepare --db-path /path/config.db --formats /tmp/formats.json --plan-out /tmp/migration.json
agentconf migrate apply --plan /tmp/migration.json
```

schema 6 也会先验证真实表结构和数据；发现多余旧表或损坏字段时明确报错，不会仅根据版本号报告“无需迁移”。旧 SDK 0.1.0 会在打开新版库时重新创建旧表，切换前必须统一全部配置写者的 SDK。

prepare 只读，冻结源路径、版本、完整摘要、接口格式、能力键搬迁和 Agent→端点映射，不包含采样参数值/凭据。已知旧 LLM 格式为 openai→Chat Completions、anthropic→Messages、deepseek→Chat Completions。其它不猜测，unresolved 非空不能 apply。可用 --relocations 指定 local 能力键搬迁映射。

apply 获取写锁后重验，在同一事务完成全部转换，不提交 schema 4／5 中间态。先用 SQLite backup 保存包含 WAL 的 0600 原库备份，再持久化 0600 Agent 导出，才移除旧 Agent 表。保留 Provider/模型绑定 ID、时间、能力声明和引用；已有库不重新灌种子。旧非 NULL 覆盖/映射、自定义结构、损坏引用或未来版本仍会阻止迁移，不能自动清空旧值。

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

### Schema 5 的 MCP 认证映射

旧 credential_ref 不会自动猜成 Provider 名称或 UUID；未解决关联会阻止 apply。用 `--mcp-map` 指定 JSON，计划中只写身份与字段路径，不写 Key。例如从旧模型 Provider 复制共享 Key：

```json
{
  "providers": [{
    "id": "<新 MCP Provider UUID>", "name": "vendor/work-account",
    "source_model_provider_id": "<旧模型 Provider ID>",
    "default_binding": {"target": "http_header", "name": "Authorization", "value_template": "Bearer {{auth}}"}
  }],
  "servers": {
    "<旧 Server ID>": {
      "auth": {"mode": "provider", "provider_id": "<新 MCP Provider UUID>", "binding_mode": "inherit"},
      "remove_fields": [["headers", "Authorization"]]
    }
  }
}
```

```sh
agentconf migrate prepare --db-path /path/config.db --mcp-map /tmp/mcp-map.json --plan-out /tmp/migration.json
agentconf migrate apply --plan /tmp/migration.json
```

只有原 Header 存在时才填写 remove_fields。多个 Server 可引用同一目标 UUID。也可用 source_server_id、source_field（如 `["headers", "Authorization"]`）与显式 strip_prefix 从旧静态字段提取 Key。原模型 Provider 保留，复制后的 MCP Key 独立维护。旧任意引用可显式迁为 external，静态 headers／env 保留 inline；需要动态占位符而没有映射时拒绝迁移。

迁移计划版本为 3，旧版计划需重新 prepare。schema 5 没有旧 Agent 表，不生成虚构 Agent 导出；0／2／3／4 来源仍先导出旧 Agent，导出保留历史 v1 契约。应用后的写入会使原计划失效；不能用旧计划覆盖后来配置。
