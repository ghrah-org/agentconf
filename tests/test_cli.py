"""命令行预览不打开写库，应用固定预览快照。"""

import json
from unittest.mock import patch

import pytest

from agentconf import AgentsConfig, CapabilityProfile
from agentconf.catalog import ModelsDevSource, prepare_import, revision
from agentconf.tui import main


def test_import_default_preview_does_not_create_database_or_parent(tmp_path, capsys):
    path = tmp_path / "absent" / "config.db"
    planfile = tmp_path / "preview.json"
    snapshot = ModelsDevSource.parse(b'{"vendor/new":{"id":"vendor/new","name":"New"}}')
    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=snapshot):
        main(["catalog", "import", "--db-path", str(path), "--plan-out", str(planfile)])
    report = json.loads(capsys.readouterr().out)
    assert report["added"] == ["vendor/new"]
    assert not path.exists() and not path.parent.exists()
    assert planfile.exists()


def test_selected_apply_never_redownloads_and_only_adds_selection(tmp_path, capsys):
    path = tmp_path / "config.db"
    with AgentsConfig(path) as cfg:
        existing = cfg.catalog_list()
    snapshot = ModelsDevSource.parse(
        b'{"v/a":{"id":"v/a","name":"A"},"v/b":{"id":"v/b","name":"B"}}'
    )
    planfile = tmp_path / "plan.json"
    prepare_import(snapshot, existing).save(planfile)
    with patch(
        "agentconf.catalog.urlopen", side_effect=AssertionError("应用不能再次下载")
    ):
        main(
            [
                "--db-path",
                str(path),
                "catalog",
                "import",
                "--plan",
                str(planfile),
                "--apply",
                "v/a",
            ]
        )
    assert json.loads(capsys.readouterr().out)["added"] == ["v/a"]
    with AgentsConfig(path) as cfg:
        assert {p.model_key for p in cfg.catalog_list()} == {
            p.model_key for p in existing
        } | {"v/a"}
    main(
        [
            "catalog",
            "list",
            "--db-path",
            str(path),
            "--origin",
            "example",
            "--type",
            "embedding",
        ]
    )
    profiles = json.loads(capsys.readouterr().out)
    assert [p["model_key"] for p in profiles] == ["example-embedding"]


def test_edit_preview_reference_impact_and_stale_revision(tmp_path, capsys):
    path = tmp_path / "config.db"
    with AgentsConfig(path) as cfg:
        old = cfg.catalog_clone("example-VLA", "local/vla")
        cfg.create_provider("p", "openai", "https://local.invalid", "key")
        cfg.create_model("p", "alias", "deployment", capabilities_key=old.model_key)
    edited = CapabilityProfile.model_validate(
        {**old.model_dump(), "max_context_length": 512000}
    )
    editfile = tmp_path / "edit.json"
    editfile.write_text(edited.model_dump_json())
    args = [
        "catalog",
        "edit",
        old.model_key,
        "--db-path",
        str(path),
        "--file",
        str(editfile),
    ]
    before = path.read_bytes()
    main(args)
    report = json.loads(capsys.readouterr().out)
    assert report["old"]["max_context_length"] == 1000000
    assert report["new"]["max_context_length"] == 512000
    assert report["references"] == ["alias"]
    assert path.read_bytes() == before
    main(args + ["--expected-revision", str(revision(old)), "--apply"])
    capsys.readouterr()
    with AgentsConfig(path) as cfg:
        assert cfg.resolve_model("alias").capabilities.max_context_length == 512000
    with pytest.raises(SystemExit) as caught:
        main(args + ["--expected-revision", str(revision(old)), "--apply"])
    assert caught.value.code == 1


def test_removed_flags_rejected(tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["--offline", "--db-path", str(tmp_path / "no.db")])
    assert caught.value.code == 2


def test_update_cli_download_preview_then_apply_fixed_plan(tmp_path, capsys):
    path = tmp_path / "config.db"
    snapshot = ModelsDevSource.parse(
        b'{"v/m":{"id":"v/m","name":"M","limit":{"context":120000}}}'
    )
    with AgentsConfig(path) as cfg:
        cfg.catalog_apply(cfg.catalog_preview(snapshot))
    updated = ModelsDevSource.parse(
        b'{"v/m":{"id":"v/m","name":"M","limit":{"context":256000}},"v/new":{"id":"v/new","name":"New"}}'
    )
    planfile = tmp_path / "update.json"
    before = path.read_bytes()
    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=updated):
        main(["catalog", "update", "--db-path", str(path), "--plan-out", str(planfile)])
    report = json.loads(capsys.readouterr().out)
    assert report["updatable"] == ["v/m"] and report["added"] == ["v/new"]
    assert path.read_bytes() == before
    with patch("agentconf.catalog.urlopen", side_effect=AssertionError("应用不能下载")):
        main(
            [
                "catalog",
                "update",
                "--db-path",
                str(path),
                "--plan",
                str(planfile),
                "--apply-all",
            ]
        )
    assert json.loads(capsys.readouterr().out)["updated"] == ["v/m"]
    with AgentsConfig(path) as cfg:
        assert cfg.catalog_get("v/m").max_context_length == 256000
    # 原件不能再交出维护权；人工调整需创建派生。
    with pytest.raises(SystemExit):
        main(["catalog", "manage", "v/m", "--manual", "--db-path", str(path)])
    main(
        [
            "catalog",
            "derive",
            "v/m",
            "--as",
            "local/m",
            "--db-path",
            str(path),
            "--apply",
        ]
    )
    assert json.loads(capsys.readouterr().out)["saved"] == "local/m"
    with AgentsConfig(path) as cfg:
        assert cfg.catalog_get("v/m").origin == "upstream"
        assert cfg.catalog_get("local/m").origin == "derived"


