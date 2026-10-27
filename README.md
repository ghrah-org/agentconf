# agentconf

agentconf 是基于 Pydantic 和 SQLite 的多模型配置 SDK，适用于个人终端应用和 Agent
运行时。它管理连接、模型能力和请求参数；实际模型调用交给 OpenAI 等客户端完成。

0.2 将配置分成四个实体：

| 实体 | 保存什么 |
| --- | --- |
| Provider | 协议类型、Base URL、API Key |
| 能力档案 `CapabilityProfile` | 固定发布模型的能力声明，例如上下文、模态、工具和推理支持 |
| 模型绑定 `ModelInstance` | 全局唯一的绑定名、实际请求模型名、Provider 引用、可选的能力档案键 |
| Agent | 模型绑定与 temperature、top_p、top_k、max_tokens、extra_params |

能力档案可以被多个绑定共享。请求模型名与档案键分别填写，不按名称推断能力。
API Key 在数据库中以明文保存，`SecretStr` 只隐藏对象展示时的内容，不提供磁盘加密。
解析后的配置包含实际密钥，不应直接打印完整解析对象。

## 安装和配置

需要 Python 3.11 或更高版本：

```sh
uv add agentconf
uv run agentconf
# 或选择另一份库
uv run agentconf --db-path ./config.db
```

不带子命令时打开 TUI。Provider、Model、Agent 页面负责连接、绑定和采样参数；
“能力目录”页面可筛选真实模型与虚构示例、查看原始声明、创建和纠错，也可显式下载
导入预览。纠错先显示修改前后内容和引用绑定，再确认保存；取消删除不会执行级联删除。

默认数据库为 `~/.agentconf/config.db`。第一次创建或升级数据库时，程序离线写入随包
真实模型种子和七条示例，不创建虚假的 Provider、绑定或 Agent。之后启动不联网、
不重复播种，也不会因包更新或目录被清空而自动恢复条目。

## SDK 快速开始

下面使用临时库，重复运行也不会影响个人配置：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from agentconf import AgentsConfig

with TemporaryDirectory() as directory:
    with AgentsConfig(Path(directory) / "config.db") as config:
        config.create_provider(
            "gateway", "openai", "https://api.example.com/v1", "your-api-key"
        )
        config.create_model(
            provider_name="gateway",
            name="chat-main",                 # 本地绑定名，全局唯一
            model_name="actual-deployment-id", # 服务端实际接受的请求名
            model_type="llm",
            capabilities_key="example-VLA",    # 虚构能力示范，请替换为合适的真实档案
        )
        config.create_agent("assistant", "chat-main", temperature=0.7, max_tokens=4096)
        resolved = config.resolve_agent("assistant")
        assert resolved.model.model_name == "actual-deployment-id"
        assert resolved.model.capabilities.max_context_length == 1_000_000
        assert resolved.model.capabilities.max_output_length == 242_000
        assert resolved.model.capability_sources["max_context_length"] == "example"
```

真实模型使用目录中的完整规范键，例如 `zhipuai/glm-5.3`。可以先在 TUI 中查看档案，
也可以调用 `catalog_list(model_type="llm", entry_kind="model")` 和 `catalog_get(key)`。
`get_model()` 返回纯绑定；通过 `resolve_model()` 或 `resolve_agent()` 获取有效能力。

模型绑定不接受 `base_url`、`api_key` 或内嵌 `capabilities`。连接只从 Provider 获取，
更换 Provider 时新建绑定；修改能力时维护档案，再显式绑定：

```python
from agentconf import CapabilityProfile

# 在已打开的 config 中执行：
profile = config.catalog_create(CapabilityProfile(
    model_key="local/my-fixed-release", display_name="我的固定模型", model_type="llm",
    max_context_length=128_000, supports_reasoning=False,
))
config.update_model("chat-main", capabilities_key=profile.model_key)
config.update_model("chat-main", capabilities_key=None)  # 清除引用，按类型补默认值
```

档案中的 `None` 表示未声明，`False` 表示明确不支持；有效默认值不会回写到档案。
未绑定档案的 LLM 默认上下文为 1,000,000，支持工具与推理，视觉和 JSON 模式默认 False。
档案中的 `max_output_length` 是能力上限；Agent 的 `max_tokens` 是实际请求配额，两者不会自动互相赋值。

## 模型类型与虚构示例

所有类型通过同一条解析路径生成有效能力。`asr` 统一保存为 `stt`，未知类型使用
`GenericCapabilities`，不会获得 LLM 专属的上下文或工具默认值。

| 档案键 | 类型 | 主要声明 |
| --- | --- | --- |
| `example-LLM` | llm | text → text，上下文 1M，输出上限 128k，工具、推理 True，视觉 False |
| `example-VLA` | llm | text + image → text，上下文 1M，输出上限 242000，视觉、工具、推理 True |
| `example-embedding` | embedding | text → embedding，输入上限 8192，维度 1536 |
| `example-rerank` | rerank | text → score，输入上限 8192，top_n=10 |
| `example-TTS` | tts | text → audio，语言 zh/en，音色未知 |
| `example-STT` | stt | audio → text，语言 zh/en |
| `example-imagegen` | image_gen | text → image，尺寸示范 1024x1024 |

这些档案不是实际发布模型，也不会自动成为请求名。`example-*` 是保留命名空间，
必须同时声明 `entry_kind="example"`、`source="example"`；外部导入不能占用这些键。
video、realtime 及其它未知类型可显式建档并通过通用能力类保留模态和 `extra`。

`find_models_by_capability("supports_reasoning")`、`input_modality:image`、
`output_modality:audio` 等筛选基于有效能力，对全部模型类型生效。未知筛选条件明确报错。

## 显式导入外部目录

默认来源为 [models.dev 的独立 models.json](https://models.dev/models.json)。完整规范 ID
直接作为档案键，不聚合供应商列表、不裁剪前缀、不按请求名自动绑定。
初始化与普通查询不访问网络，只有显式导入会下载公开数据。

```sh
# 默认只读预览，不创建数据库、不迁移旧库；保存这一份规范化快照
agentconf catalog import --source models.dev --plan-out /tmp/catalog-plan.json

