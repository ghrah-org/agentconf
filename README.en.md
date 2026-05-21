# agentconf

[中文](README.md)

A universal multi-model configuration management SDK designed for single-user terminal AI applications.

Built on Pydantic + SQLite, supporting Any2Any multimodal model capability tagging.

## Features

- **Hierarchical Configuration**: Provider(BaseURL+APIKEY) → Model Instance(ModelName+ModelType+Capabilities) → Agent, with automatic inheritance from upper layers
- **Multimodal Capability Tagging**: Describe model capabilities via `model_type` and `capabilities` (vision, function calling, embedding, TTS, ASR, etc.)
- **Secure Storage**: API Keys are protected by SecretStr
- **Type Safety**: Full-chain Pydantic validation with discriminated union type support for polymorphic capabilities
- **Simple API**: Intuitive CRUD + configuration resolution interface
- **User-level Configuration**: Configure once, read consistently across multiple applications

## Roadmap

- Connection proxy layer (missing encryption and protection solution)
- Localization

## Limitations

### 1. Configuration Only

This SDK only provides configuration management. The configuration needs to be combined with a specific model SDK (such as OpenAI, LangChain) to instantiate models.

Application-level implementation details such as timeouts, retries, and prompts are not within the scope of support.

### 2. No Encryption

The generated SQLite database itself is not encrypted. It only ensures that API keys do not appear in your code files. If you use SQL queries or print(), it is still possible to accidentally print the API key!

## Quick Start

### Installation

```bash
uv add agentconf
```

### Usage

agentconf provides two usage modes:
1. **TUI Tool** - Suitable for visual interactive configuration management and viewing
2. **Python SDK** - Suitable for programmatic configuration management

---

## Python SDK Usage

### Basic Usage

```python
from agentconf import AgentsConfig, ProviderType

with AgentsConfig() as config:
    # Create a Provider
    config.create_provider(
        name="openai",
        provider_type=ProviderType.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key="sk-your-api-key",
    )

    # Create a Model Instance
    config.create_model(
        provider_name="openai",
        name="gpt4o",
        model_name="gpt-4o",
        model_type="llm",
    )

    # Create an Agent
    config.create_agent(
        name="code-reviewer",
        model_name="gpt4o",
        temperature=0.3,
        top_p=0.9,
    )

    # Resolve complete configuration (including inheritance and overrides)
    resolved = config.resolve_agent("code-reviewer")
    print(resolved.model_dump())
```

### Multimodal Capability Tagging

```python
from agentconf import AgentsConfig, ProviderType
from agentconf.models.capabilities import LLMCapabilities, EmbeddingCapabilities

with AgentsConfig() as config:
    config.create_provider(
        name="openai",
        provider_type=ProviderType.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key="sk-xxx",
    )

    # Create an LLM with vision capabilities
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

    # Create an Embedding model
    config.create_model(
        provider_name="openai",
        name="text-embed",
        model_name="text-embedding-3-large",
        model_type="embedding",
        capabilities=EmbeddingCapabilities(dimensions=1536),
    )

    # You can also pass a dict as capabilities
    config.create_model(
        provider_name="openai",
        name="rerank-v1",
        model_name="rerank-v1",
        model_type="rerank",
        capabilities={"top_n": 5, "max_input_length": 4096},
    )

    # Filter models by capability
    vision_models = config.find_models_by_capability("supports_vision")
    function_call_models = config.find_models_by_capability("supports_function_calling")
    image_input_models = config.find_models_by_capability("input_modality:image")
    audio_output_models = config.find_models_by_capability("output_modality:audio")

    # Filter by type
    llm_models = config.list_models(model_type="llm")
    embed_models = config.list_models(model_type="embedding")
```

### SDK API Reference

#### AgentsConfig Client

```python
from agentconf import AgentsConfig

# Initialize (default uses ~/.agentconf/config.db)
config = AgentsConfig()
# Or use a custom path
config = AgentsConfig(db_path="/path/to/config.db")

# Use context manager for automatic connection cleanup
with AgentsConfig() as config:
    # operations...
    pass
```

#### Provider Operations

```python
# Create
provider = config.create_provider(
    name="openai-main",
    provider_type=ProviderType.OPENAI,  # or "openai"
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
)

# Query
provider = config.get_provider("openai-main")
providers = config.list_providers()

# Update
provider = config.update_provider("openai-main", base_url="https://new-url.com")

# Delete (cascades to associated Models and Agents)
config.delete_provider("openai-main")
```

#### Model Instance Operations

```python
from agentconf.models.capabilities import LLMCapabilities

# Create
model = config.create_model(
    provider_name="openai-main",
    name="gpt4o",
    model_name="gpt-4o",
    model_type="llm",  # default "llm", options: embedding, rerank, tts, stt, asr, image_gen
    base_url="https://proxy.com/v1",  # optional: overrides Provider
    api_key="sk-proxy-key",          # optional: overrides Provider
    capabilities=LLMCapabilities(     # optional: capability tags
        supports_vision=True,
        max_context_length=128000,
    ),
)

# Query
model = config.get_model("gpt4o")
models = config.list_models()
models = config.list_models(provider_name="openai-main")  # filter by Provider
models = config.list_models(model_type="embedding")       # filter by type

# Filter by capability
vision_models = config.find_models_by_capability("supports_vision")

# Update
model = config.update_model("gpt4o", model_name="gpt-4o-turbo")

# Delete (cascades to associated Agents)
config.delete_model("gpt4o")
```

