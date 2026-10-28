"""能力档案与显式绑定的业务约束。"""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agentconf import (
    AgentsConfig,
    CapabilityProfile,
    CapabilityInUseError,
    CapabilityNotFoundError,
    CapabilityTypeMismatchError,
    CatalogError,
    CatalogImportError,
    ImportPlan,
    LLMCapabilities,
    ModelAlreadyExistsError,
    ModelsDevSource,
)
from agentconf.catalog import revision


@pytest.fixture
def config(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as cfg:
        cfg.create_provider("p", "custom", "https://local.invalid/v1", "test-key")
        yield cfg


def snapshot(rows=None):
    rows = rows or {
        "vendor/new": {
            "id": "vendor/new",
            "name": "New",
            "limit": {"context": 120000},
            "tool_call": False,
        }
    }
    return ModelsDevSource.parse(json.dumps(rows).encode())


def test_offline_seed_once_and_no_fake_connections(tmp_path):
    path = tmp_path / "config.db"
    with patch(
        "agentconf.catalog.urlopen", side_effect=AssertionError("初始化不可联网")
    ):
        with AgentsConfig(path) as cfg:
            assert len(cfg.catalog_list(origin="upstream")) > 500
            examples = cfg.catalog_list(origin="example")
            assert {p.model_key for p in examples} >= {
                "example-VLA",
                "example-embedding",
                "example-TTS",
                "example-STT",
                "example-LLM",
                "example-rerank",
                "example-imagegen",
            }
            assert cfg.list_providers() == cfg.list_models() == cfg.list_agents() == []
            cfg._db.conn.execute("DELETE FROM model_capabilities")
            cfg._db.conn.commit()
        with AgentsConfig(path) as cfg:
            assert cfg.catalog_list() == []
            assert cfg._db.conn.execute("PRAGMA user_version").fetchone()[0] == 4


@pytest.mark.parametrize(
    "key,typ,field,value",
    [
        ("example-LLM", "llm", "max_output_length", 128000),
        ("example-VLA", "llm", "max_output_length", 242000),
        ("example-embedding", "embedding", "dimensions", 1536),
        ("example-rerank", "rerank", "top_n", 10),
        ("example-TTS", "tts", "languages", ["zh", "en"]),
        ("example-STT", "asr", "input_modalities", ["audio"]),
        ("example-imagegen", "image_gen", "supported_sizes", ["1024x1024"]),
    ],
)
def test_examples_resolve_all_types_without_changing_request_name(
    config, key, typ, field, value
):
    binding = config.create_model(
        "p", "m", "actual-request-id", model_type=typ, capabilities_key=key
    )
    config.create_agent("a", "m", max_tokens=100)
    resolved = config.resolve_agent("a")
    caps = resolved.model.capabilities
    assert caps.model_dump(mode="json")[field] == value
    assert resolved.model.model_name == binding.model_name == "actual-request-id"
    assert resolved.model.capabilities_key == key
    assert resolved.model.capability_sources[field] == "example"
    assert resolved.max_tokens == 100
    assert not hasattr(binding, "capabilities")
    if typ != "llm":
        assert "max_context_length" not in caps.model_dump()
        assert config.find_models_by_capability("supports_function_calling") == []


@pytest.mark.parametrize(
    "typ",
    [
        "llm",
        "embedding",
        "rerank",
        "tts",
        "asr",
        "image_gen",
        "video",
        "realtime",
        "unknown",
    ],
)
def test_unbound_type_defaults_do_not_infer_name(config, typ):
    config.create_model("p", "m", "example-VLA", model_type=typ)
    result = config.resolve_model("m")
    assert result.capabilities is not None
    assert result.capabilities_key is None
    assert set(result.capability_sources.values()) == {"default"}
    if typ == "llm":
        assert result.capabilities.max_context_length == 1000000
        assert result.capabilities.supports_reasoning is True
        assert result.capabilities.supports_function_calling is True
        assert result.capabilities.supports_vision is False


def test_unknown_is_not_false_and_effective_values_never_written(config):
    profile = CapabilityProfile(
        model_key="local/partial", display_name="Partial", supports_reasoning=False
    )
    config.catalog_create(profile)
    config.create_model("p", "m", "deployment", capabilities_key=profile.model_key)
    before = config.catalog_get(profile.model_key)
    result = config.resolve_model("m")
    assert result.capabilities.supports_reasoning is False
    assert result.capabilities.max_context_length == 1000000
    assert result.capability_sources["supports_reasoning"] == "manual"
    assert result.capability_sources["max_context_length"] == "default"
    assert config.catalog_get(profile.model_key) == before
    assert before.max_context_length is None
    raw = config._db.conn.execute(
        "SELECT supports_reasoning, max_context_length FROM model_capabilities WHERE model_key=?",
        (profile.model_key,),
    ).fetchone()
    assert tuple(raw) == (0, None)


def test_explicit_capability_objects_do_not_freeze_defaults():
    p = CapabilityProfile.from_capabilities(
        model_key="local/m",
        display_name="M",
        model_type="llm",
        capabilities=LLMCapabilities(supports_vision=False),
    )
    assert p.declaration() == {"supports_vision": False}
    with pytest.raises(ValueError):
        CapabilityProfile.from_capabilities(
            model_key="local/e",
            display_name="E",
            model_type="embedding",
            capabilities=LLMCapabilities(),
        )


@pytest.mark.parametrize(
    "values",
    [
        {"supports_reasoning": 1},
        {"max_context_length": True},
        {"max_context_length": -1},
        {"input_modalities": ["unknown"]},
        {"dimensions": 1536},
        {"extra": {"supports_reasoning": True}},
        {"entry_kind": "example"},
        {"source": "example"},
        {"model_key": "example-fake"},
        {"unsupported": 123},
    ],
)
def test_invalid_profiles_rejected(values):
    with pytest.raises(ValidationError):
        CapabilityProfile.model_validate(
            {"model_key": "local/test", "display_name": "Test", **values}
        )


def test_binding_constraints_clear_rebind_and_cascades(config):
    config.create_model("p", "m", "deployment", capabilities_key="example-VLA")
    config.create_agent("a", "m")
    with pytest.raises(CapabilityInUseError):
        config.catalog_delete(
            "example-VLA", expected_revision=config.catalog_get("example-VLA").revision
        )
    with pytest.raises(CapabilityTypeMismatchError):
        config.update_model("m", model_type="embedding")
    assert config.get_model("m").model_type == "llm"
    with pytest.raises(CapabilityNotFoundError):
        config.update_model("m", capabilities_key="missing")
    config.update_model(
        "m", model_type="embedding", capabilities_key="example-embedding"
    )
    assert config.resolve_agent("a").model.capabilities.dimensions == 1536
    config.update_model("m", capabilities_key=None)
    assert config.resolve_model("m").capabilities.dimensions is None
    config.create_provider("p2", "openai", "https://other.invalid", "key2")
    with pytest.raises(ModelAlreadyExistsError):
        config.create_model("p2", "m", "other")
    config.delete_provider("p")
    assert config.list_agents() == config.list_models() == []
    assert config.catalog_get("example-VLA").max_output_length == 242000


@pytest.mark.parametrize(
    "field",
    [
        "base_url",
        "api_key",
        "capabilities",
        "metadata_provider",
        "metadata_model",
        "provider_name",
        "typo",
    ],
)
def test_removed_update_fields_are_not_silently_ignored(config, field):
    config.create_model("p", "m", "remote")
    with pytest.raises(TypeError):
        config.update_model("m", **{field: None})


@pytest.mark.parametrize(
    "field",
    ["base_url", "api_key", "capabilities", "metadata_provider", "metadata_model"],
)
def test_removed_create_fields_rejected(config, field):
    with pytest.raises(TypeError):
        config.create_model("p", "m", "remote", **{field: None})


def test_effective_filter_all_types_and_reject_unknown(config):
    for name, typ, key in [
        ("llm", "llm", "example-VLA"),
        ("tts", "tts", "example-TTS"),
        ("embedding", "embedding", "example-embedding"),
    ]:
        config.create_model("p", name, name, model_type=typ, capabilities_key=key)
    assert [
        m.name for m in config.find_models_by_capability("input_modality:image")
    ] == ["llm"]
    assert [
        m.name for m in config.find_models_by_capability("output_modality:audio")
    ] == ["tts"]
    assert [
        m.name for m in config.find_models_by_capability("output_modality:embedding")
    ] == ["embedding"]
    with pytest.raises(ValueError):
        config.find_models_by_capability("unknown")
    with pytest.raises(ValueError):
        config.find_models_by_capability("output_modality:invalid")


def test_manual_edit_reference_impact_revision_and_immutable_type(config):
    old = config.catalog_clone("example-VLA", "local/vla")
    config.create_model("p", "alias1", "actual-1", capabilities_key=old.model_key)
    config.create_model("p", "alias2", "actual-2", capabilities_key=old.model_key)
    assert config.catalog_references(old.model_key) == ["alias1", "alias2"]
    updated = CapabilityProfile.model_validate(
        {**old.model_dump(), "max_output_length": 120000}
    )
    saved = config.catalog_edit(updated, expected_revision=revision(old))
    assert saved.created_at == old.created_at
    assert saved.updated_at > old.updated_at
    assert config.resolve_model("alias2").capabilities.max_output_length == 120000
    with pytest.raises(CatalogError, match="已被修改"):
        config.catalog_edit(updated, expected_revision=revision(old))
    different_type = CapabilityProfile(
        model_key=old.model_key,
        display_name="Changed type",
        model_type="tts",
        source="manual",
    )
    with pytest.raises(CatalogError, match="不可变"):
        config.catalog_edit(different_type, expected_revision=revision(saved))


def test_import_preview_saved_plan_idempotent_no_timestamp_change(config, tmp_path):
    plan = config.catalog_preview(snapshot())
    assert plan.added == ["vendor/new"]
    assert not any(p.model_key == "vendor/new" for p in config.catalog_list())
    path = tmp_path / "plan.json"
    plan.save(path)
    loaded = ImportPlan.load(path)
    assert loaded == plan
    assert config.catalog_apply(loaded)["added"] == ["vendor/new"]
    before = config.catalog_get("vendor/new")
    assert config.catalog_apply(loaded)["skipped"] == ["vendor/new"]
    assert config.catalog_get("vendor/new") == before
    again = config.catalog_preview(snapshot())
    assert again.unchanged == ["vendor/new"]
    assert config.catalog_apply(again)["added"] == []
    assert config.catalog_get("vendor/new").updated_at == before.updated_at


def test_import_existing_differences_and_missing_are_retained(config):
    config.catalog_apply(config.catalog_preview(snapshot()))
    original = config.catalog_get("vendor/new")
    config.create_model("p", "m", "remote-alias", capabilities_key=original.model_key)
    rows = {
        "vendor/new": {
            "id": "vendor/new",
            "name": "Changed",
            "limit": {"context": 50000},
        },
        "vendor/other": {"id": "vendor/other", "name": "Other"},
    }
    plan = config.catalog_preview(snapshot(rows))
    assert plan.changed == ["vendor/new"]
    assert plan.differences["vendor/new"]["max_context_length"] == {
        "old": 120000,
        "new": 50000,
    }
    with pytest.raises(CatalogImportError):
        config.catalog_apply(plan, ["vendor/new"])
    config.catalog_apply(plan)
    assert config.catalog_get("vendor/new") == original
    assert config.resolve_model("m").capabilities.max_context_length == 120000
    missing_plan = config.catalog_preview(
        snapshot({"vendor/other": rows["vendor/other"]})
    )
    assert "vendor/new" in missing_plan.missing
    config.catalog_apply(missing_plan)
    assert config.catalog_get("vendor/new") == original


def test_import_manual_protection_selected_types_and_atomic_race_rollback(config):
    rows = {
        "a/new": {"id": "a/new", "name": "A"},
        "z/new": {
            "id": "z/new",
            "name": "Z",
            "type": "embedding",
            "limit": {"context": 4000},
        },
    }
    plan = config.catalog_preview(snapshot(rows))
    config.catalog_create(
        CapabilityProfile(
            model_key="z/new", display_name="My correction", model_type="embedding"
        )
    )
    with pytest.raises(CatalogImportError, match="整批回滚"):
        config.catalog_apply(plan)
    with pytest.raises(CapabilityNotFoundError):
        config.catalog_get("a/new")
    result = config.catalog_apply(plan, types=["llm"])
    assert result["added"] == ["a/new"]
    assert config.catalog_get("z/new").display_name == "My correction"
    assert "z/new" in config.catalog_preview(snapshot(rows)).conflicts


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"[]",
        b"invalid",
        b'{"example-X":{"id":"example-X","name":"X"}}',
        b'{"x":{"id":"y","name":"X"}}',
        b'{"x":{"id":"x","tool_call":"yes"}}',
    ],
)
def test_invalid_download_never_mutates_catalog(config, raw):
    before = config.catalog_list()
    with pytest.raises(CatalogImportError):
        config.catalog_preview(ModelsDevSource.parse(raw))
    assert config.catalog_list() == before