# 接受同一快照中所选的新增项，不再次下载
agentconf catalog import --plan /tmp/catalog-plan.json --apply zhipuai/glm-5.3

# 接受同一快照中的所有新增 LLM 与 embedding
agentconf catalog import --plan /tmp/catalog-plan.json --apply-all --types llm,embedding

agentconf catalog list --kind example
agentconf catalog list --kind model --type embedding
agentconf catalog show example-VLA
agentconf catalog diff --source models.dev
```

只有预览中属于“新增”的键可接受。已有相同内容直接跳过，不修改更新时间；已有差异
只报告并保留本地内容，上游缺项也保留。一次批量写入是原子的；若预览后有其它客户端
创建同一键，相同内容跳过，不同内容则整批回滚。人工档案、示例与人工修订都不会被导入覆盖。

快照保存来源 URL、原始 SHA256、规范化版本及完整声明。`--dry-run` 明确指定只读预览，
不能与应用参数同时使用。首次通过应用命令创建新库时仍会执行正常的离线种子初始化。
只读命令遇到旧库会要求先显式打开 SDK 或 TUI 完成迁移，不会自行升级旧库。

SDK 的对应操作为：

```python
plan = config.catalog_preview()              # 显式下载和预览
plan.save("/tmp/catalog-plan.json")
# plan.added / changed / unchanged / missing / differences
result = config.catalog_apply(plan, keys=plan.added[:2])
# 或 types=["llm", "embedding"] 限定所选新增项的类型
```

## 人工纠错和引用保护

档案键、模型类型、档案种类创建后不可修改；要改变它们，应新建档案并重新绑定。
显示名称、能力及来源可以显式纠错。编辑需要当前修订指纹，防止覆盖其它客户端的修改：

```python
from agentconf.catalog import revision

old = config.catalog_get("local/my-fixed-release")
references = config.catalog_references(old.model_key)  # 查看受影响的绑定
values = old.model_dump()
values.update(max_context_length=256_000, source="manual", source_ref=None)
corrected = CapabilityProfile.model_validate(values)
config.catalog_edit(corrected, expected_revision=revision(old))
```

CLI 可先从 `catalog show KEY` 获取完整 `profile` JSON 与修订指纹，修改后保存到文件：

```sh
agentconf catalog edit local/my-fixed-release --file /tmp/profile.json
# 核对 old/new、references 和 revision 后，再明确应用同一文件
agentconf catalog edit local/my-fixed-release --file /tmp/profile.json \
  --expected-revision PREVIEW_REVISION --apply

agentconf catalog create --file /tmp/new-profile.json      # 默认预览
agentconf catalog create --file /tmp/new-profile.json --apply
agentconf catalog delete local/my-fixed-release           # 默认查看引用影响
agentconf catalog delete local/my-fixed-release --apply
```

被绑定引用的档案禁止删除。删除 Provider 会级联删除其模型绑定和 Agent；删除绑定会
级联删除其 Agent，能力档案独立保留。TUI 对级联删除要求确认。

## 从 0.1 升级与回滚

0.2 是破坏性配置接口变更。升级前停止其它配置写入者，再显式打开 SDK 或 TUI。
运行时仍用旧版库时不要让新旧 SDK 同时维护同一份配置。

升级按 `PRAGMA user_version` 执行 0 → 2：

1. 在任何 schema 改动之前预检，拒绝所有非 SQL NULL 的模型覆盖列，包括空串和 `"{}"`；拒绝跨 Provider 重名绑定。错误列出绑定 ID、名称和列名，不打印覆盖值。
2. 获取写锁后复检，用 SQLite `Connection.backup()` 创建同目录一致性备份，包含 WAL 中已提交的数据。
3. 在一个事务中重建 Provider 与模型绑定表，保留 ID、时间、Agent 引用和采样参数；旧绑定的档案键保持 NULL，不猜测对应档案。
4. 初始化种子，删除旧 metadata 缓存表，校验配置可解析、行数和外键后提交版本。任一步失败整体回滚。

历史重命名留下的空 `llm_instances` 表可安全移除；该表仍有记录、自定义表/列/索引/
触发器或不支持的 schema 版本时会拒绝自动升级，要求先人工处理。

迁移备份命名为 `config.db.backup-时间-随机标识`，它包含密钥，按个人配置保管。回滚时
先停止所有配置读取和写入进程，用旧版 SDK 打开备份的副本，或用 SQLite 备份 API 恢复到
一个新路径并让旧版应用指向该路径。不要在运行中的库上直接覆盖主文件，也不要仅复制 WAL 主文件。

以下旧接口已删除：模型级连接/能力覆盖、`metadata_provider`、`metadata_model`、
`refresh_metadata()`、元数据缓存查询 API、`metadata_*` 构造参数，以及
`--refresh-metadata`、`--metadata-status`、`--offline`。传入旧字段会明确报错。

## 开发与数据维护

```sh
uv sync
uv run pytest -q
uv run ruff check src tests scripts
uv build
```

种子的来源、许可与可复现生成命令见 [随包能力种子](src/agentconf/data/README.md)。
真实模型数据使用 models.dev 的 MIT 许可；本项目代码与虚构示例使用 Apache-2.0。
