# agentconf

[English](README.en.md)

泛用的多模型配置管理SDK，适用于单用户的终端AI应用的各类模型的供应商聚合。

基于 Pydantic + SQLite，支持 Any2Any 多模态模型能力标记。

## 特性

- **层级化配置**：Provider(BaseURL+APIKEY) → Model Instance(ModelName+ModelType+Capabilities) → Agent，上层配置自动继承
- **多模态能力标记**：通过 `model_type` 和 `capabilities` 描述模型能力（视觉、函数调用、嵌入、TTS、ASR 等）
- **安全存储**：API Key 通过 SecretStr 保护
- **类型安全**：全链路 Pydantic 校验，支持判别多模态和不同类型的模型
- **简洁 API**：直觉化的 CRUD + 配置解析接口
- **用户级配置**：一处配置，多应用读取且一致

## 接下来的计划

- 连接代理层(缺少的加密与保护的解决方案)
- 本地化

## 限制

### 1. 仅配置

这个SDK只提供配置信息的管理，配置信息需要结合具体的模型SDK（如 OpenAI、LangChain）来实例化模型。

对于应用层的细节实现，比如超时、重试、Prompt 等不包含在支持的范围内。

### 2. 无加密

生成的SqliteDB本身是没有加密的，它仅能保证APIKEY不会出现在你的代码文件中，如果使用sql语句查询或者print(), 仍有可能意外的打印出APIKEY！

## 快速开始

### 安装

```bash
uv add agentconf
```

### 使用方式

agentconf 提供两种使用方式：
1. **TUI 工具** - 适合可视化的交互式的管理和查看配置
2. **Python SDK** - 适合程序化配置管理

---

## Python SDK 使用

### 基础用法

```python
from agentconf import AgentsConfig, ProviderType

with AgentsConfig() as config:
    # 创建 Provider（提供商）
    config.create_provider(
        name="openai",
        provider_type=ProviderType.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key="sk-your-api-key",
    )

    # 创建 Model Instance（模型实例）
    config.create_model(
        provider_name="openai",
        name="gpt4o",
        model_name="gpt-4o",
        model_type="llm",
    )

    # 创建 Agent（代理）
    config.create_agent(
        name="code-reviewer",
        model_name="gpt4o",
        temperature=0.3,
        top_p=0.9,
    )

    # 解析完整配置（含继承和覆盖）
    resolved = config.resolve_agent("code-reviewer")
    print(resolved.model_dump())
```

### 多模态能力标记

```python
from agentconf import AgentsConfig, ProviderType
from agentconf.models.capabilities import LLMCapabilities, EmbeddingCapabilities

with AgentsConfig() as AgentsConfig:
    config.create_provider(
        name="openai",
        provider_type=ProviderType.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key="sk-xxx",
    )

    # 创建带视觉能力的 LLM
    config.create_model(
        provider_name="openai",
        name="gpt4o-vision",
        model_name="gpt-4o",
        model_type="llm",
        capabilities=LLMCapabilities(
            input_modalities=["text", "image"],
            supports_vision=True,
            supports_function_calling=True,
            max_context_length=128000,
        ),
    )

    # 创建 Embedding 模型
    config.create_model(
        provider_name="openai",
        name="text-embed",
        model_name="text-embedding-3-large",
        model_type="embedding",
        capabilities=EmbeddingCapabilities(dimensions=1536),
    )

    # 也可以传入 dict 作为 capabilities
    config.create_model(
        provider_name="openai",
        name="rerank-v1",
        model_name="rerank-v1",
        model_type="rerank",
        capabilities={"top_n": 5, "max_input_length": 4096},
    )

    # 按能力筛选模型
    vision_models = config.find_models_by_capability("supports_vision")
    function_call_models = config.find_models_by_capability("supports_function_calling")
    image_input_models = config.find_models_by_capability("input_modality:image")
    audio_output_models = config.find_models_by_capability("output_modality:audio")

    # 按类型筛选
    llm_models = config.list_models(model_type="llm")
    embed_models = config.list_models(model_type="embedding")
```

### SDK API 参考

#### agentconf 客户端

```python
from agentconf import AgentsConfig

# 初始化（默认使用 ~/.agentconf/config.db）
config = AgentsConfig()
# 或使用自定义路径
config = AgentsConfig(db_path="/path/to/config.db")

# 使用上下文管理器自动关闭连接
with AgentsConfig() as config:
    # 操作...
    pass
```

#### Provider 操作

```python
# 创建
provider = config.create_provider(
    name="openai-main",
    provider_type=ProviderType.OPENAI,  # 或 "openai"
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
)

# 查询
provider = config.get_provider("openai-main")
providers = config.list_providers()

# 更新
provider = config.update_provider("openai-main", base_url="https://new-url.com")

# 删除（级联删除关联的 Model 和 Agent）
config.delete_provider("openai-main")
```

#### Model Instance 操作

```python
from agentconf.models.capabilities import LLMCapabilities

# 创建
model = config.create_model(
    provider_name="openai-main",
    name="gpt4o",
    model_name="gpt-4o",
    model_type="llm",  # 默认 "llm"，可选: embedding, rerank, tts, stt, asr, image_gen
    base_url="https://proxy.com/v1",  # 可选：覆盖 Provider
    api_key="sk-proxy-key",          # 可选：覆盖 Provider
    capabilities=LLMCapabilities(     # 可选：能力标记
        supports_vision=True,
        max_context_length=128000,
    ),
)

# 查询
model = config.get_model("gpt4o")
models = config.list_models()
models = config.list_models(provider_name="openai-main")  # 按 Provider 筛选
models = config.list_models(model_type="embedding")       # 按类型筛选

# 按能力筛选
vision_models = config.find_models_by_capability("supports_vision")

# 更新
model = config.update_model("gpt4o", model_name="gpt-4o-turbo")

# 删除（级联删除关联的 Agent）
config.delete_model("gpt4o")
```