def test_source_timeout_and_size_limits():
    with patch("agentconf.catalog.urlopen", side_effect=TimeoutError("test timeout")):
        with pytest.raises(CatalogImportError, match="timeout"):
            ModelsDevSource().fetch()
    from unittest.mock import MagicMock

    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"x" * 11
    with patch("agentconf.catalog.urlopen", return_value=response):
        with pytest.raises(CatalogImportError, match="大小限制"):
            ModelsDevSource(max_bytes=10).fetch()


def test_all_upstream_types_and_semantic_modality_normalization():
    rows = {
        typ: {
            "id": typ,
            "type": typ,
            "name": typ,
            "limit": {"context": 8192, "output": 1536},
            "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
        }
        for typ in (
            "chat",
            "embedding",
            "reranking",
            "speech",
            "transcription",
            "image",
            "video",
            "realtime",
            "future",
        )
    }
    profiles = {p.model_key: p for p in snapshot(rows).profiles}
    assert profiles["chat"].model_type == "llm"
    assert profiles["chat"].input_modalities == ["text", "image", "document_pdf"]
    assert profiles["embedding"].output_modalities == ["embedding"]
    assert profiles["embedding"].dimensions is None
    assert profiles["reranking"].output_modalities == ["score"]
    assert profiles["speech"].output_modalities == ["audio"]
    assert profiles["transcription"].input_modalities == ["audio"]
    assert profiles["image"].output_modalities == ["image"]
    assert profiles["future"].to_capabilities()[0].capabilities_type == "generic"


def test_two_clients_accept_same_snapshot_and_initialize_safely(tmp_path):
    path = tmp_path / "parallel.db"

    def open_count(_):
        with AgentsConfig(path) as cfg:
            return len(cfg.catalog_list())

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(open_count, range(2)))) == 1
    with AgentsConfig(path) as cfg:
        plan = cfg.catalog_preview(snapshot())

    def apply(_):
        with AgentsConfig(path) as cfg:
            return cfg.catalog_apply(plan)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, range(2)))
    assert sum(len(r["added"]) for r in results) == 1
    assert sum(len(r["skipped"]) for r in results) == 1


def test_database_checks_boolean_lengths_and_restrict_references(config):
    import sqlite3

    conn = config._db.conn
    for sql in (
        "UPDATE model_capabilities SET supports_reasoning=2 WHERE model_key='example-VLA'",
        "UPDATE model_capabilities SET max_context_length=-1 WHERE model_key='example-VLA'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(sql)
    config.create_model("p", "m", "remote", capabilities_key="example-VLA")
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("DELETE FROM model_capabilities WHERE model_key='example-VLA'")
    assert config.resolve_model("m").capabilities.max_output_length == 242000