#### Agent Operations

```python
# Create
agent = config.create_agent(
    name="code-reviewer",
    model_name="gpt4o",
    temperature=0.3,
    top_p=0.9,
    top_k=40,
    max_tokens=2000,
    extra_params={"stop": ["\n\n"]},
)

# Query
agent = config.get_agent("code-reviewer")
agents = config.list_agents()

# Update
agent = config.update_agent("code-reviewer", temperature=0.5)
agent = config.update_agent("code-reviewer", model_name="gpt35")  # change Model

# Delete
config.delete_agent("code-reviewer")
```

#### Configuration Resolution

```python
# Resolve Model's complete configuration (including Provider inheritance and Model overrides)
resolved_model = config.resolve_model("gpt4o")
print(resolved_model.api_key)        # complete API Key
print(resolved_model.model_type)     # model type
print(resolved_model.capabilities)   # capability tags

# Resolve Agent's complete configuration (full inheritance chain)
resolved_agent = config.resolve_agent("code-reviewer")
print(resolved_agent.model.base_url)   # final URL used
print(resolved_agent.temperature)       # Agent parameters
print(resolved_agent.model.capabilities)  # model capabilities
```

## Data Models

### Provider

| Field | Type | Description |
|-------|------|-------------|
| name | str | Unique name |
| provider_type | enum | openai / anthropic / custom |
| base_url | str | API base URL |
| api_key | SecretStr | API Key stored in plaintext (protected by SecretStr) |

### Model Instance

| Field | Type | Description |
|-------|------|-------------|
| provider_name | str | Associated Provider |
| name | str | Instance name |
| model_name | str | Model identifier (e.g., gpt-4o) |
| model_type | str | Model type (llm / embedding / rerank / tts / stt / asr / image_gen) |
| base_url | str? | Optional URL override for Provider |
| api_key | SecretStr? | Optional Key override for Provider |
| capabilities | Capabilities? | Model capability tags (varies by model_type) |

### Agent

| Field | Type | Description |
|-------|------|-------------|
| name | str | Unique name |
| model_name | str | Associated Model Instance |
| temperature | float? | Temperature parameter (0.0-2.0) |
| top_p | float? | Top-P parameter (0.0-1.0) |
| top_k | int? | Top-K parameter |
| max_tokens | int? | Maximum output token count |
| extra_params | dict? | Extended parameters |

## Model Capability Tags (Capabilities)

Each `model_type` corresponds to a different capability description class with fields specific to that type:

### Modality (Modality Enum)

| Value | Description |
|-------|-------------|
| text | Text |
| image | Image |
| audio | Audio |
| video | Video |
| document_pdf | PDF document |
| embedding | Vector embedding |
| score | Score (Rerank output) |

### LLMCapabilities (Large Language Model)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| input_modalities | list[Modality] | [text] | Supported input modalities |
| output_modalities | list[Modality] | [text] | Supported output modalities |
| supports_streaming | bool | True | Whether streaming output is supported |
| supports_function_calling | bool | False | Whether function calling is supported |
| supports_vision | bool | False | Whether vision understanding is supported |
| supports_json_mode | bool | False | Whether JSON mode is supported |
| max_context_length | int? | None | Maximum context length (tokens) |
| max_output_length | int? | None | Maximum output length (tokens) |

### EmbeddingCapabilities (Vector Embedding)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| input_modalities | list[Modality] | [text] | Supported input modalities |
| output_modalities | list[Modality] | [embedding] | Supported output modalities |
| dimensions | int? | None | Vector dimensions |
| max_input_length | int? | None | Maximum input length (tokens) |

### RerankCapabilities (Reranking)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| input_modalities | list[Modality] | [text] | Supported input modalities |
| output_modalities | list[Modality] | [score] | Supported output modalities |
| max_input_length | int? | None | Maximum input length (tokens) |
| top_n | int? | None | Maximum number of documents returned |

### TTSCapabilities (Text-to-Speech)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| input_modalities | list[Modality] | [text] | Supported input modalities |
| output_modalities | list[Modality] | [audio] | Supported output modalities |
| languages | list[str]? | None | Supported language list |
| voices | list[str]? | None | Supported voice list |

### STTCapabilities (Speech-to-Text / ASR)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| input_modalities | list[Modality] | [audio] | Supported input modalities |
| output_modalities | list[Modality] | [text] | Supported output modalities |
| languages | list[str]? | None | Supported language list |

### ImageGenCapabilities (Image Generation)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| input_modalities | list[Modality] | [text] | Supported input modalities |
| output_modalities | list[Modality] | [image] | Supported output modalities |
| supported_sizes | list[str]? | None | Supported image size list |

## Configuration Resolution Priority

Agent → Model Instance → Provider (lower layer overrides upper layer)

```python
# Model Instance can override Provider's base_url and api_key
config.create_model(
    provider_name="openai",
    name="gpt4o-proxy",
    model_name="gpt-4o",
    base_url="https://proxy.example.com/v1",  # override
    api_key="sk-proxy-key",                      # override
)
```
## Development

```bash
# Install development dependencies
uv sync --extra dev

# Run tests
uv run pytest -v

# Run main.py to start TUI
uv run python main.py
```

## License

Apache-2.0