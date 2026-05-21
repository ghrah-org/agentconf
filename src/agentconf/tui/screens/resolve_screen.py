# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Resolve 配置解析界面"""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Container
from textual.widgets import (
    Button,
    Label,
    Select,
)

from agentconf.exceptions import AgentsConfigError
from agentconf.tui.screens.dialog import DialogScreen


class ResolveResultDialog(DialogScreen):
    def __init__(self, title: str, lines: list[str]) -> None:
        self._lines = lines
        super().__init__(
            title,
            confirm_text="关闭",
            cancel_text="",
        )

    def compose_body(self) -> ComposeResult:
        for line in self._lines:
            yield Label(line)


class ResolveScreen(Container):
    BINDINGS = [
        Binding("r", "resolve", "解析"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("选择实体类型和名称后点击「解析」")
        with Horizontal(classes="action-bar"):
            yield Label("类型:")
            yield Select(
                [("Agent", "agent"), ("Model", "model")],
                id="resolve-type",
                allow_blank=False,
                value="agent",
            )
            yield Label("名称:")
            yield Select([], id="resolve-name", allow_blank=True)
            yield Button("解析(R)", variant="primary", id="btn-resolve")

    def on_mount(self) -> None:
        self._refresh_names()

    def _refresh_names(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            resolve_type = self.query_one("#resolve-type", Select).value
            select = self.query_one("#resolve-name", Select)

            if resolve_type == "agent":
                items = self.app.config.list_agents()
            else:
                items = self.app.config.list_models()

            select.set_options([(i.name, i.name) for i in items])
            if items:
                select.value = items[0].name
        except AgentsConfigError:
            pass

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "resolve-type":
            self._refresh_names()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-resolve":
            self.action_resolve()

    def action_resolve(self) -> None:
        resolve_type = self.query_one("#resolve-type", Select).value
        name = self.query_one("#resolve-name", Select).value

        if not name:
            self.app.show_toast("请选择一个实体", error=True)
            return

        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)

            if resolve_type == "agent":
                self._resolve_agent(name)
            else:
                self._resolve_model(name)
        except AgentsConfigError as e:
            self.app.show_toast(str(e), error=True)

    def _resolve_agent(self, name: str) -> None:
        from agentconf.tui.app import agentconfApp

        assert isinstance(self.app, agentconfApp)
        resolved = self.app.config.resolve_agent(name)

        lines = [
            f"Agent: {resolved.agent_name}",
            "",
            "Model 配置:",
            f"  Provider: {resolved.model.provider_name}",
            f"  Provider Type: {resolved.model.provider_type.value}",
            f"  Model: {resolved.model.model_name}",
            f"  Model Type: {resolved.model.model_type}",
            f"  Base URL: {resolved.model.base_url}",
            f"  API Key: {resolved.model.api_key[:8]}...",
        ]

        if resolved.model.capabilities:
            lines.append("  能力标记:")
            lines.append(
                f"    输入模态: {', '.join(mod.value for mod in resolved.model.capabilities.input_modalities)}"
            )
            if hasattr(resolved.model.capabilities, "output_modalities"):
                lines.append(
                    f"    输出模态: {', '.join(mod.value for mod in resolved.model.capabilities.output_modalities)}"
                )

        lines.append("")
        lines.append("Agent 参数:")
        if resolved.temperature is not None:
            lines.append(f"  Temperature: {resolved.temperature}")
        if resolved.top_p is not None:
            lines.append(f"  Top-P: {resolved.top_p}")
        if resolved.top_k is not None:
            lines.append(f"  Top-K: {resolved.top_k}")
        if resolved.max_tokens is not None:
            lines.append(f"  Max Tokens: {resolved.max_tokens}")
        if resolved.extra_params:
            lines.append(f"  扩展参数: {json.dumps(resolved.extra_params, ensure_ascii=False)}")

        self.app.push_screen(ResolveResultDialog(f"Agent 解析: {name}", lines))

    def _resolve_model(self, name: str) -> None:
        from agentconf.tui.app import agentconfApp

        assert isinstance(self.app, agentconfApp)
        resolved = self.app.config.resolve_model(name)

        lines = [
            f"Model: {name}",
            "",
            "Provider 配置:",
            f"  Provider: {resolved.provider_name}",
            f"  Provider Type: {resolved.provider_type.value}",
            f"  Base URL: {resolved.base_url}",
            f"  API Key: {resolved.api_key[:8]}...",
            f"  Model: {resolved.model_name}",
            f"  Model Type: {resolved.model_type}",
        ]

        if resolved.capabilities:
            lines.append("  能力标记:")
            lines.append(
                f"    输入模态: {', '.join(mod.value for mod in resolved.capabilities.input_modalities)}"
            )
            if hasattr(resolved.capabilities, "output_modalities"):
                lines.append(
                    f"    输出模态: {', '.join(mod.value for mod in resolved.capabilities.output_modalities)}"
                )

        self.app.push_screen(ResolveResultDialog(f"Model 解析: {name}", lines))