def test_update_cli_preview_does_not_create_database(tmp_path, capsys):
    path = tmp_path / "absent" / "config.db"
    snapshot = ModelsDevSource.parse(b'{"v/m":{"id":"v/m","name":"M"}}')
    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=snapshot):
        main(["catalog", "update", "--db-path", str(path)])
    report = json.loads(capsys.readouterr().out)
    assert report["added"] == ["v/m"] and report["updatable"] == []
    assert not path.parent.exists()


def test_catalog_crud_lineage_and_migration_commands(tmp_path, capsys):
    from agentconf import prepare_migration
    from test_catalog_lineage import old_catalog

    path = tmp_path / "old.db"
    old_catalog(path)
    before = path.read_bytes()
    planfile = tmp_path / "migration.json"
    main(["migrate", "prepare", "--db-path", str(path), "--plan-out", str(planfile)])
    report = json.loads(capsys.readouterr().out)
    assert report["source_version"] == 3
    assert path.read_bytes() == before
    main(["migrate", "apply", "--plan", str(planfile)])
    assert json.loads(capsys.readouterr().out)["schema_version"] == 4
    base = ["--db-path", str(path), "catalog"]
    main(base + ["derive", "v/m", "--as", "local/tuned"])
    assert json.loads(capsys.readouterr().out)["new_key"] == "local/tuned"
    with AgentsConfig(path) as cfg:
        assert not any(p.model_key == "local/tuned" for p in cfg.catalog_list())
    main(base + ["derive", "v/m", "--as", "local/tuned", "--apply"])
    assert json.loads(capsys.readouterr().out)["saved"] == "local/tuned"
    main(base + ["variants", "v/m"])
    assert json.loads(capsys.readouterr().out)[0]["model_key"] == "local/tuned"
    main(base + ["diff", "local/tuned"])
    assert json.loads(capsys.readouterr().out)["stale"] is False
    main(base + ["rebuild", "local/tuned", "--expected-revision", "1", "--apply"])
    capsys.readouterr()
    main(base + ["delete", "local/tuned", "--expected-revision", "2", "--apply"])
    assert json.loads(capsys.readouterr().out)["deleted"] == "local/tuned"
    main(base + ["clone", "example-test", "--as", "local/example", "--apply"])
    capsys.readouterr()
    main(
        base
        + [
            "relocate",
            "local/example",
            "--as",
            "local/new",
            "--expected-revision",
            "1",
            "--apply",
        ]
    )
    assert json.loads(capsys.readouterr().out)["saved"] == "local/new"
    with pytest.raises(Exception):
        prepare_migration(path)  # 当前库不会再次迁移


def test_diff_without_key_and_readonly_edit_are_safe(tmp_path, capsys):
    path = tmp_path / "config.db"
    with AgentsConfig(path) as cfg:
        profile = cfg.catalog_get("example-VLA")
    before = path.read_bytes()
    snapshot = ModelsDevSource.parse(b'{"v/new":{"id":"v/new","name":"New"}}')
    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=snapshot):
        main(["catalog", "diff", "--db-path", str(path), "--source", "models.dev"])
    assert json.loads(capsys.readouterr().out)["added"] == ["v/new"]
    file = tmp_path / "example.json"
    file.write_text(profile.model_dump_json())
    with pytest.raises(SystemExit) as error:
        main(
            [
                "catalog",
                "edit",
                "example-VLA",
                "--db-path",
                str(path),
                "--file",
                str(file),
            ]
        )
    assert error.value.code == 1
    assert "只读" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_type_alias_filter_matches_applied_type(tmp_path, capsys):
    path = tmp_path / "missing.db"
    snapshot = ModelsDevSource.parse(
        b'{"v/stt":{"id":"v/stt","name":"STT","type":"transcription"},"v/llm":{"id":"v/llm","name":"LLM"}}'
    )
    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=snapshot):
        main(["catalog", "import", "--db-path", str(path), "--types", "asr"])
    report = json.loads(capsys.readouterr().out)
    assert report["added"] == ["v/stt"]
    assert not path.exists()
