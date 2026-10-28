# agentconf

agentconf 是基于 Pydantic 和 SQLite 的多模型配置 SDK。它保存服务连接、请求模型标识与静态能力，并提供离线解析；实际调用由下游客户端完成。

**本文描述当前开发工作树，尚未发布新的稳定版本。** 当前能力目录采用 schema 4；Provider、ModelInstance、Agent 的旧结构暂时保留，已有消费者仍可通过 `resolve_model` / `resolve_agent` 使用。已有旧数据库必须显式迁移，普通打开不会自动升级。

## 安装与打开

需要 Python ≥3.11，开发环境使用 uv：

```sh
uv sync
uv run agentconf --db-path ./config.db
```

裸命令打开 TUI。新库离线写入真实模型种子和七条虚构示例，不创建虚假的 Provider、模型绑定或 Agent。已有库不因包更新、重启或清空目录而重新播种。

默认库位于 `~/.agentconf/config.db`。Provider 保存连接，模型绑定保存实际请求名与能力档案引用，Agent 暂时保存采样参数。API Key 在数据库中为明文，SecretStr 的展示隐藏不等于磁盘加密；不要直接打印带凭据的解析结果。

## SDK 快速开始

下面使用临时库，不影响个人配置：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from agentconf import AgentsConfig

with TemporaryDirectory() as directory:
    with AgentsConfig(Path(directory) / "config.db") as config:
        config.create_provider("gateway", "openai", "https://api.example.com/v1", "key")
        config.create_model(
            "gateway", "chat-main", "actual-deployment-id",
            capabilities_key="example-VLA",
        )
        config.create_agent("assistant", "chat-main", temperature=0.0)
        resolved = config.resolve_agent("assistant")
        assert resolved.temperature == 0.0
        assert resolved.model.model_name == "actual-deployment-id"
        assert resolved.model.capabilities.max_context_length == 1_000_000
```

请求模型名与档案键分别填写，不按名称猜能力。`get_model` 返回纯绑定；`resolve_model` / `resolve_agent` 生成有效能力。模型连接只从 Provider 获取，不再接受模型级 URL/key/能力覆盖。

原始能力 `None` 表示未声明，`False`、空列表表示显式声明；默认值只在解析中补齐，不回写。未绑定能力的 LLM 默认上下文 1,000,000，支持工具与推理。非 LLM 按各自类型投影；`asr` 规范为 `stt`，未知用途使用 GenericCapabilities，不套 LLM 默认值。

## 能力来源与人工维护

每个 CapabilityProfile 保存完整原始声明、来源和整数 revision。来源与写权限如下：

| origin | 含义与维护方式 |
| --- | --- |
| upstream | 随包真实种子或外部目录导入的原件，人工只读，只能由同来源显式更新 |
| derived | 从一个 upstream 完整派生的人工副本，可编辑，只有一层，不做运行时继承 |
| local | 用户自建或从示例复制的档案，可编辑 |
| example | 本项目提供的虚构示范，人工只读，可复制为 local |

`source` 记录来历，不决定写权限。人工创建不会因为写了 `source="models.dev"` 就获得自动维护资格。`entry_kind`、`managed_by` 已由 origin 替代；旧 `catalog_set_management` 会给出迁移提示。

```python
from agentconf import CapabilityProfile

