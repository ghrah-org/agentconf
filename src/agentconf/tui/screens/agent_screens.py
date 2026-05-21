# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Agent CRUD 界面"""

from __future__ import annotations

import json

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

from agentconf.exceptions import AgentsConfigError
from agentconf.tui.screens.dialog import ConfirmDialog, DialogScreen


class AgentCreateDialog(DialogScreen):
    def __init__(self) -> None:
        super().__init__("创建 Agent", confirm_text="创建")

    def compose_body(self) -> ComposeResult:
        yield Label("名称")
        yield Input(placeholder="如 code-reviewer", id="name")
        yield Label("关联 Model")
        yield Select([], id="model", allow_blank=True)
        yield Label("Temperature（0-2，可选）")
        yield Input(placeholder="0.7", id="temperature")
        yield Label("Top-P（0-1，可选）")
        yield Input(placeholder="0.9", id="top_p")
        yield Label("Top-K（≥1，可选）")
        yield Input(placeholder="40", id="top_k")
        yield Label("Max Tokens（≥1，可选）")
        yield Input(placeholder="4096", id="max_tokens")
        yield Label("扩展参数 JSON（可选）")
        yield Input(placeholder='{"key": "value"}', id="extra_params")

    def on_mount(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            models = self.app.config.list_models()
            select = self.query_one("#model", Select)
            select.set_options([(m.name, m.name) for m in models])
            if models:
                select.value = models[0].name
        except AgentsConfigError:
            pass

    def on_confirm(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        model = self.query_one("#model", Select).value
        temp_str = self.query_one("#temperature", Input).value.strip()
        top_p_str = self.query_one("#top_p", Input).value.strip()
        top_k_str = self.query_one("#top_k", Input).value.strip()
        max_tokens_str = self.query_one("#max_tokens", Input).value.strip()
        extra_str = self.query_one("#extra_params", Input).value.strip()

        if not all([name, model != Select.NULL]):
            self._flash_error("名称和关联 Model 为必填")
            return

        temperature = float(temp_str) if temp_str else None
        top_p = float(top_p_str) if top_p_str else None
        top_k = int(top_k_str) if top_k_str else None
        max_tokens = int(max_tokens_str) if max_tokens_str else None

        extra_params = None
        if extra_str:
            try:
                extra_params = json.loads(extra_str)
            except json.JSONDecodeError:
                self._flash_error("扩展参数必须是有效的 JSON")
                return

        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            self.app.config.create_agent(
                name=name,
                model_name=model,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                max_tokens=max_tokens,
                extra_params=extra_params,
            )
            self.dismiss(None)
            self.app.show_toast(f"Agent 已创建: {name}")
        except AgentsConfigError as e:
            self._flash_error(str(e))


class AgentUpdateDialog(DialogScreen):
    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name
        super().__init__(f"编辑 Agent: {agent_name}", confirm_text="保存")

    def compose_body(self) -> ComposeResult:
        yield Label("关联 Model（留空不修改）")
        yield Select([], id="model", allow_blank=True)
        yield Label("Temperature（留空不修改）")
        yield Input(id="temperature")
        yield Label("Top-P（留空不修改）")
        yield Input(id="top_p")
        yield Label("Top-K（留空不修改）")
        yield Input(id="top_k")
        yield Label("Max Tokens（留空不修改）")
        yield Input(id="max_tokens")
        yield Label("扩展参数 JSON（留空不修改）")
        yield Input(id="extra_params")

    def on_mount(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            models = self.app.config.list_models()
            select = self.query_one("#model", Select)
            select.set_options([(m.name, m.name) for m in models])
        except AgentsConfigError:
            pass

    def on_confirm(self) -> None:
        model = self.query_one("#model", Select).value
        temp_str = self.query_one("#temperature", Input).value.strip()
        top_p_str = self.query_one("#top_p", Input).value.strip()
        top_k_str = self.query_one("#top_k", Input).value.strip()
        max_tokens_str = self.query_one("#max_tokens", Input).value.strip()
        extra_str = self.query_one("#extra_params", Input).value.strip()

        has_model = model != Select.NULL

        if not any([has_model, temp_str, top_p_str, top_k_str, max_tokens_str, extra_str]):
            self._flash_error("至少修改一个字段")
            return

        kwargs: dict = {}
        if has_model:
            kwargs["model_name"] = model
        if temp_str:
            try:
                kwargs["temperature"] = float(temp_str)
            except ValueError:
                self._flash_error("Temperature 必须是数字")
                return
        if top_p_str:
            try:
                kwargs["top_p"] = float(top_p_str)
            except ValueError:
                self._flash_error("Top-P 必须是数字")
                return
        if top_k_str:
            try:
                kwargs["top_k"] = int(top_k_str)
            except ValueError:
                self._flash_error("Top-K 必须是整数")
                return
        if max_tokens_str:
            try:
                kwargs["max_tokens"] = int(max_tokens_str)
            except ValueError:
                self._flash_error("Max Tokens 必须是整数")
                return
        if extra_str:
            try:
                kwargs["extra_params"] = json.loads(extra_str)
            except json.JSONDecodeError:
                self._flash_error("扩展参数必须是有效的 JSON")
                return

        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            self.app.config.update_agent(self._agent_name, **kwargs)
            self.dismiss(None)
            self.app.show_toast(f"Agent 已更新: {self._agent_name}")
        except AgentsConfigError as e:
            self._flash_error(str(e))


class AgentDetailDialog(DialogScreen):
    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name
        super().__init__(
            f"Agent 详情: {agent_name}",
            confirm_text="关闭",
            cancel_text="",
        )

    def compose_body(self) -> ComposeResult:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            a = self.app.config.get_agent(self._agent_name)
            m = self.app.config.get_model_by_id(a.model_instance_id)
        except AgentsConfigError as e:
            yield Label(str(e), classes="error-toast")
            return

        yield Label(f"名称: {a.name}")
        yield Label(f"Model: {m.name if m else '?'}")
        if a.temperature is not None:
            yield Label(f"Temperature: {a.temperature}")
        if a.top_p is not None:
            yield Label(f"Top-P: {a.top_p}")
        if a.top_k is not None:
            yield Label(f"Top-K: {a.top_k}")
        if a.max_tokens is not None:
            yield Label(f"Max Tokens: {a.max_tokens}")
        if a.extra_params:
            yield Label(f"扩展参数: {json.dumps(a.extra_params, ensure_ascii=False)}")
        yield Label(f"创建时间: {a.created_at}")
        yield Label(f"更新时间: {a.updated_at}")

class AgentScreen(Container):
    BINDINGS = [
        Binding("n", "create", "新建"),
        Binding("enter", "view", "查看"),
        Binding("e", "edit", "编辑"),
        Binding("d", "delete", "删除"),
        Binding("r", "refresh", "刷新"),
    ]

    def compose(self) -> ComposeResult:
        yield DataTable(id="agent-table")
        with Horizontal(classes="action-bar"):
            yield Button("新建(N)", variant="success", id="btn-create")
            yield Button("查看(Enter)", variant="primary", id="btn-view")
            yield Button("编辑(E)", variant="primary", id="btn-edit")
            yield Button("删除(D)", variant="error", id="btn-delete")
            yield Button("刷新(R)", id="btn-refresh")

    def on_mount(self) -> None:
        table = self.query_one("#agent-table", DataTable)
        table.add_columns("名称", "Model", "参数", "创建时间")
        self._refresh()

    def _refresh(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            agents = self.app.config.list_agents()
        except AgentsConfigError:
            agents = []

        table = self.query_one("#agent-table", DataTable)
        table.clear()
        for a in agents:
            try:
                assert isinstance(self.app, agentconfApp)
                m = self.app.config.get_model_by_id(a.model_instance_id)
                model_name = m.name if m else "?"
            except AgentsConfigError:
                model_name = "?"

            params = []
            if a.temperature is not None:
                params.append(f"temp={a.temperature}")
            if a.top_p is not None:
                params.append(f"top_p={a.top_p}")
            if a.top_k is not None:
                params.append(f"top_k={a.top_k}")
            if a.max_tokens is not None:
                params.append(f"max_tokens={a.max_tokens}")

            table.add_row(
                a.name,
                model_name,
                ", ".join(params) if params else "-",
                str(a.created_at)[:19],
                key=a.name,
            )

    def _get_selected(self) -> str | None:
        table = self.query_one("#agent-table", DataTable)
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

        self.app.push_screen(AgentCreateDialog(), _on_dismiss)

    def action_view(self) -> None:
        name = self._get_selected()
        if name:
            self.app.push_screen(AgentDetailDialog(name))

    def action_edit(self) -> None:
        name = self._get_selected()
        if not name:
            return

        def _on_dismiss(_) -> None:
            self._refresh()

        self.app.push_screen(AgentUpdateDialog(name), _on_dismiss)

    def action_delete(self) -> None:
        name = self._get_selected()
        if not name:
            return

        def _do_delete(_) -> None:
            try:
                from agentconf.tui.app import agentconfApp

                assert isinstance(self.app, agentconfApp)
                self.app.config.delete_agent(name)
                self.app.show_toast(f"Agent 已删除: {name}")
            except AgentsConfigError as e:
                self.app.show_toast(str(e), error=True)
            self._refresh()

        self.app.push_screen(
            ConfirmDialog("确认删除", f"确定删除 Agent '{name}'？"),
            _do_delete,
        )

    def action_refresh(self) -> None:
        self._refresh()
