"""无头交互验证：筛选、绑定、清除档案与显式确认。"""

import asyncio
from unittest.mock import patch

from textual.widgets import DataTable, Input, Select, TabbedContent, TextArea

from agentconf import AgentsConfig
from agentconf.catalog import ModelsDevSource
from agentconf.tui.app import agentconfApp
from agentconf.tui.screens.catalog_screens import ImportPreviewDialog, ProfileEditDialog
from agentconf.tui.screens.model_screens import ModelCreateDialog, ModelUpdateDialog


def run_ui(coro):
    # 独立事件循环，避免 Textual 的清理任务污染其它同步 SDK 测试。
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def test_catalog_filter_model_binding_clear_and_delete_cancel(tmp_path):
    path = tmp_path / "config.db"
    with AgentsConfig(path) as cfg:
        cfg.create_provider("p", "openai", "https://local.invalid", "test-key")

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = app.query_one("#catalog-table", DataTable)
            assert screen.row_count > 500
            app.query_one("#catalog-kind", Select).value = "example"
            app.query_one("#catalog-filter", Input).value = "VLA"
            await pilot.pause()
            assert screen.row_count == 1
            app.push_screen(ModelCreateDialog())
            await pilot.pause()
            app.screen.query_one("#name", Input).value = "alias"
            app.screen.query_one("#model_name", Input).value = "real-deployment"
            app.screen.query_one("#profile", Select).value = "example-VLA"
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.resolve_model("alias").model_name == "real-deployment"
            assert app.config.get_model("alias").capabilities_key == "example-VLA"
            app.push_screen(ModelUpdateDialog("alias"))
            await pilot.pause()
            assert app.screen.query_one("#profile", Select).value == "example-VLA"
            assert not app.screen.query("#provider")
            app.screen.query_one("#profile", Select).value = Select.NULL
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.get_model("alias").capabilities_key is None
            # 取消删除不能触发级联删除。
            app.query_one(TabbedContent).active = "tab-model"
            model_screen = app.query_one("#model-table").parent
            model_screen._refresh()
            model_screen.action_delete()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.config.get_model("alias").model_name == "real-deployment"

    run_ui(scenario())


def test_edit_requires_preview_and_import_is_explicit(tmp_path):
    path = tmp_path / "config.db"

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            app.config.catalog_clone("example-VLA", "local/vla")
            app.push_screen(ProfileEditDialog("local/vla"))
            await pilot.pause()
            old = app.config.catalog_get("local/vla")
            value = old.model_dump_json().replace(
                '"max_output_length":242000', '"max_output_length":128000'
            )
            app.screen.query_one(TextArea).load_text(value)
            app.screen.on_confirm()
            assert app.config.catalog_get("local/vla") == old
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("local/vla").max_output_length == 128000
            snapshot = ModelsDevSource.parse(b'{"v/new":{"id":"v/new","name":"New"}}')
            plan = app.config.catalog_preview(snapshot)
            app.push_screen(ImportPreviewDialog(plan))
            await pilot.pause()
            app.screen.query_one("#import-keys", Input).value = "v/new"
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("v/new").source == "models.dev"

    with patch(
        "agentconf.catalog.urlopen", side_effect=AssertionError("未显式请求下载")
    ):
        run_ui(scenario())


def test_background_import_only_previews_and_new_profile_can_be_saved(tmp_path):
    path = tmp_path / "config.db"
    snapshot = ModelsDevSource.parse(b'{"v/new":{"id":"v/new","name":"New"}}')

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(140, 60)) as pilot:
            await pilot.pause()
            from agentconf.tui.screens.catalog_screens import CatalogScreen

            app.query_one(CatalogScreen).action_import()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, ImportPreviewDialog)
            assert "v/new" in app.screen.plan.added
            await pilot.press("escape")
            await pilot.pause()
            assert not any(p.model_key == "v/new" for p in app.config.catalog_list())
            app.push_screen(ProfileEditDialog())
            await pilot.pause()
            app.screen.query_one(TextArea).load_text(
                '{"model_key":"local/new","display_name":"New","max_context_length":200000}'
            )
            app.screen.on_confirm()
            assert not any(
                p.model_key == "local/new" for p in app.config.catalog_list()
            )
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("local/new").max_context_length == 200000

    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=snapshot):
        run_ui(scenario())