# 在已打开的 config 中执行
profile = config.catalog_create(CapabilityProfile(
    model_key="local/fixed", display_name="我的固定模型",
    max_context_length=128_000, supports_reasoning=False,
))
local = config.catalog_clone("example-VLA", "local/vla")
# 上游键需先存在，例如随包目录的真实模型
derived = config.catalog_derive("zhipuai/glm-5.3", "local/glm-tuned")
old = config.catalog_get(derived.model_key)
edited = CapabilityProfile.model_validate({**old.model_dump(), "max_output_length": 64_000})
saved = config.catalog_edit(edited, expected_revision=old.revision)
config.update_model("chat-main", capabilities_key=saved.model_key)
```

普通编辑只能改变展示信息、能力与说明，不能改变键、用途、origin、来源或派生关系。更新和删除检查 expected_revision，防止覆盖另一个客户端的改动。上游原件与示例不可通过普通 CRUD 删除；本地/派生档案存在引用时也不能删除。

`catalog_references` 同时列出模型绑定和 `derived:KEY` 派生引用。Provider/模型的旧级联删除语义在当前开发阶段保持；能力档案独立保留。

## 派生差异与 stale

派生时完整复制上游声明，并保存 derived_from 和 base_fingerprint。上游后续变化不修改派生内容或模型绑定；`catalog_stale` 提示上游能力已经变化。

```python
variants = config.catalog_variants("zhipuai/glm-5.3")
diff = config.catalog_diff("local/glm-tuned")
if diff["stale"]:
    old = config.catalog_get("local/glm-tuned")
    # 显式确认当前上游基线；保留人工能力，不合并上游字段
    config.catalog_rebuild(
        old.model_key, expected_revision=old.revision,
        expected_parent_fingerprint=diff["parent_fingerprint"],
    )
