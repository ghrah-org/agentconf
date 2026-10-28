"""严格 transport 分派、纯静态解析及所有敏感连接字段的默认保护。"""

import sys
from unittest.mock import patch
import pytest
from agentconf import AgentsConfig, MCPServerCreate, ProviderCreate
from agentconf.models.mcp_server import GenericMCPConnection
from agentconf.models.secure import MASK

SENTINEL = "PRIVATE-SENTINEL-8a7c"


@pytest.fixture
def cfg(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as config:
        yield config


@pytest.mark.parametrize(
    "transport,settings",
    [
        ("stdio", {}),
        ("stdio", {"command": ""}),
        ("stdio", {"command": "python", "url": "https://host.invalid"}),
        ("stdio", {"command": "python", "headers": {}}),
        ("stdio", {"command": "python", "args": [42]}),
        ("stdio", {"command": "python", "env": {"X": 5}}),
        ("stdio", {"command": "python", "connect_timeout": 0}),
        ("stdio", {"command": "python", "connect_timeout": True}),
        ("streamable_http", {"url": "file:///secret"}),
        ("streamable_http", {"url": "https://host.invalid", "command": "python"}),
        ("streamable_http", {"url": "https://host.invalid", "credential_ref": ""}),
        ("streamable_http", {"url": "https://host.invalid", "trusted": True}),
    ],
)
def test_known_transport_invalid_input_does_not_fall_back(transport, settings):
    with pytest.raises(ValueError):
        MCPServerCreate(name="tools", transport=transport, settings=settings)


@pytest.mark.parametrize(
    "field",
    [
        "trusted",
        "allow_tools",
        "roles",
        "provider_id",
        "model_name",
        "capabilities_key",
    ],
)
def test_business_policy_and_model_fields_rejected(field):
    with pytest.raises(ValueError):
        MCPServerCreate(
            name="tools", transport="anything", settings={}, **{field: True}
        )


def test_no_network_process_sdk_import_or_environment_resolution(cfg):
    cfg.create_mcp_server(
        "stdio",
        "stdio",
        {
            "command": "not-a-command",
            "args": [SENTINEL],
            "env": {"API_KEY": SENTINEL},
            "cwd": "/does/not/exist",
        },
    )
    cfg.create_mcp_server(
        "http",
        "streamable_http",
        {
            "url": "https://user:" + SENTINEL + "@host.invalid/mcp?token=" + SENTINEL,
            "headers": {"Authorization": SENTINEL},
            "credential_ref": "opaque-secret-reference",
        },
    )
    cfg.create_mcp_server("custom", "unregistered", {"custom": {"token": SENTINEL}})
    before = set(sys.modules)
    with (
        patch("subprocess.Popen", side_effect=AssertionError("no subprocess")),
        patch("socket.socket", side_effect=AssertionError("no network")),
    ):
        records = cfg.resolve_mcp_servers(["stdio", "http", "custom"])
    assert list(records) == ["stdio", "http", "custom"]
    assert not ({"openai", "anthropic", "mcp"} & (set(sys.modules) - before))
    for name, resolved in records.items():
        assert SENTINEL not in repr(resolved)
        assert SENTINEL not in repr(resolved.model_dump())
        assert SENTINEL not in resolved.model_dump_json()
        assert not hasattr(resolved, "model")
        assert resolved.connection is not None
    assert isinstance(records["custom"].connection, GenericMCPConnection)
    assert (
        records["custom"].connection.options.get_secret_value()["custom"]["token"]
        == SENTINEL
    )
    assert SENTINEL in records["http"].model_dump_json(context={"show_secrets": True})


def test_validation_errors_hide_inputs_even_nested_secrets():
    for model, payload in [
        (
            MCPServerCreate,
            dict(
                name="x",
                transport="stdio",
                settings={"command": "python", "args": SENTINEL},
            ),
        ),
        (
            MCPServerCreate,
            dict(
                name="x",
                transport="streamable_http",
                settings={"url": "not-a-url-" + SENTINEL},
            ),
        ),
        (ProviderCreate, dict(name="p", provider_type="x", base_url="bad-" + SENTINEL)),
    ]:
        with pytest.raises(ValueError) as error:
            model.model_validate(payload)
        assert SENTINEL not in str(error.value)


def test_mask_edit_preserves_values_and_explicit_clearing_works(cfg):
    cfg.create_provider(
        "p",
        "x",
        "https://host.invalid",
        SENTINEL,
        connection_options={"headers": {"Key": SENTINEL}},
    )
    cfg.update_provider(
        "p", expected_revision=1, base_url=MASK, api_key=MASK, connection_options=MASK
    )
    assert cfg.get_provider("p").api_key.get_secret_value() == SENTINEL
    assert cfg.get_provider("p").base_url.get_secret_value() == "https://host.invalid"
    cfg.update_provider("p", expected_revision=2, api_key=None, connection_options={})
    assert cfg.get_provider("p").api_key is None
    assert cfg.get_provider("p").connection_options.get_secret_value() == {}
    cfg.create_mcp_server(
        "x",
        "stdio",
        {"command": "python", "args": [SENTINEL], "env": {"KEY": SENTINEL}},
    )
    old = cfg.get_mcp_server("x")
    safe = old.settings.model_dump(mode="json")
    cfg.update_mcp_server("x", expected_revision=1, settings=safe)
    assert cfg.get_mcp_server("x").settings.args.get_secret_value() == [SENTINEL]
    cfg.update_mcp_server(
        "x", expected_revision=2, settings={"command": MASK, "args": [], "env": {}}
    )
    assert cfg.get_mcp_server("x").settings.args.get_secret_value() == []
    with pytest.raises(ValueError):
        cfg.update_mcp_server(
            "x", expected_revision=3, settings={"command": MASK, "env": {"KEY": MASK}}
        )
    with pytest.raises(ValueError):
        cfg.create_mcp_server("new", "stdio", {"command": MASK})
    raw = cfg._db.conn.execute(
        "SELECT settings_json FROM mcp_servers WHERE name=?", ("x",)
    ).fetchone()[0]
    assert MASK not in raw


def test_explicit_export_respects_exclude_and_patch_omission(cfg):
    from agentconf import ProviderUpdate, ModelEndpointUpdate, MCPServerUpdate

    cfg.create_provider("p", "custom", "https://host.invalid", SENTINEL)
    provider = cfg.get_provider("p")
    exported = provider.model_dump(
        mode="json", context={"show_secrets": True}, exclude={"api_key"}
    )
    assert "api_key" not in exported
    patch = ProviderUpdate(api_key=None)
    values = patch.model_dump(exclude_unset=True, context={"show_secrets": True})
    assert values == {"api_key": None}
    cfg.update_provider("p", expected_revision=1, **values)
    assert cfg.get_provider("p").api_key is None
    assert ModelEndpointUpdate(capabilities_key=None).model_dump(
        exclude_unset=True
    ) == {"capabilities_key": None}
    assert MCPServerUpdate(name="new").model_dump(exclude_unset=True) == {"name": "new"}
