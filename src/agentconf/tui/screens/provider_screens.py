# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Provider CRUD 界面"""

from __future__ import annotations


from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Container
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Select,
)

from agentconf import ProviderType
from agentconf.exceptions import AgentsConfigError
from agentconf.tui.screens.dialog import ConfirmDialog, DialogScreen


PROVIDER_TYPES = ["deepseek", "openai", "anthropic", "custom"]


class ProviderCreateDialog(DialogScreen):
    def __init__(self) -> None:
        super().__init__(
            "创建 Provider",
            confirm_text="创建",
        )

    def compose_body(self) -> ComposeResult:
        yield Label("名称")
        yield Input(placeholder="如 openai-main", id="name")
        yield Label("类型")
        yield Select(
            [(t, t) for t in PROVIDER_TYPES],
            id="provider_type",
            allow_blank=False,
            value="openai",
        )
        yield Label("Base URL")
        yield Input(placeholder="https://api.openai.com/v1", id="base_url")
        yield Label("API Key")
        yield Input(placeholder="sk-...", id="api_key", password=True)

    def on_confirm(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        provider_type = self.query_one("#provider_type", Select).value
        base_url = self.query_one("#base_url", Input).value.strip()
        api_key = self.query_one("#api_key", Input).value.strip()

        if not all([name, base_url, api_key, provider_type != Select.NULL]):
            self._flash_error("所有字段为必填")
            return

        try:
            app = self.app
            from agentconf.tui.app import agentconfApp

            assert isinstance(app, agentconfApp)
            app.config.create_provider(
                name=name,
                provider_type=ProviderType(provider_type),
                base_url=base_url,
                api_key=api_key,
            )
            self.dismiss(None)
            app.show_toast(f"Provider 已创建: {name}")
        except AgentsConfigError as e:
            self._flash_error(str(e))


class ProviderUpdateDialog(DialogScreen):
    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name
        super().__init__(
            f"编辑 Provider: {provider_name}",
            confirm_text="保存",
        )

    def compose_body(self) -> ComposeResult:
        yield Label("Base URL（留空不修改）")
        yield Input(placeholder="https://api.openai.com/v1", id="base_url")
        yield Label("API Key（留空不修改）")
        yield Input(placeholder="输入新的 API Key", id="api_key", password=True)
        yield Label("类型（留空不修改）")
        yield Select(
            [(t, t) for t in PROVIDER_TYPES],
            id="provider_type",
            allow_blank=True,
        )

    def on_confirm(self) -> None:
        base_url = self.query_one("#base_url", Input).value.strip() or None
        api_key = self.query_one("#api_key", Input).value.strip() or None
        provider_type = self.query_one("#provider_type", Select).value

        has_provider_type = provider_type != Select.NULL

        if not any([base_url, api_key, has_provider_type]):
            self._flash_error("至少修改一个字段")
            return

        kwargs: dict = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        if has_provider_type:
            kwargs["provider_type"] = provider_type

        try:
            app = self.app
            from agentconf.tui.app import agentconfApp

            assert isinstance(app, agentconfApp)
            app.config.update_provider(self._provider_name, **kwargs)
            self.dismiss(None)
            app.show_toast(f"Provider 已更新: {self._provider_name}")
        except AgentsConfigError as e:
            self._flash_error(str(e))


class ProviderDetailDialog(DialogScreen):
    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name
        super().__init__(
            f"Provider 详情: {provider_name}",
            confirm_text="关闭",
            cancel_text="",
        )

    def compose_body(self) -> ComposeResult:
        try:
            app = self.app
            from agentconf.tui.app import agentconfApp

            assert isinstance(app, agentconfApp)
            p = app.config.get_provider(self._provider_name)
        except AgentsConfigError as e:
            yield Label(str(e), classes="error-toast")
            return

        yield Label(f"名称: {p.name}")
        yield Label(f"类型: {p.provider_type.value}")
        yield Label(f"Base URL: {p.base_url}")
        yield Label(f"API Key: {p.api_key.get_secret_value()[:8]}...")
        yield Label(f"创建时间: {p.created_at}")
        yield Label(f"更新时间: {p.updated_at}")



class ProviderScreen(Container):
    BINDINGS = [
        Binding("n", "create", "新建"),
        Binding("enter", "view", "查看"),
        Binding("e", "edit", "编辑"),
        Binding("d", "delete", "删除"),
        Binding("r", "refresh", "刷新"),
    ]

    def compose(self) -> ComposeResult:
        yield DataTable(id="provider-table")
        with Horizontal(classes="action-bar"):
            yield Button("新建(N)", variant="success", id="btn-create")
            yield Button("查看(Enter)", variant="primary", id="btn-view")
            yield Button("编辑(E)", variant="primary", id="btn-edit")
            yield Button("删除(D)", variant="error", id="btn-delete")
            yield Button("刷新(R)", id="btn-refresh")

    def on_mount(self) -> None:
        table = self.query_one("#provider-table", DataTable)
        table.add_columns("名称", "类型", "Base URL", "API Key", "创建时间")
        self._refresh()

    def _refresh(self) -> None:
        try:
            app = self.app
            from agentconf.tui.app import agentconfApp

            assert isinstance(app, agentconfApp)
            providers = app.config.list_providers()
        except AgentsConfigError:
            providers = []

        table = self.query_one("#provider-table", DataTable)
        table.clear()
        for p in providers:
            table.add_row(
                p.name,
                p.provider_type.value,
                p.base_url,
                p.api_key.get_secret_value()[:8] + "...",
                str(p.created_at)[:19],
                key=p.name,
            )

    def _get_selected(self) -> str | None:
        table = self.query_one("#provider-table", DataTable)
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
            return cell_key.row_key.value
        except Exception:
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "btn-create":
            self.action_create()
        elif btn == "btn-view":
            self.action_view()
        elif btn == "btn-edit":
            self.action_edit()
        elif btn == "btn-delete":
            self.action_delete()
        elif btn == "btn-refresh":
            self._refresh()

    def action_create(self) -> None:
        def _on_dismiss(_) -> None:
            self._refresh()

        self.app.push_screen(ProviderCreateDialog(), _on_dismiss)

    def action_view(self) -> None:
        name = self._get_selected()
        if name:
            self.app.push_screen(ProviderDetailDialog(name))

    def action_edit(self) -> None:
        name = self._get_selected()
        if not name:
            return

        def _on_dismiss(_) -> None:
            self._refresh()

        self.app.push_screen(ProviderUpdateDialog(name), _on_dismiss)

    def action_delete(self) -> None:
        name = self._get_selected()
        if not name:
            return

        def _do_delete(_) -> None:
            try:
                app = self.app
                from agentconf.tui.app import agentconfApp

                assert isinstance(app, agentconfApp)
                app.config.delete_provider(name)
                app.show_toast(f"Provider 已删除: {name}")
            except AgentsConfigError as e:
                app.show_toast(str(e), error=True)
            self._refresh()

        self.app.push_screen(
            ConfirmDialog(
                "确认删除",
                f"确定删除 Provider '{name}'？\n关联的 Model 和 Agent 将被级联删除。",
            ),
            _do_delete,
        )

    def action_refresh(self) -> None:
        self._refresh()
