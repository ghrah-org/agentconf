"""最终资源 CRUD、引用保护和破坏性 API 诊断。"""

import pytest
from agentconf import (
    AgentsConfig,
    ProviderNotFoundError,
    EndpointNotFoundError,
    MCPServerNotFoundError,
    ResourceAlreadyExistsError,
    ResourceInUseError,
    RevisionConflictError,
    LegacyAPIError,
    CapabilityTypeMismatchError,
)


@pytest.fixture
def cfg(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as config:
        yield config


def test_provider_crud_open_family_optional_key_and_revisions(cfg):
    p = cfg.create_provider("p", "future-family", "https://host.invalid", None)
    assert p.provider_type == "future-family" and p.api_key is None
    assert cfg.get_provider_by_id(p.id) == p
    assert cfg.list_providers() == [p]
    with pytest.raises(ResourceAlreadyExistsError):
        cfg.create_provider("p", "openai", "https://other.invalid")
    changed = cfg.update_provider(
        "p",
        expected_revision=p.revision,
        name="renamed",
        api_key="new-key",
        connection_options={"query": {"token": "private"}},
    )
    assert changed.id == p.id and changed.revision == 2
    assert changed.api_key.get_secret_value() == "new-key"
    with pytest.raises(ProviderNotFoundError):
        cfg.get_provider("p")
    with pytest.raises(RevisionConflictError):
        cfg.delete_provider("renamed", expected_revision=p.revision)
    cfg.delete_provider("renamed", expected_revision=changed.revision)
    assert cfg.list_providers() == []


def test_endpoint_crud_separates_model_usage_format_and_provider(cfg):
    p = cfg.create_provider("p", "deepseek", "https://gateway.invalid")
    q = cfg.create_provider("q", "custom", "https://other.invalid")
    e = cfg.create_model_endpoint(
        "p",
        "same",
        "actual-request",
        "openai.chat_completions",
        capabilities_key="example-VLA",
    )
    assert cfg.get_model_endpoint_by_id(e.id) == e
    assert cfg.provider_references("p") == ["same"]
    with pytest.raises(ResourceInUseError):
        cfg.delete_provider("p", expected_revision=p.revision)
    with pytest.raises(CapabilityTypeMismatchError):
        cfg.update_model_endpoint("same", expected_revision=1, model_type="embedding")
    with pytest.raises(ValueError):
        cfg.update_model_endpoint("same", expected_revision=1, temperature=0)
    changed = cfg.update_model_endpoint(
        "same",
        expected_revision=1,
        name="new",
        provider_name="q",
        api_format="future.voice",
        model_type="tts",
        capabilities_key="example-TTS",
    )
    assert changed.id == e.id and changed.provider_id == q.id
    assert cfg.list_model_endpoints(
        provider_name="q", model_type="tts", api_format="future.voice"
    ) == [changed]
    assert cfg.list_model_endpoints(capability_filter="output_modality:audio") == [
        changed
    ]
    assert cfg.provider_references("p") == []
    with pytest.raises(RevisionConflictError):
        cfg.update_model_endpoint("new", expected_revision=1, model_name="stale")
    cfg.delete_model_endpoint("new", expected_revision=2)
    with pytest.raises(EndpointNotFoundError):
        cfg.get_model_endpoint("new")


def test_mcp_crud_independent_names_and_new_transport(cfg):
    cfg.create_provider("p", "custom", "https://host.invalid")
    cfg.create_model_endpoint("p", "same", "raw", "my.api")
    server = cfg.create_mcp_server(
        "same", "my.transport", {"opaque": {"anything": ["value"]}}
    )
    assert cfg.get_mcp_server_by_id(server.id) == server
    assert cfg.list_mcp_servers(transport="my.transport") == [server]
    changed = cfg.update_mcp_server(
        "same",
        expected_revision=1,
        name="tools",
        transport="stdio",
        settings={"command": "python"},
    )
    assert changed.id == server.id and changed.revision == 2
    with pytest.raises(RevisionConflictError):
        cfg.delete_mcp_server("tools", expected_revision=1)
    cfg.delete_mcp_server("tools", expected_revision=2)
    with pytest.raises(MCPServerNotFoundError):
        cfg.get_mcp_server("tools")
    assert cfg.get_model_endpoint("same").model_name == "raw"


@pytest.mark.parametrize(
    "method",
    [
        "create_agent",
        "resolve_agent",
        "list_agents",
        "create_model",
        "get_model",
        "resolve_model",
        "find_models_by_capability",
    ],
)
def test_legacy_apis_diagnose_instead_of_returning_a_different_shape(cfg, method):
    with pytest.raises(LegacyAPIError, match="model_endpoint"):
        getattr(cfg, method)("anything")


def test_readonly_client_does_not_change_database_and_cannot_write(tmp_path):
    path = tmp_path / "config.db"
    with AgentsConfig(path) as cfg:
        cfg.create_provider("p", "custom", "https://host.invalid")
    before = path.read_bytes()
    with AgentsConfig(path, read_only=True) as cfg:
        assert cfg.list_providers()[0].name == "p"
    assert path.read_bytes() == before


def test_optional_identity_guard_rejects_recreated_name(cfg):
    old = cfg.create_provider("p", "x", "https://host.invalid")
    cfg.delete_provider("p", expected_revision=1)
    cfg.create_provider("p", "x", "https://host.invalid")
    with pytest.raises(RevisionConflictError):
        cfg.update_provider(
            "p", expected_revision=1, expected_id=old.id, provider_type="changed"
        )
    with pytest.raises(RevisionConflictError):
        cfg.delete_provider("p", expected_revision=1, expected_id=old.id)


@pytest.mark.parametrize("value", [False, [], 0, ""])
def test_connection_options_do_not_coerce_invalid_empty_values(cfg, value):
    with pytest.raises(ValueError):
        cfg.create_provider("p", "x", "https://host.invalid", connection_options=value)