#### Agent 操作

```python
# 创建
agent = config.create_agent(
    name="code-reviewer",
    model_name="gpt4o",
    temperature=0.3,
    top_p=0.9,
    top_k=40,
    max_tokens=2000,
    extra_params={"stop": ["\n\n"]},
)

# 查询
agent = config.get_agent("code-reviewer")
agents = config.list_agents()

# 更新
agent = config.update_agent("code-reviewer", temperature=0.5)
agent = config.update_agent("code-reviewer", model_name="gpt35")  # 更换 Model

# 删除
config.delete_agent("code-reviewer")
```

#### 配置解析

```python
# 解析 Model 完整配置（含 Provider 继承和 Model 覆盖）
resolved_model = config.resolve_model("gpt4o")
print(resolved_model.api_key)        # 完整的 API Key
print(resolved_model.model_type)     # 模型类型
print(resolved_model.capabilities)   # 能力标记

# 解析 Agent 完整配置（完整继承链）
resolved_agent = config.resolve_agent("code-reviewer")
print(resolved_agent.model.base_url)   # 最终使用的 URL
print(resolved_agent.temperature)       # Agent 参数
print(resolved_agent.model.capabilities)  # 模型能力
```

## 数据模型

### Provider（提供商）

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 唯一名称 |
| provider_type | enum | openai / anthropic / custom |
| base_url | str | API 基础 URL |
| api_key | SecretStr | 明文存储的 API Key（SecretStr 保护） |

### Model Instance（模型实例）

| 字段 | 类型 | 说明 |
|------|------|------|
| provider_name | str | 关联的 Provider |
| name | str | 实例名称 |
| model_name | str | 模型标识（如 gpt-4o） |
| model_type | str | 模型类型（llm / embedding / rerank / tts / stt / asr / image_gen） |
| base_url | str? | 可选覆盖 Provider 的 URL |
| api_key | SecretStr? | 可选覆盖 Provider 的 Key |
| capabilities | Capabilities? | 模型能力标记（按 model_type 差异化） |

### Agent（代理）

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 唯一名称 |
| model_name | str | 关联的 Model Instance |
| temperature | float? | 温度参数 (0.0-2.0) |
| top_p | float? | Top-P 参数 (0.0-1.0) |
| top_k | int? | Top-K 参数 |
| max_tokens | int? | 最大输出 token 数 |
| extra_params | dict? | 扩展参数 |

## 模型能力标记（Capabilities）

每个 `model_type` 对应不同的能力描述类，包含特定于该类型的能力字段：

### Modality（模态枚举）

| 值 | 说明 |
|----|------|
| text | 文本 |
| image | 图像 |
| audio | 音频 |
| video | 视频 |
| document_pdf | PDF 文档 |
| embedding | 向量嵌入 |
| score | 分数（Rerank 输出） |

### LLMCapabilities（大语言模型）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| input_modalities | list[Modality] | [text] | 支持的输入模态 |
| output_modalities | list[Modality] | [text] | 支持的输出模态 |
| supports_streaming | bool | True | 是否支持流式输出 |
| supports_function_calling | bool | False | 是否支持函数调用 |
| supports_vision | bool | False | 是否支持视觉理解 |
| supports_json_mode | bool | False | 是否支持 JSON 模式 |
| max_context_length | int? | None | 最大上下文长度（tokens） |
| max_output_length | int? | None | 最大输出长度（tokens） |

### EmbeddingCapabilities（向量嵌入）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| input_modalities | list[Modality] | [text] | 支持的输入模态 |
| output_modalities | list[Modality] | [embedding] | 支持的输出模态 |
| dimensions | int? | None | 向量维度 |
| max_input_length | int? | None | 最大输入长度（tokens） |

### RerankCapabilities（重排序）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| input_modalities | list[Modality] | [text] | 支持的输入模态 |
| output_modalities | list[Modality] | [score] | 支持的输出模态 |
| max_input_length | int? | None | 最大输入长度（tokens） |
| top_n | int? | None | 最大返回文档数 |

### TTSCapabilities（文本转语音）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| input_modalities | list[Modality] | [text] | 支持的输入模态 |
| output_modalities | list[Modality] | [audio] | 支持的输出模态 |
| languages | list[str]? | None | 支持的语言列表 |
| voices | list[str]? | None | 支持的音色列表 |

### STTCapabilities（语音转文本 / ASR）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| input_modalities | list[Modality] | [audio] | 支持的输入模态 |
| output_modalities | list[Modality] | [text] | 支持的输出模态 |
| languages | list[str]? | None | 支持的语言列表 |

### ImageGenCapabilities（图像生成）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| input_modalities | list[Modality] | [text] | 支持的输入模态 |
| output_modalities | list[Modality] | [image] | 支持的输出模态 |
| supported_sizes | list[str]? | None | 支持的图像尺寸列表 |

## 配置解析优先级

Agent → Model Instance → Provider（下层覆盖上层）

```python
# Model Instance 可覆盖 Provider 的 base_url 和 api_key
config.create_model(
    provider_name="openai",
    name="gpt4o-proxy",
    model_name="gpt-4o",
    base_url="https://proxy.example.com/v1",  # 覆盖
    api_key="sk-proxy-key",                      # 覆盖
)
```
## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest -v

# 运行 main.py 以启动TUI
uv run python main.py 
```

## License

Apache-2.0
