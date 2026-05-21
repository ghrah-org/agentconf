# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Model Instance CRUD 界面"""

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

MODEL_TYPES = ["llm", "embedding", "rerank", "tts", "stt", "asr", "image_gen"]


class ModelCreateDialog(DialogScreen):
    def __init__(self) -> None:
        super().__init__("创建 Model Instance", confirm_text="创建")

    def compose_body(self) -> ComposeResult:
        yield Label("名称")
        yield Input(placeholder="如 gpt4o", id="name")
        yield Label("关联 Provider")
        yield Select([], id="provider", allow_blank=True)
        yield Label("模型标识")
        yield Input(placeholder="如 gpt-4o", id="model_name")
        yield Label("模型类型")
        yield Select(
            [(t, t) for t in MODEL_TYPES],
            id="model_type",
            allow_blank=False,
            value="llm",
        )
        yield Label("Base URL（可选，覆盖 Provider）")
        yield Input(placeholder="留空使用 Provider 的 Base URL", id="base_url")
        yield Label("API Key（可选，覆盖 Provider）")
        yield Input(
            placeholder="留空使用 Provider 的 API Key",
            id="api_key",
            password=True,
        )
        yield Label("能力标记 JSON（可选）")
        yield Input(
            placeholder='如 {"supports_vision": true}',
            id="capabilities",
        )

    def on_mount(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            providers = self.app.config.list_providers()
            select = self.query_one("#provider", Select)
            select.set_options([(p.name, p.name) for p in providers])
            if providers:
                select.value = providers[0].name
        except AgentsConfigError:
            pass

    def on_confirm(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        provider = self.query_one("#provider", Select).value
        model_name = self.query_one("#model_name", Input).value.strip()
        model_type = self.query_one("#model_type", Select).value
        base_url = self.query_one("#base_url", Input).value.strip() or None
        api_key = self.query_one("#api_key", Input).value.strip() or None
        caps_str = self.query_one("#capabilities", Input).value.strip() or None

        if not all([name, provider != Select.NULL, model_name]):
            self._flash_error("名称、Provider、模型标识为必填")
            return

        caps_dict = None
        if caps_str:
            try:
                caps_dict = json.loads(caps_str)
            except json.JSONDecodeError:
                self._flash_error("能力标记必须是有效的 JSON")
                return

        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            self.app.config.create_model(
                provider_name=provider,
                name=name,
                model_name=model_name,
                model_type=model_type,
                base_url=base_url,
                api_key=api_key,
                capabilities=caps_dict,
            )
            self.dismiss(None)
            self.app.show_toast(f"Model 已创建: {name}")
        except AgentsConfigError as e:
            self._flash_error(str(e))


class ModelUpdateDialog(DialogScreen):
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        super().__init__(f"编辑 Model: {model_name}", confirm_text="保存")

    def compose_body(self) -> ComposeResult:
        yield Label("模型标识（留空不修改）")
        yield Input(id="model_name")
        yield Label("模型类型（留空不修改）")
        yield Select(
            [(t, t) for t in MODEL_TYPES],
            id="model_type",
            allow_blank=True,
        )
        yield Label("关联 Provider（留空不修改）")
        yield Select([], id="provider", allow_blank=True)
        yield Label("Base URL（留空不修改）")
        yield Input(id="base_url")
        yield Label("API Key（留空不修改）")
        yield Input(id="api_key", password=True)
        yield Label("能力标记 JSON（留空不修改）")
        yield Input(id="capabilities")

    def on_mount(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            providers = self.app.config.list_providers()
            select = self.query_one("#provider", Select)
            select.set_options([(p.name, p.name) for p in providers])
        except AgentsConfigError:
            pass

    def on_confirm(self) -> None:
        model_name = self.query_one("#model_name", Input).value.strip() or None
        model_type = self.query_one("#model_type", Select).value
        provider = self.query_one("#provider", Select).value
        base_url = self.query_one("#base_url", Input).value.strip() or None
        api_key = self.query_one("#api_key", Input).value.strip() or None
        caps_str = self.query_one("#capabilities", Input).value.strip() or None

        has_model_type = model_type != Select.NULL
        has_provider = provider != Select.NULL

        if not any([model_name, has_model_type, has_provider, base_url, api_key, caps_str]):
            self._flash_error("至少修改一个字段")
            return

        kwargs: dict = {}
        if model_name:
            kwargs["model_name"] = model_name
        if has_model_type:
            kwargs["model_type"] = model_type
        if has_provider:
            kwargs["provider_name"] = provider
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        if caps_str:
            try:
                kwargs["capabilities"] = json.loads(caps_str)
            except json.JSONDecodeError:
                self._flash_error("能力标记必须是有效的 JSON")
                return

        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            self.app.config.update_model(self._model_name, **kwargs)
            self.dismiss(None)
            self.app.show_toast(f"Model 已更新: {self._model_name}")
        except AgentsConfigError as e:
            self._flash_error(str(e))


class ModelDetailDialog(DialogScreen):
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        super().__init__(
            f"Model 详情: {model_name}",
            confirm_text="关闭",
            cancel_text="",
        )

    def compose_body(self) -> ComposeResult:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            m = self.app.config.get_model(self._model_name)
            prov = self.app.config.get_provider_by_id(m.provider_id)
        except AgentsConfigError as e:
            yield Label(str(e), classes="error-toast")
            return

        yield Label(f"名称: {m.name}")
        yield Label(f"Provider: {prov.name if prov else '?'}")
        yield Label(f"模型标识: {m.model_name}")
        yield Label(f"类型: {m.model_type}")
        if m.base_url:
            yield Label(f"Base URL: {m.base_url}")
        if m.api_key:
            yield Label(f"API Key: {m.api_key.get_secret_value()[:8]}...")

        if m.capabilities:
            yield Label("能力标记:")
            yield Label(
                f"  输入模态: {', '.join(mod.value for mod in m.capabilities.input_modalities)}"
            )
            if hasattr(m.capabilities, "output_modalities"):
                yield Label(
                    f"  输出模态: {', '.join(mod.value for mod in m.capabilities.output_modalities)}"
                )
            for field_name, field_val in m.capabilities:
                if field_name in ("input_modalities", "output_modalities", "extra"):
                    continue
                if isinstance(field_val, bool):
                    mark = "✓" if field_val else "✗"
                    yield Label(f"  {field_name}: {mark}")
                elif field_val is not None:
                    yield Label(f"  {field_name}: {field_val}")
            if m.capabilities.extra:
                yield Label(f"  扩展: {json.dumps(m.capabilities.extra, ensure_ascii=False)}")

        yield Label(f"创建时间: {m.created_at}")
        yield Label(f"更新时间: {m.updated_at}")


class ModelScreen(Container):
    BINDINGS = [
        Binding("n", "create", "新建"),
        Binding("enter", "view", "查看"),
        Binding("e", "edit", "编辑"),
        Binding("d", "delete", "删除"),
        Binding("r", "refresh", "刷新"),
    ]

    def compose(self) -> ComposeResult:
        yield DataTable(id="model-table")
        with Horizontal(classes="action-bar"):
            yield Button("新建(N)", variant="success", id="btn-create")
            yield Button("查看(Enter)", variant="primary", id="btn-view")
            yield Button("编辑(E)", variant="primary", id="btn-edit")
            yield Button("删除(D)", variant="error", id="btn-delete")
            yield Button("刷新(R)", id="btn-refresh")

    def on_mount(self) -> None:
        table = self.query_one("#model-table", DataTable)
        table.add_columns("名称", "Provider", "模型标识", "类型", "创建时间")
        self._refresh()

    def _refresh(self) -> None:
        try:
            from agentconf.tui.app import agentconfApp

            assert isinstance(self.app, agentconfApp)
            models = self.app.config.list_models()
        except AgentsConfigError:
            models = []

        table = self.query_one("#model-table", DataTable)
        table.clear()
        for m in models:
            try:
                assert isinstance(self.app, agentconfApp)
                prov = self.app.config.get_provider_by_id(m.provider_id)
                provider_name = prov.name if prov else "?"
            except AgentsConfigError:
                provider_name = "?"

            table.add_row(
                m.name,
                provider_name,
                m.model_name,
                m.model_type,
                str(m.created_at)[:19],
                key=m.name,
            )

    def _get_selected(self) -> str | None:
        table = self.query_one("#model-table", DataTable)
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

        self.app.push_screen(ModelCreateDialog(), _on_dismiss)

    def action_view(self) -> None:
        name = self._get_selected()
        if name:
            self.app.push_screen(ModelDetailDialog(name))

    def action_edit(self) -> None:
        name = self._get_selected()
        if not name:
            return

        def _on_dismiss(_) -> None:
            self._refresh()

        self.app.push_screen(ModelUpdateDialog(name), _on_dismiss)

    def action_delete(self) -> None:
        name = self._get_selected()
        if not name:
            return

        def _do_delete(_) -> None:
            try:
                from agentconf.tui.app import agentconfApp

                assert isinstance(self.app, agentconfApp)
                self.app.config.delete_model(name)
                self.app.show_toast(f"Model 已删除: {name}")
            except AgentsConfigError as e:
                self.app.show_toast(str(e), error=True)
            self._refresh()

        self.app.push_screen(
            ConfirmDialog(
                "确认删除",
                f"确定删除 Model '{name}'？\n关联的 Agent 将被级联删除。",
            ),
            _do_delete,
        )

    def action_refresh(self) -> None:
        self._refresh()
