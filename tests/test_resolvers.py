"""独立解析结果、格式提示、能力来源与同一读取快照。"""

import pytest
from agentconf import AgentsConfig, EndpointNotFoundError, MCPServerNotFoundError


@pytest.fixture
def cfg(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as config:
        config.create_provider("gateway", "deepseek", "https://host.invalid")
        yield config


@pytest.mark.parametrize(
    "fmt,hint",
    [
        ("openai.chat_completions", "openai"),
        ("openai.responses", "openai"),
        ("openai.audio.speech", "openai"),
        ("anthropic.messages", "anthropic"),
        ("google.generate_content", "google-genai"),
        ("my.custom", None),
    ],
)
def test_format_is_explicit_and_sdk_hint_is_only_data(cfg, fmt, hint):
    cfg.create_model_endpoint("gateway", "model", "exact-request-name", fmt)
    r = cfg.resolve_model_endpoint("model")
    assert r.connection.provider_type == "deepseek"
    assert r.connection.api_format == fmt
    assert (
        r.connection.sdk_hint.distribution if r.connection.sdk_hint else None
    ) == hint
    assert r.model.model_name == "exact-request-name"
    assert r.model.model_type == "llm"
    assert r.metadata is None
    assert r.capabilities.max_context_length == 1000000
    assert (
        r.capabilities.supports_reasoning and r.capabilities.supports_function_calling
    )
    assert set(r.capability_sources.values()) == {"default"}


def test_metadata_sources_and_unknown_model_usage(cfg):
    cfg.catalog_clone("example-VLA", "local/custom")
    cfg.create_model_endpoint(
        "gateway",
        "model",
        "request",
        "openai.responses",
        capabilities_key="local/custom",
    )
    r = cfg.resolve_model_endpoint("model")
    assert r.metadata.model_key == "local/custom" and r.metadata.origin == "local"
    cfg.create_model_endpoint(
        "gateway", "unknown", "x", "future.api", model_type="future-usage"
    )
    unknown = cfg.resolve_model_endpoint("unknown")
    assert unknown.capabilities.capabilities_type == "generic"
    assert not hasattr(unknown.capabilities, "max_context_length")
    assert cfg.catalog_get("local/custom").max_output_length == 242000


def test_batch_missing_all_names_and_namespace_separation(cfg):
    cfg.create_model_endpoint("gateway", "same", "request", "openai.responses")
    cfg.create_mcp_server("same", "stdio", {"command": "python"})
    assert list(cfg.resolve_model_endpoints(["same"])) == ["same"]
    assert list(cfg.resolve_mcp_servers(["same"])) == ["same"]
    with pytest.raises(EndpointNotFoundError) as error:
        cfg.resolve_model_endpoints(["same", "missing-a", "missing-b"])
    assert "missing-a" in str(error.value) and "missing-b" in str(error.value)
    with pytest.raises(MCPServerNotFoundError):
        cfg.resolve_mcp_servers(["missing"])


def test_batch_uses_single_snapshot_during_concurrent_provider_edit(cfg):
    cfg.create_model_endpoint("gateway", "a", "a", "openai.responses")
    cfg.create_model_endpoint("gateway", "b", "b", "openai.responses")
    original = cfg._resolve_model
    changed = False

    def resolve(name):
        nonlocal changed
        result = original(name)
        if not changed:
            changed = True
            with AgentsConfig(cfg._db.db_path) as writer:
                writer.update_provider(
                    "gateway", expected_revision=1, provider_type="new-family"
                )
        return result

    cfg._resolve_model = resolve
    results = cfg.resolve_model_endpoints(["a", "b"])
    assert [r.connection.provider_type for r in results.values()] == [
        "deepseek",
        "deepseek",
    ]
    assert cfg.get_provider("gateway").provider_type == "new-family"
