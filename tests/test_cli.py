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
            "--kind",
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
        old = cfg.catalog_get("example-VLA")
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
    main(args + ["--expected-revision", revision(old), "--apply"])
    capsys.readouterr()
    with AgentsConfig(path) as cfg:
        assert cfg.resolve_model("alias").capabilities.max_context_length == 512000
    with pytest.raises(SystemExit) as caught:
        main(args + ["--expected-revision", revision(old), "--apply"])
    assert caught.value.code == 1


def test_removed_flags_rejected(tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(["--offline", "--db-path", str(tmp_path / "no.db")])
    assert caught.value.code == 2
