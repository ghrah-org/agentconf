"""显式升级只维护托管数据，保护手工声明与并发修改。"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agentconf import (
    AgentsConfig,
    ModelsDevSource,
    UpdatePlan,
)


@pytest.fixture
def cfg(tmp_path):
    with AgentsConfig(tmp_path / "config.db") as config:
        yield config


def snapshot(window=120000, *, extra=None, model_type=None):
    row = {
        "id": "vendor/m",
        "name": "M",
        "limit": {"context": window},
        "reasoning": False,
        "tool_call": False,
    }
    if model_type:
        row["type"] = model_type
    return ModelsDevSource.parse(
        json.dumps({"vendor/m": row, **(extra or {})}).encode()
    )


def imported(cfg):
    cfg.catalog_apply(cfg.catalog_preview(snapshot()))
    return cfg.catalog_get("vendor/m")


def test_update_fixed_snapshot_and_repeated_apply_preserve_timestamps(cfg, tmp_path):
    old = imported(cfg)
    cfg.create_provider("p", "openai", "https://local.invalid", "key")
    cfg.create_model("p", "alias", "real-request", capabilities_key=old.model_key)
    plan = cfg.catalog_update_preview(snapshot(256000))
    assert plan.operation == "update" and plan.updatable == [old.model_key]
    assert plan.references[old.model_key] == ["alias"]
    assert plan.differences[old.model_key]["max_context_length"] == {
        "old": 120000,
        "new": 256000,
    }
    assert cfg.catalog_get(old.model_key) == old
    path = tmp_path / "update.json"
    plan.save(path)
    loaded = UpdatePlan.load(path)
    with patch(
        "agentconf.catalog.urlopen", side_effect=AssertionError("接受不应重新下载")
    ):
        result = cfg.catalog_update_apply(loaded)
    assert result["updated"] == [old.model_key]
    changed = cfg.catalog_get(old.model_key)
    assert changed.created_at == old.created_at and changed.updated_at > old.updated_at
    assert cfg.resolve_model("alias").capabilities.max_context_length == 256000
    assert cfg.resolve_model("alias").model_name == "real-request"
    assert cfg.catalog_update_apply(loaded)["skipped"] == [old.model_key]
    assert cfg.catalog_get(old.model_key) == changed
    assert cfg.catalog_update_preview(snapshot(256000)).unchanged == [old.model_key]


def test_type_conflict_and_missing_rows_never_change_bound_record(cfg):
    old = imported(cfg)
    plan = cfg.catalog_update_preview(snapshot(256000, model_type="embedding"))
    assert "vendor/m" in plan.conflicts and not plan.updatable
    assert not cfg.catalog_update_apply(plan)["updated"]
    assert cfg.catalog_get(old.model_key) == old
    other = ModelsDevSource.parse(
        b'{"vendor/other":{"id":"vendor/other","name":"Other"}}'
    )
    plan = cfg.catalog_update_preview(other)
    assert old.model_key in plan.missing
    cfg.catalog_update_apply(plan)
    assert cfg.catalog_get(old.model_key) == old


def test_update_selected_keys_types_and_import_plan_isolation(cfg):
    imported(cfg)
    rows = {
        "v/e": {
            "id": "v/e",
            "name": "Embedding",
            "type": "embedding",
            "limit": {"context": 8192},
        }
    }
    plan = cfg.catalog_update_preview(snapshot(256000, extra=rows))
    assert cfg.catalog_update_apply(plan, types=["embedding"])["added"] == ["v/e"]
    assert cfg.catalog_get("vendor/m").max_context_length == 120000
    assert cfg.catalog_update_apply(plan, ["vendor/m"])["updated"] == ["vendor/m"]
    with pytest.raises(ValueError):
        cfg.catalog_apply(plan)
    with pytest.raises(ValueError):
        cfg.catalog_update_apply(cfg.catalog_preview(snapshot()))


def schema_v2(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            (Path(__file__).parent / "fixtures/schema_v2.sql").read_text()
        )
        conn.execute(
            "INSERT INTO model_capabilities (model_key,entry_kind,display_name,model_type,max_context_length,source,source_ref,created_at,updated_at) VALUES ('vendor/m','model','Previously edited?','llm',64000,'models.dev','vendor/m','2026-01-01','2026-01-02')"
        )
