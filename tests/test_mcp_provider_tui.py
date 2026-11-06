"""真实键盘、下拉与按钮的 MCP 配置流程，不用直接提交表单替代交互。"""

import asyncio
import pytest
from textual.widgets import Input, Select, TabbedContent, TextArea, Button
from agentconf.tui.app import agentconfApp
from agentconf.tui.widgets.connection_editors import CollectionEditor, EntryRow


async def fill(pilot, widget, value):
    widget.focus()
    await pilot.pause(0.25)
    await pilot.press("home", "shift+end", "backspace", *value)
    await pilot.pause(0.25)


async def down(pilot, select, count=1):
    select.focus()
    await pilot.pause(0.25)
    await pilot.press("enter", *(["down"] * count), "enter")
    await pilot.pause(0.25)


@pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 60)])
def test_shared_provider_and_http_server_created_with_actual_controls(tmp_path, size):
    async def scenario():
        app = agentconfApp(str(tmp_path / "config.db"))
        async with app.run_test(size=size) as pilot:
            app.query_one(TabbedContent).active = "tab-mcp-provider"
            await pilot.pause(0.25)
            assert await pilot.click("#mcp-provider-create")
            await pilot.pause(0.25)
            await fill(
                pilot, app.screen.query_one("#res-name", Input), "vendor/account"
            )
            await fill(
                pilot,
                app.screen.query_one("#mcp-provider-key", Input),
                "UI-PRIVATE-KEY",
            )
            await down(pilot, app.screen.query_one("#provider-binding Select", Select))
            assert (
                app.screen.query_one("#provider-binding Select", Select).value
                == "bearer"
            )
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            assert app.config.list_mcp_providers() == []
            assert (
                "UI-PRIVATE-KEY"
                not in app.screen.query_one("#resource-preview", TextArea).text
            )
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            provider = app.config.get_mcp_provider("vendor/account")
            assert provider.default_binding.name == "Authorization"
            app.query_one(TabbedContent).active = "tab-mcp"
            await pilot.pause(0.25)
            app.query_one("#mcp-create", Button).focus()
            await pilot.press("enter")
            await pilot.pause(0.25)
            await fill(pilot, app.screen.query_one("#res-name", Input), "web-tools")
            await down(pilot, app.screen.query_one("#mcp-transport", Select))
            assert (
                app.screen.query_one("#mcp-transport", Select).value
                == "streamable_http"
            )
            await fill(
                pilot,
                app.screen.query_one("#mcp-url", Input),
                "https://example.invalid/mcp",
            )
            await down(pilot, app.screen.query_one("#mcp-auth-mode", Select))
            await down(pilot, app.screen.query_one("#mcp-provider-select", Select))
            assert (
                app.screen.query_one("#mcp-provider-select", Select).value
                == provider.id
            )
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            assert app.config.list_mcp_servers() == []
            preview = app.screen.query_one("#resource-preview", TextArea).text
            assert provider.id in preview and "UI-PRIVATE-KEY" not in preview
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            resolved = app.config.resolve_mcp_server("web-tools")
            assert resolved.auth.provider_id == provider.id
            assert resolved.auth.binding.value_template == "Bearer {{auth}}"

    asyncio.run(scenario())


def test_static_header_rows_preserve_other_secret_when_deleting_and_replacing(tmp_path):
    async def scenario():
        app = agentconfApp(str(tmp_path / "config.db"))
        app.config.create_mcp_server(
            "inline",
            "streamable_http",
            {
                "url": "https://example.invalid",
                "headers": {"X-One": "secret-one", "X-Two": "secret-two"},
            },
            auth={"mode": "inline"},
        )
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(TabbedContent).active = "tab-mcp"
            await pilot.pause(0.25)
            assert await pilot.click("#mcp-edit")
            await pilot.pause(0.25)
            editor = app.screen.query_one("#mcp-headers", CollectionEditor)
            rows = list(editor.query(EntryRow))
            delete = rows[0].query_one(Button)
            delete.focus()
            await pilot.pause(0.25)
            await pilot.press("enter")
            await pilot.pause(0.25)
            assert len(list(editor.query(EntryRow))) == 1
            assert "secret-two" not in app.export_screenshot()
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            assert app.config.get_mcp_server(
                "inline"
            ).settings.headers.get_secret_value() == {"X-Two": "secret-two"}

    asyncio.run(scenario())


def test_actual_stale_delete_confirmation_keeps_recreated_record(tmp_path):
    async def scenario():
        app = agentconfApp(str(tmp_path / "config.db"))
        old = app.config.create_mcp_server("tools", "stdio", {"command": "python"})
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(TabbedContent).active = "tab-mcp"
            await pilot.pause(0.25)
            assert await pilot.click("#mcp-delete")
            await pilot.pause(0.25)
            app.config.delete_mcp_server(
                "tools", expected_revision=1, expected_id=old.id
            )
            replacement = app.config.create_mcp_server(
                "tools", "stdio", {"command": "other"}
            )
            assert await pilot.click("#btn-confirm")
            await pilot.pause(0.25)
            assert app.config.get_mcp_server("tools").id == replacement.id

    asyncio.run(scenario())
