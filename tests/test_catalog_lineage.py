"""来源、派生、语义差异、原子搬迁和显式迁移的数据保护契约。"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
from agentconf import (
    AgentsConfig,
    CapabilityProfile,
    CatalogError,
    CatalogImportError,
    CatalogConflictError,
    CatalogReadOnlyError,
    CapabilityInUseError,
    ImportPlan,
    ModelsDevSource,
    MigrationRequiredError,
    MigrationError,
    prepare_migration,
    apply_migration,
    MigrationPlan,
)
from agentconf.catalog import fingerprint
from agentconf.storage.catalog_repo import CatalogRepository
from agentconf.storage.database import Database


@pytest.fixture
def cfg(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as config:
        yield config


def snapshot(context=120000, **fields):
    return ModelsDevSource.parse(
        json.dumps(
            {
                "v/m": {
                    "id": "v/m",
                    "name": "Model",
                    "limit": {"context": context},
                    "reasoning": False,
                    **fields,
                }
            }
        ).encode()
    )


def upstream(cfg):
    cfg.catalog_apply(cfg.catalog_preview(snapshot()))
    return cfg.catalog_get("v/m")


@pytest.mark.parametrize("key", ["zhipuai/glm-5.3", "example-VLA"])
def test_source_originals_are_readonly_including_noop(cfg, key):
    old = cfg.catalog_get(key)
    with pytest.raises(CatalogReadOnlyError):
        cfg.catalog_edit(old, expected_revision=old.revision)
    with pytest.raises(CatalogReadOnlyError):
        cfg.catalog_delete(key, expected_revision=old.revision)
    with pytest.raises(CatalogError, match="已移除"):
        cfg.catalog_set_management(key)
    assert cfg.catalog_get(key) == old


def test_derive_clone_raw_values_no_live_inheritance_and_source_labels(cfg):
    old = upstream(cfg)
    derived = cfg.catalog_derive(old.model_key, "local/tuned")
    assert derived.declaration() == old.declaration()
    assert derived.max_output_length is None
    assert derived.base_fingerprint == fingerprint(old)
    assert cfg.catalog_variants(old.model_key) == [derived]
    with pytest.raises(CatalogError):
        cfg.catalog_derive(derived.model_key, "local/nested")
    changed = CapabilityProfile.model_validate(
        {**derived.model_dump(), "max_context_length": 64000}
    )
    saved = cfg.catalog_edit(changed, expected_revision=derived.revision)
    assert saved.revision == derived.revision + 1
    cfg.create_provider("p", "openai", "https://local.invalid", "key")
    cfg.create_model_endpoint(
        "p",
        "alias",
        "request",
        capabilities_key=saved.model_key,
        api_format="openai.chat_completions",
    )
    result = cfg.resolve_model_endpoint("alias")
    assert result.capabilities.max_context_length == 64000
    assert result.capabilities.supports_reasoning is False
    assert result.capability_sources["max_context_length"] == "derived:local/tuned"
    assert result.capability_sources["max_output_length"] == "default"
    cfg.catalog_update_apply(cfg.catalog_update_preview(snapshot(256000)))
    assert cfg.catalog_stale(saved.model_key)
    assert cfg.catalog_get(saved.model_key) == saved
    assert cfg.resolve_model_endpoint("alias").capabilities.max_context_length == 64000
    rebuilt = cfg.catalog_rebuild(saved.model_key, expected_revision=saved.revision)
    assert rebuilt.declaration() == saved.declaration()
    assert not cfg.catalog_stale(saved.model_key)
    with pytest.raises(CatalogConflictError):
        cfg.catalog_edit(saved, expected_revision=saved.revision)
    local = cfg.catalog_clone("example-VLA", "local/example")
    assert local.origin == "local" and local.source == "manual"
    assert local.derived_from is None and local.base_fingerprint is None
    with pytest.raises(CatalogError):
        cfg.catalog_clone(old.model_key, "local/copy")


def test_stale_ignores_annotations_display_and_set_order_but_preserves_extra_order(cfg):
    row = upstream(cfg)
    d = cfg.catalog_derive(row.model_key, "local/d")
    update = snapshot(
        name="Renamed", description="New description", last_updated="2026-10-28"
    )
    cfg.catalog_update_apply(cfg.catalog_update_preview(update))
    new = cfg.catalog_get(row.model_key)
    assert new.annotations["description"] == "New description"
    assert not cfg.catalog_stale(d.model_key)
    a = CapabilityProfile(
        model_key="local/a",
        display_name="A",
        input_modalities=["image", "text"],
        extra={"steps": [1, 2]},
    )
    b = CapabilityProfile.model_validate(
        {**a.model_dump(), "input_modalities": ["text", "image", "text"]}
    )
    assert fingerprint(a) == fingerprint(b)
    from agentconf.catalog import declaration_changes

    assert declaration_changes(a, b) == {}
    c = CapabilityProfile.model_validate({**a.model_dump(), "extra": {"steps": [2, 1]}})
    assert fingerprint(a) != fingerprint(c)
    assert fingerprint(
        CapabilityProfile(model_key="local/null", display_name="N")
    ) != fingerprint(
        CapabilityProfile(
            model_key="local/f", display_name="F", supports_reasoning=False
        )
    )


@pytest.mark.parametrize(
    "race", ["row", "ownership", "deleted", "binding", "variant", "other_update"]
)
def test_update_conflicts_roll_back_new_entries(cfg, race):
    old = upstream(cfg)
    next_snapshot = ModelsDevSource.parse(
        b'{"a/new":{"id":"a/new","name":"New"},"v/m":{"id":"v/m","name":"Model","limit":{"context":256000}}}'
    )
    plan = cfg.catalog_update_preview(next_snapshot)
    conn = cfg._db.conn
    if race == "row":
        conn.execute(
            "UPDATE model_capabilities SET display_name='changed' WHERE model_key='v/m'"
        )
        conn.commit()
    elif race == "ownership":
        conn.execute(
            "UPDATE model_capabilities SET origin='local' WHERE model_key='v/m'"
        )
        conn.commit()
    elif race == "deleted":
        conn.execute("DELETE FROM model_capabilities WHERE model_key='v/m'")
        conn.commit()
    elif race == "binding":
        cfg.create_provider("p", "openai", "https://local.invalid", "key")
        cfg.create_model_endpoint(
            "p",
            "alias",
            "request",
            capabilities_key=old.model_key,
            api_format="openai.chat_completions",
        )
    elif race == "variant":
        cfg.catalog_derive(old.model_key, "local/d")
    else:
        cfg.catalog_update_apply(cfg.catalog_update_preview(snapshot(512000)))
    with pytest.raises(CatalogImportError, match="整批回滚"):
        cfg.catalog_update_apply(plan)
    assert not any((p.model_key == "a/new" for p in cfg.catalog_list()))


def test_local_conflict_and_atomic_relocation_with_fixed_plan(cfg, tmp_path):
    old = cfg.catalog_create(
        CapabilityProfile(
            model_key="v/m",
            display_name="User",
            max_context_length=64000,
            source="models.dev",
        )
    )
    cfg.create_provider("p", "openai", "https://local.invalid", "key")
    cfg.create_model_endpoint(
        "p",
        "alias",
        "request",
        capabilities_key=old.model_key,
        api_format="openai.chat_completions",
    )
    assert cfg.catalog_update_preview(snapshot()).protected == ["v/m"]
    assert "v/m" in cfg.catalog_preview(snapshot()).conflicts
    plan = cfg.catalog_preview(snapshot(), relocations={"v/m": "local/original"})
    path = tmp_path / "plan.json"
    plan.save(path)
    plan = ImportPlan.load(path)
    with patch(
        "agentconf.catalog.urlopen",
        side_effect=AssertionError("apply must not download"),
    ):
        cfg.catalog_apply(plan)
    assert cfg.catalog_get("v/m").origin == "upstream"
    assert cfg.catalog_get("local/original").origin == "local"
    assert cfg.resolve_model_endpoint("alias").capabilities.max_context_length == 64000
    assert cfg.get_model_endpoint("alias").capabilities_key == "local/original"
    assert cfg.catalog_apply(plan)["skipped"] == ["v/m"]
    assert cfg._db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_relocation_race_and_fk_failure_roll_back(cfg):
    old = cfg.catalog_create(CapabilityProfile(model_key="v/m", display_name="User"))
    plan = cfg.catalog_preview(snapshot(), relocations={"v/m": "local/keep"})
    cfg.catalog_edit(
        CapabilityProfile.model_validate(
            {**old.model_dump(), "display_name": "Edited"}
        ),
        expected_revision=old.revision,
    )
    with pytest.raises(CatalogImportError):
        cfg.catalog_apply(plan)
    assert cfg.catalog_get("v/m").origin == "local"
    assert not any((p.model_key == "local/keep" for p in cfg.catalog_list()))
    cfg.create_provider("p", "openai", "https://local.invalid", "key")
    cfg.create_model_endpoint(
        "p",
        "alias",
        "request",
        capabilities_key="v/m",
        api_format="openai.chat_completions",
    )
    current = cfg.catalog_get("v/m")
    cfg.catalog_relocate("v/m", "local/keep", expected_revision=current.revision)
    assert cfg.get_model_endpoint("alias").capabilities_key == "local/keep"
    with pytest.raises(CapabilityInUseError):
        cfg.catalog_delete("local/keep", expected_revision=current.revision + 1)
    with pytest.raises(CatalogConflictError):
        cfg.catalog_delete("local/keep", expected_revision=current.revision)


def test_origin_identity_and_type_cannot_be_edited(cfg):
    old = cfg.catalog_create(
        CapabilityProfile(model_key="local/a", display_name="User")
    )
    for field, value in [
        ("source", "other"),
        ("origin", "upstream"),
        ("revision", 7),
        ("model_type", "unknown"),
    ]:
        with pytest.raises((CatalogError, ValueError)):
            edited = CapabilityProfile.model_validate(
                {**old.model_dump(), field: value}
            )
            cfg.catalog_edit(edited, expected_revision=old.revision)
    assert cfg.catalog_get(old.model_key) == old
    cfg.catalog_delete(old.model_key, expected_revision=old.revision)


def old_catalog(path, version=3):
    fixture = Path(__file__).parent / f"fixtures/schema_v{version}.sql"
    with sqlite3.connect(path) as conn:
        conn.executescript(fixture.read_text())
        for key, kind, source in [
            ("v/m", "model", "models.dev"),
            ("v/manual", "model", "models.dev"),
            ("example-test", "example", "example"),
        ]:
            conn.execute(
                "INSERT INTO model_capabilities (model_key,entry_kind,display_name,model_type,max_context_length,supports_reasoning,input_modalities,extra,source,created_at,updated_at) VALUES (?,?,?,'llm',64000,0,'[\"text\"]','{\"description\":\"original\",\"custom\":false}',?,'2026-01-01','2026-01-02')",
                (key, kind, key, source),
            )
        if version == 3:
            conn.execute(
                "UPDATE model_capabilities SET managed_by='models.dev' WHERE model_key='v/m'"
            )


@pytest.mark.parametrize("version", [2, 3])
def test_readonly_prepare_explicit_migration_preserves_originals_and_json(
    tmp_path, version
):
    path = tmp_path / "old.db"
    old_catalog(path, version)
    before = path.read_bytes()
    assert len(CatalogRepository.read_only(path)) == 3
    with pytest.raises(MigrationRequiredError):
        AgentsConfig(path)
    plan = prepare_migration(path)
    assert path.read_bytes() == before and (not list(tmp_path.glob("*.backup-*")))
    planfile = tmp_path / "migration.json"
    plan.save(planfile)
    report = apply_migration(MigrationPlan.load(planfile))
    backup = Path(report["backup_path"])
    assert backup.stat().st_mode & 511 == 384
    with AgentsConfig(path) as config:
        assert len(config.catalog_list()) == 3
        assert config.catalog_get("v/m").origin == (
            "upstream" if version == 3 else "local"
        )
        assert config.catalog_get("v/manual").origin == "local"
        assert config.catalog_get("example-test").origin == "example"
        profile = config.catalog_get("v/m")
        assert profile.supports_reasoning is False
        assert profile.annotations == {"description": "original"}
        assert profile.extra == {"custom": False}
        assert profile.updated_at.isoformat().startswith("2026-01-02")
        assert config._db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version
    assert apply_migration(plan)["already_applied"]


def test_migration_changed_since_prepare_and_failure_roll_back(tmp_path):
    path = tmp_path / "old.db"
    old_catalog(path)
    plan = prepare_migration(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE model_capabilities SET display_name='changed' WHERE model_key='v/m'"
        )
    with pytest.raises(MigrationError, match="已变化"):
        apply_migration(plan)
    plan = prepare_migration(path)
    with patch.object(
        Database, "_validate_rows", side_effect=MigrationError("injected check")
    ):
        with pytest.raises(MigrationError, match="injected"):
            apply_migration(plan)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "managed_by" in {
            r[1] for r in conn.execute("PRAGMA table_info(model_capabilities)")
        }
        assert (
            conn.execute("SELECT count(*) FROM model_capabilities").fetchone()[0] == 3
        )


def test_old_normalizer_plans_and_malformed_partition_rejected(cfg):
    plan = cfg.catalog_preview(snapshot())
    raw = plan.model_dump(mode="json")
    raw["snapshot"]["normalizer_version"] = 2
    with pytest.raises(ValueError):
        ImportPlan.model_validate(raw)
    raw = plan.model_dump(mode="json")
    raw["changed"] = list(raw["added"])
    with pytest.raises(ValueError):
        ImportPlan.model_validate(raw)


def test_explicit_empty_declarations_and_annotation_conflict():
    empty = CapabilityProfile(
        model_key="local/empty", display_name="Empty", extra={}, input_modalities=[]
    )
    assert empty.declaration()["extra"] == {}
    assert empty.declaration()["input_modalities"] == []
    with pytest.raises(ValueError, match="重复且不一致"):
        CapabilityProfile(
            model_key="local/conflict",
            display_name="C",
            extra={"description": "a"},
            annotations={"description": "b"},
        )


def test_schema4_fixture_and_backup_failure_are_safe(tmp_path):
    path = tmp_path / "v4.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            (Path(__file__).parent / "fixtures/schema_v4.sql").read_text()
        )
    apply_migration(prepare_migration(path))
    with AgentsConfig(path) as config:
        assert config.catalog_list() == []
    old = tmp_path / "v3.db"
    old_catalog(old)
    plan = prepare_migration(old)
    before = old.read_bytes()
    with patch.object(
        __import__(
            "agentconf.storage.legacy", fromlist=["LegacyDatabase"]
        ).LegacyDatabase,
        "_backup",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError, match="disk full"):
            apply_migration(plan)
    assert old.read_bytes() == before


def test_copy_and_rebuild_reject_changed_preview(cfg):
    old = upstream(cfg)
    derived = cfg.catalog_derive(old.model_key, "local/d")
    diff = cfg.catalog_diff(derived.model_key)
    cfg.catalog_update_apply(cfg.catalog_update_preview(snapshot(256000)))
    with pytest.raises(CatalogConflictError):
        cfg.catalog_derive(
            old.model_key, "local/another", expected_revision=old.revision
        )
    with pytest.raises(CatalogConflictError):
        cfg.catalog_rebuild(
            derived.model_key,
            expected_revision=derived.revision,
            expected_parent_fingerprint=diff["parent_fingerprint"],
        )
    assert cfg.catalog_stale(derived.model_key)


@pytest.mark.parametrize("relocate", [False, True])
def test_recreated_same_name_binding_invalidates_frozen_plan(cfg, relocate):
    if relocate:
        cfg.catalog_create(CapabilityProfile(model_key="v/m", display_name="User"))
    else:
        upstream(cfg)
    cfg.create_provider("p", "openai", "https://local.invalid", "key")
    cfg.create_model_endpoint(
        "p",
        "same-alias",
        "request",
        capabilities_key="v/m",
        api_format="openai.chat_completions",
    )
    plan = (
        cfg.catalog_preview(snapshot(), relocations={"v/m": "local/keep"})
        if relocate
        else cfg.catalog_update_preview(snapshot(256000))
    )
    cfg.delete_model_endpoint(
        "same-alias", expected_revision=cfg.get_model_endpoint("same-alias").revision
    )
    cfg.create_model_endpoint(
        "p",
        "same-alias",
        "request",
        capabilities_key="v/m",
        api_format="openai.chat_completions",
    )
    assert cfg.catalog_references("v/m") == ["same-alias"]
    with pytest.raises(CatalogImportError, match="整批回滚"):
        (cfg.catalog_apply if relocate else cfg.catalog_update_apply)(plan)
    assert cfg.get_model_endpoint("same-alias").capabilities_key == "v/m"
