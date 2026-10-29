"""实际按钮交互：三资源全入口、掩码编辑、MCP 表单与目录功能。"""

import asyncio
import json
from textual.widgets import Button, Input, TextArea, Select, TabbedContent, DataTable
from agentconf.tui.app import agentconfApp
from agentconf.tui.screens.resource_screens import ResourceEditDialog
from agentconf.tui.screens.catalog_screens import ProfileEditDialog
from test_catalog_lineage import old_catalog

SECRET = "UI-PRIVATE-SENTINEL"


def run_ui(coro):
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def test_provider_endpoint_and_mcp_create_preview_edit_resolve_cancel_delete(tmp_path):
    path = tmp_path / "config.db"

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(130, 60)) as pilot:
            await pilot.pause()
            assert await pilot.click("#provider-create")
            await pilot.pause()
            app.screen.query_one("#res-name", Input).value = "gateway"
            app.screen.query_one("#provider-type", Input).value = "future-provider"
            app.screen.query_one("#provider-url", Input).value = (
                "https://host.invalid/?token=" + SECRET
            )
            app.screen.query_one("#provider-key", Input).value = SECRET
            app.screen.on_confirm()
            assert app.config.list_providers() == []
            assert (
                SECRET not in app.screen.query_one("#resource-preview", TextArea).text
            )
            app.screen.on_confirm()
            await pilot.pause()
            assert (
                app.config.get_provider("gateway").api_key.get_secret_value() == SECRET
            )
            await pilot.click("#--content-tab-tab-endpoint")
            await pilot.pause(0.1)
            assert await pilot.click("#endpoint-create")
            await pilot.pause()
            app.screen.query_one("#res-name", Input).value = "model"
            app.screen.query_one("#endpoint-model", Input).value = "actual-request"
            app.screen.query_one("#endpoint-format", Input).value = "openai.responses"
            app.screen.query_one("#endpoint-profile", Select).value = "example-VLA"
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert (
                app.config.resolve_model_endpoint("model").connection.api_format
                == "openai.responses"
            )
            assert await pilot.click("#endpoint-resolve")
            await pilot.pause()
            assert SECRET not in app.screen.query_one("#resource-detail", TextArea).text
            assert (
                "actual-request"
                in app.screen.query_one("#resource-detail", TextArea).text
            )
            await pilot.press("escape")  # 详情没有取消按钮，但键盘可关闭
            await pilot.pause()
            assert await pilot.click("#endpoint-edit")
            await pilot.pause()
            app.screen.query_one("#endpoint-model", Input).value = "cancelled"
            await pilot.press("escape")
            await pilot.pause()
            assert app.config.get_model_endpoint("model").model_name == "actual-request"
            await pilot.click("#--content-tab-tab-mcp")
            await pilot.pause(0.1)
            assert await pilot.click("#mcp-create")
            await pilot.pause()
            app.screen.query_one("#res-name", Input).value = "tools"
            app.screen.query_one("#mcp-command", Input).value = "python"
            app.screen.query_one("#mcp-args", TextArea).load_text(json.dumps([SECRET]))
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.get_mcp_server(
                "tools"
            ).settings.args.get_secret_value() == [SECRET]
            assert await pilot.click("#mcp-edit")
            await pilot.pause()
            assert SECRET not in app.screen.query_one("#mcp-args", TextArea).text
            app.screen.query_one("#mcp-args", TextArea).load_text("[]")
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert (
                app.config.get_mcp_server("tools").settings.args.get_secret_value()
                == []
            )
            assert await pilot.click("#mcp-delete")
            await pilot.pause()
            await pilot.click("#btn-confirm")
            await pilot.pause()
            assert app.config.list_mcp_servers() == []
            assert not app.query("#tab-agent")

    run_ui(scenario())


