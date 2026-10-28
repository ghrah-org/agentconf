# SPDX-License-Identifier: Apache-2.0
"""模型绑定界面；能力档案由目录维护。"""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Input, Label, Select

from agentconf.exceptions import AgentsConfigError
from agentconf.tui.screens.dialog import ConfirmDialog, DialogScreen

MODEL_TYPES = [
    "llm",
    "embedding",
    "rerank",
    "tts",
    "stt",
    "image_gen",
    "video",
    "realtime",
]


class ModelCreateDialog(DialogScreen):
    def __init__(self, model_name: str | None = None) -> None:
        self._editing = model_name
        super().__init__(
            f"编辑绑定: {model_name}" if model_name else "创建模型绑定",
            confirm_text="保存",
        )

    def compose_body(self) -> ComposeResult:
        if self._editing is None:
            yield Label("绑定名称（全局唯一）")
            yield Input(id="name")
            yield Label("Provider")
            yield Select([], id="provider")
        else:
            yield Label("Provider 由绑定确定；更换连接请创建新绑定。")
        yield Label("实际请求模型标识（选档案不会修改此字段）")
        yield Input(id="model_name")
        yield Label("模型类型")
        types = set(MODEL_TYPES) | {
            p.model_type for p in self.app.config.catalog_list()
        }
        if self._editing:
            types.add(self.app.config.get_model(self._editing).model_type)
        yield Select(
            [(t, t) for t in sorted(types)],
            id="model_type",
            allow_blank=False,
            value="llm",
        )
        yield Label("能力档案（留空使用类型默认值）")
        yield Select([], id="profile", prompt="未绑定档案")
        yield Label("LLM 默认上下文 1M，支持工具与推理；example-* 为虚构示范。")

    def on_mount(self) -> None:
        if self._editing:
            model = self.app.config.get_model(self._editing)
            self.query_one("#model_name", Input).value = model.model_name
            self.query_one("#model_type", Select).value = model.model_type
            self._refresh_profiles(model.model_type)
            self.query_one("#profile", Select).value = (
                model.capabilities_key or Select.NULL
            )
        else:
            providers = self.app.config.list_providers()
            select = self.query_one("#provider", Select)
            select.set_options([(p.name, p.name) for p in providers])
            if providers:
                select.value = providers[0].name
            self._refresh_profiles("llm")

    def _refresh_profiles(self, model_type: str) -> None:
        select = self.query_one("#profile", Select)
        previous = select.value
        profiles = self.app.config.catalog_list(model_type=model_type)
        select.set_options(
            [
                (
                    f"{'[示例] ' if p.origin == 'example' else ''}{p.display_name} ({p.model_key})",
                    p.model_key,
                )
                for p in profiles
            ]
        )
        if previous in {p.model_key for p in profiles}:
            select.value = previous

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "model_type" and event.value != Select.NULL:
            self._refresh_profiles(str(event.value))

    def on_confirm(self) -> None:
        profile = self.query_one("#profile", Select).value
        values = dict(
            model_name=self.query_one("#model_name", Input).value.strip(),
            model_type=self.query_one("#model_type", Select).value,
            capabilities_key=None if profile == Select.NULL else profile,
        )
        try:
            if self._editing:
                self.app.config.update_model(self._editing, **values)
            else:
                provider = self.query_one("#provider", Select).value
                if provider == Select.NULL:
                    raise ValueError("请选择 Provider")
                self.app.config.create_model(
                    provider_name=provider,
                    name=self.query_one("#name", Input).value.strip(),
                    **values,
                )
            self.dismiss(None)
            self.app.show_toast("模型绑定已保存")
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class ModelUpdateDialog(ModelCreateDialog):
    def __init__(self, model_name: str) -> None:
        super().__init__(model_name)


class ModelDetailDialog(DialogScreen):
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        super().__init__(f"模型绑定: {model_name}", confirm_text="关闭", cancel_text="")

    def compose_body(self) -> ComposeResult:
        binding = self.app.config.get_model(self._model_name)
        resolved = self.app.config.resolve_model(self._model_name)
        yield Label(
            json.dumps(binding.model_dump(mode="json"), ensure_ascii=False, indent=2),
            markup=False,
        )
        yield Label(f"连接: {resolved.provider_name} / {resolved.base_url}")
        yield Label("有效能力（未声明字段按类型补默认值）")
        yield Label(
            json.dumps(
                resolved.capabilities.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
            markup=False,
        )
        yield Label(
            json.dumps(resolved.capability_sources, ensure_ascii=False, indent=2),
            markup=False,
        )


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
        table.add_columns(
            "名称", "Provider", "模型标识", "类型", "能力档案", "创建时间"
        )
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
                m.capabilities_key or "(默认)",
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

        def _do_delete(accepted) -> None:
            if not accepted:
                return
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