```

能力指纹覆盖用途、显式能力与语义性 extra；不包含展示名、描述、发布日期、来源时间或 revision。说明信息单独存 annotations，已知集合能力的顺序不影响 stale，扩展数组的顺序仍保留语义。并发检查使用 revision 和完整记录快照，不能用能力指纹代替。

有效能力的来源标签保留原有 source 语义：upstream 使用来源名，example 为 example，local 使用声明的来历（通常为 manual），derived 为 `derived:档案键`；未声明字段为 default。写权限只看 origin，不能从能力来源标签判断是否允许自动更新。

## CLI 与 TUI

CLI 默认只读预览；`--apply` 才写入。编辑文件包含完整 CapabilityProfile，先通过 show 获取 profile 后修改需要的字段。

```sh
agentconf catalog list --origin upstream --type llm
agentconf catalog show zhipuai/glm-5.3 --with-variants
agentconf catalog derive zhipuai/glm-5.3 --as local/tuned --apply
agentconf catalog clone example-embedding --as local/embedding --apply
agentconf catalog variants zhipuai/glm-5.3
agentconf catalog diff local/tuned
agentconf catalog rebuild local/tuned                       # 查看差异
agentconf catalog rebuild local/tuned --expected-revision 1 --apply
agentconf catalog create --file /tmp/profile.json           # 查看新增声明
agentconf catalog create --file /tmp/profile.json --apply
agentconf catalog edit local/tuned --file /tmp/profile.json
agentconf catalog edit local/tuned --file /tmp/profile.json --expected-revision 2 --apply
agentconf catalog delete local/embedding                    # 查看引用
agentconf catalog delete local/embedding --expected-revision 1 --apply
```

revision 只是示例，应用时必须使用自己的预览值。TUI 的能力目录支持来源筛选、查看原始声明/派生差异、派生/复制、完整编辑、基线重建、键搬迁、删除预览、导入和升级。原件只读；取消操作不写入。旧库显示迁移帮助与只读检查入口，不自动改表。

## 显式导入与手动升级

来源为 [models.dev 的独立 models.json](https://models.dev/models.json)，完整规范 ID 作为键，不聚合供应商记录、不裁剪前缀。初始化和解析不联网，只有显式下载入口取公开目录。

```sh
# 只新增，不覆盖已有差异；保存完整规范化快照
agentconf catalog import --source models.dev --plan-out /tmp/import.json
agentconf catalog import --plan /tmp/import.json --apply-all --types llm,embedding
# 查看上游差异，不写库
agentconf catalog diff --source models.dev
# 同来源 upstream 的增量更新；local/derived/example 受保护
agentconf catalog update --source models.dev --plan-out /tmp/update.json
agentconf catalog update --plan /tmp/update.json --apply-all
# 或 --apply KEY1 KEY2 只接受部分新增/可更新项
```

SDK 对应 catalog_preview/catalog_apply 与 catalog_update_preview/catalog_update_apply。apply 使用已预览的快照，不重新下载。用途/来源变化视为冲突；上游缺项保留，不自动删除。预览后内容、来源或引用发生变化，整批回滚。相同更新重复应用不改时间。旧规范化版本的计划拒绝，须重新预览。

### 人工占用上游规范键

旧人工档案占用规范键时，默认报告冲突。可以显式搬迁，保留人工声明并把原模型引用一起迁往 local/ 新键：

```sh
agentconf catalog relocate vendor/model --as local/original
agentconf catalog relocate vendor/model --as local/original --expected-revision 1 --apply
```

也可把搬迁与导入放入同一个冻结计划、同一事务。先保存映射文件，例如 `{"vendor/model":"local/original"}`：

```sh
agentconf catalog import --relocations /tmp/relocations.json --plan-out /tmp/import.json
agentconf catalog import --plan /tmp/import.json --apply-all
```

SDK 用 `catalog_preview(snapshot, relocations={...})` 或 `catalog_update_preview(snapshot, relocations={...})`。若原声明/引用/目标键在预览后变化，搬迁和导入一起回滚；原模型不能静默改指上游声明。

## 虚构示例与能力筛选

| 键 | 用途 | 主要声明 |
| --- | --- | --- |
| example-LLM | llm | text→text，上下文 1M，输出 128k，工具/推理支持 |
| example-VLA | llm | text+image→text，上下文 1M，输出 242000，工具/推理/视觉支持 |
| example-embedding | embedding | text→embedding，输入 8192，维度 1536 |
| example-rerank | rerank | text→score，输入 8192，top_n=10 |
| example-TTS | tts | text→audio，语言 zh/en |
| example-STT | stt | audio→text，语言 zh/en |
| example-imagegen | image_gen | text→image，尺寸 1024x1024 |

example-* 为保留键，origin/source 均为 example，不能由外部目录导入。它们不是实际模型，不自动作为请求名。`find_models_by_capability("supports_reasoning")`、`input_modality:image`、`output_modality:audio` 按有效能力筛选，未知条件报错。

## 旧库迁移与回滚

当前 schema 4，已有 0/2/3 库采用显式迁移；schema 4 是开发阶段，不要求用户单独部署。先停止其它配置写者，在备份副本演练：

```sh
agentconf migrate prepare --db-path /path/config.db --plan-out /tmp/migration.json
agentconf migrate apply --plan /tmp/migration.json
```

SDK 对应 prepare_migration/apply_migration，MigrationPlan 可 save/load。prepare 只读并冻结库路径、内容/结构摘要与版本，普通构造器对旧库报 MigrationRequiredError。apply 锁内重验，先用 SQLite backup 做包含 WAL 已提交内容的一致备份，再在一个事务转换；失败回滚，外键开关在事务外恢复。备份文件权限为 0600，包含密钥，不提交版本库。

旧库迁移保留 ID、时间、绑定、Agent 引用与参数，不猜能力引用、不重新灌 seed：

- schema 3：example 按旧种类识别；合法 managed_by 可证明的行迁 upstream，其余迁 local。
- schema 2：无法证明旧行是否被人工改过，真实行全部保守迁 local。
- schema 0：先拒绝任何非 NULL 的旧覆盖/映射字段，包含空串/{}；拒绝重名绑定、未知结构、非空历史 llm_instances。不能静默清空旧值。

未来版本、自定义结构、外键损坏拒绝处理。旧 metadata 缓存只在旧字段预检通过后移除。迁移不把内容相同当成来源证明，不伪造派生关系。库变动后旧计划失效，须重新 prepare。

回滚需停用写者并恢复匹配旧 SDK 的备份副本；不要覆盖运行中的主文件，也不要只复制 WAL 主文件。迁移备份路径会返回给调用方。

## 开发验证

```sh
uv run pytest -q
uv run ruff check src tests scripts
uv build
```

来源、许可和固定快照重建命令见 [随包能力种子](src/agentconf/data/README.md)。真实模型数据使用 models.dev 的 MIT 许可，代码与虚构示例使用 Apache-2.0。