def test_manual_update_preview_and_ownership_dialog(tmp_path):
    from agentconf.catalog import UpdatePlan
    from agentconf.tui.screens.catalog_screens import (
        CatalogScreen,
        ProfileCopyDialog,
    )

    path = tmp_path / "config.db"
    old_snapshot = ModelsDevSource.parse(
        b'{"v/m":{"id":"v/m","name":"M","limit":{"context":120000}}}'
    )
    next_snapshot = ModelsDevSource.parse(
        b'{"v/m":{"id":"v/m","name":"M","limit":{"context":256000}}}'
    )
    with AgentsConfig(path) as cfg:
        cfg.catalog_apply(cfg.catalog_preview(old_snapshot))

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            app.query_one(CatalogScreen).action_import(update=True)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen.plan, UpdatePlan)
            assert app.config.catalog_get("v/m").max_context_length == 120000
            app.screen.query_one("#import-keys", Input).value = "v/m"
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("v/m").max_context_length == 256000
            app.push_screen(ProfileCopyDialog("v/m"))
            await pilot.pause()
            app.screen.query_one("#copy-key", Input).value = "local/m"
            app.screen.on_confirm()
            assert app.config.catalog_get("v/m").origin == "upstream"
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("local/m").origin == "derived"

    with patch("agentconf.catalog.ModelsDevSource.fetch", return_value=next_snapshot):
        run_ui(scenario())


def test_catalog_copy_edit_delete_rebuild_and_migration_help(tmp_path):
    from agentconf import CapabilityProfile
    from agentconf.tui.screens.catalog_screens import ProfileRebuildDialog
    from test_catalog_lineage import old_catalog

    path = tmp_path / "ui.db"

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(120, 55)) as pilot:
            await pilot.pause()
            app.query_one("#catalog-kind", Select).value = "example"
            app.query_one("#catalog-filter", Input).value = "VLA"
            await pilot.pause()
            assert await pilot.click("#catalog-copy")
            await pilot.pause()
            app.screen.query_one("#copy-key", Input).value = "local/vla"
            app.screen.on_confirm()
            assert not any(
                p.model_key == "local/vla" for p in app.config.catalog_list()
            )
            await pilot.press("escape")
            await pilot.pause()
            assert not any(
                p.model_key == "local/vla" for p in app.config.catalog_list()
            )
            assert await pilot.click("#catalog-copy")
            await pilot.pause()
            app.screen.query_one("#copy-key", Input).value = "local/vla"
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("local/vla").origin == "local"
            app.config.catalog_create(
                CapabilityProfile(model_key="local/delete", display_name="Delete")
            )
            app.query_one("#catalog-kind", Select).value = "local"
            app.query_one("#catalog-filter", Input).value = "local/delete"
            await pilot.pause()
            assert await pilot.click("#catalog-delete")
            await pilot.pause()
            await pilot.click("#btn-confirm")
            await pilot.pause()
            assert not any(
                p.model_key == "local/delete" for p in app.config.catalog_list()
            )
            snapshot = ModelsDevSource.parse(
                b'{"v/m":{"id":"v/m","name":"M","limit":{"context":120000}}}'
            )
            app.config.catalog_apply(app.config.catalog_preview(snapshot))
            app.config.catalog_derive("v/m", "local/d")
            app.config.catalog_update_apply(
                app.config.catalog_update_preview(
                    ModelsDevSource.parse(
                        b'{"v/m":{"id":"v/m","name":"M","limit":{"context":256000}}}'
                    )
                )
            )
            app.push_screen(ProfileRebuildDialog("local/d"))
            await pilot.pause()
            app.screen.on_confirm()
            await pilot.pause()
            assert not app.config.catalog_stale("local/d")
            assert app.config.catalog_get("local/d").max_context_length == 120000

    run_ui(scenario())
    old = tmp_path / "old.db"
    old_catalog(old)
    before = old.read_bytes()

    async def help_page():
        app = agentconfApp(str(old))
        async with app.run_test(size=(120, 55)) as pilot:
            await pilot.pause()
            assert not app.query("#catalog-table")
            assert await pilot.click("#migration-preview")
            await pilot.pause()
            assert "source_version" in app.query_one("#migration-report", TextArea).text

    run_ui(help_page())
    assert old.read_bytes() == before