def test_mcp_transport_forms_custom_json_and_stale_edit(tmp_path):
    path = tmp_path / "config.db"

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            app.push_screen(ResourceEditDialog("mcp"))
            await pilot.pause()
            app.screen.query_one("#res-name", Input).value = "http"
            app.screen.query_one("#mcp-transport", Input).value = "streamable_http"
            await pilot.pause()
            assert app.screen.query_one("#http-fields").display
            assert not app.screen.query_one("#stdio-fields").display
            app.screen.query_one("#mcp-url", Input).value = "https://host.invalid/mcp"
            app.screen.query_one("#mcp-headers", TextArea).load_text(
                json.dumps({"Key": SECRET})
            )
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            app.push_screen(ResourceEditDialog("mcp", "http"))
            await pilot.pause()
            app.screen.on_confirm()
            app.config.update_mcp_server("http", expected_revision=1, name="changed")
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.get_mcp_server("changed").revision == 2
            assert isinstance(app.screen, ResourceEditDialog)
            await pilot.press("escape")
            await pilot.pause()
            app.push_screen(ResourceEditDialog("mcp"))
            await pilot.pause()
            app.screen.query_one("#res-name", Input).value = "custom"
            app.screen.query_one("#mcp-transport", Input).value = "future-transport"
            await pilot.pause()
            assert app.screen.query_one("#custom-fields").display
            app.screen.query_one("#mcp-custom", TextArea).load_text(
                json.dumps({"opaque": SECRET})
            )
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            app.push_screen(ResourceEditDialog("mcp", "custom"))
            await pilot.pause()
            assert SECRET not in app.screen.query_one("#mcp-custom", TextArea).text
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.get_mcp_server(
                "custom"
            ).settings.options.get_secret_value() == {"opaque": SECRET}

    run_ui(scenario())


def test_catalog_stays_available_and_old_library_has_migration_help(tmp_path):
    path = tmp_path / "config.db"

    async def scenario():
        app = agentconfApp(str(path))
        async with app.run_test(size=(120, 60)) as pilot:
            await pilot.pause()
            app.query_one(TabbedContent).active = "tab-catalog"
            app.query_one("#catalog-kind", Select).value = "example"
            app.query_one("#catalog-filter", Input).value = "VLA"
            await pilot.pause()
            assert app.query_one("#catalog-table", DataTable).row_count == 1
            assert await pilot.click("#catalog-copy")
            await pilot.pause()
            app.screen.query_one("#copy-key", Input).value = "local/vla"
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("local/vla").origin == "local"
            app.push_screen(ProfileEditDialog("local/vla"))
            await pilot.pause()
            value = app.config.catalog_get("local/vla").model_dump(mode="json")
            value["supports_reasoning"] = False
            app.screen.query_one("#profile-json", TextArea).load_text(json.dumps(value))
            app.screen.on_confirm()
            app.screen.on_confirm()
            await pilot.pause()
            assert app.config.catalog_get("local/vla").supports_reasoning is False

    run_ui(scenario())
    old = tmp_path / "old.db"
    old_catalog(old)
    before = old.read_bytes()

    async def help_page():
        app = agentconfApp(str(old))
        async with app.run_test(size=(120, 55)) as pilot:
            await pilot.pause()
            assert await pilot.click("#migration-preview")
            await pilot.pause()
            assert "api_formats" in app.query_one("#migration-report", TextArea).text
            assert not app.query("#provider-table")

    run_ui(help_page())
    assert old.read_bytes() == before


def test_action_bars_are_compact_and_fit_common_widths(tmp_path):
    async def scenario():
        for width in (80, 120):
            app = agentconfApp(str(tmp_path / f"layout-{width}.db"))
            async with app.run_test(size=(width, 40)) as pilot:
                await pilot.pause()
                app.query_one(TabbedContent).active = "tab-catalog"
                await pilot.pause()
                catalog = app.query_one("#catalog-table").parent
                assert catalog.query_one(".action-bar").region.height == 6
                assert len(catalog.query(".action-row")) == 2
                catalog_buttons = list(catalog.query(Button))
                assert len(catalog_buttons) == 9
                assert all(
                    button.region.x + button.region.width <= width
                    for button in catalog_buttons
                )
                labels = {
                    str(button.label): button.region.width for button in catalog_buttons
                }
                assert labels["查看"] == 8
                assert max(labels.values()) <= 15

                app.query_one(TabbedContent).active = "tab-provider"
                await pilot.pause()
                provider = app.query_one("#provider-table").parent
                assert provider.query_one(".action-bar").region.height == 3
                assert len(provider.query(".action-row")) == 1
                assert len(list(provider.query(Button))) == 4

                app.query_one(TabbedContent).active = "tab-endpoint"
                await pilot.pause()
                endpoint = app.query_one("#endpoint-table").parent
                assert endpoint.query_one(".action-bar").region.height == 3
                endpoint_buttons = list(endpoint.query(Button))
                assert len(endpoint_buttons) == 5
                assert all(button.region.width < 16 for button in endpoint_buttons)
                assert all(
                    button.region.x + button.region.width <= width
                    for button in endpoint_buttons
                )

                app.push_screen(ResourceEditDialog("provider"))
                await pilot.pause()
                assert app.screen.query_one("#btn-cancel").region.width == 10
                assert app.screen.query_one("#btn-confirm").region.width >= 10
                await pilot.press("escape")
                await pilot.pause()

    run_ui(scenario())
