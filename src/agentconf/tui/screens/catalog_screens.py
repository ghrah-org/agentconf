# SPDX-License-Identifier: Apache-2.0
"""能力目录：离线查看、显式导入预览与受保护的人工纠错。"""

from __future__ import annotations

import json

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Input, Label, Select, TextArea

from agentconf import AgentsConfigError, CapabilityProfile, ModelsDevSource
from agentconf.catalog import ImportPlan, content, revision
from agentconf.tui.screens.dialog import ConfirmDialog, DialogScreen


class ProfileDetailDialog(DialogScreen):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__("能力档案（原始声明）", confirm_text="关闭", cancel_text="")

    def compose_body(self) -> ComposeResult:
        profile = self.app.config.catalog_get(self.key)
        if profile.entry_kind == "example":
            yield Label("虚构示范档案：请求模型名由模型绑定单独填写。")
        yield Label(profile.model_dump_json(indent=2), markup=False)
        yield Label(
            f"引用绑定: {', '.join(self.app.config.catalog_references(self.key)) or '无'}"
        )


class ProfileEditDialog(DialogScreen):
    def __init__(self, key: str | None = None) -> None:
        self.key = key
        self._reviewed: str | None = None
        super().__init__(
            "编辑能力档案" if key else "新建能力档案", confirm_text="预览 / 确认保存"
        )

    def compose_body(self) -> ComposeResult:
        self.old = self.app.config.catalog_get(self.key) if self.key else None
        self.expected_revision = revision(self.old) if self.old else None
        template = (
            self.old.model_dump_json(indent=2)
            if self.old
            else json.dumps(
                {
                    "model_key": "local/my-model",
                    "display_name": "我的模型",
                    "model_type": "llm",
                    "source": "manual",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        yield Label("完整原始声明 JSON；null 表示未声明，字段须符合模型类型。")
        yield TextArea(template, id="profile-json")
        yield Label(
            "先预览差异与引用影响，再按一次确认保存。", id="edit-preview", markup=False
        )

    def on_confirm(self) -> None:
        try:
            profile = CapabilityProfile.model_validate_json(
                self.query_one("#profile-json", TextArea).text
            )
            if self.old and profile.model_key != self.old.model_key:
                raise ValueError("档案键不可变")
            serialized = json.dumps(
                content(profile), sort_keys=True, ensure_ascii=False
            )
            if self._reviewed != serialized:
                self.query_one("#edit-preview", Label).update(
                    json.dumps(
                        {
                            "old": content(self.old) if self.old else None,
                            "new": content(profile),
                            "references": self.app.config.catalog_references(self.key)
                            if self.key
                            else [],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                self._reviewed = serialized
                return
            if self.old:
                self.app.config.catalog_edit(
                    profile, expected_revision=self.expected_revision
                )
            else:
                self.app.config.catalog_create(profile)
            self.dismiss(True)
            self.app.show_toast("能力档案已保存")
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class ImportPreviewDialog(DialogScreen):
    def __init__(self, plan: ImportPlan) -> None:
        self.plan = plan
        super().__init__("导入预览（已有差异保留）", confirm_text="接受所选新增项")

    def compose_body(self) -> ComposeResult:
        yield Label(
            f"来源: {self.plan.snapshot.source_url}\nSHA256: {self.plan.snapshot.source_sha256}"
        )
        yield Label(
            f"新增 {len(self.plan.added)} / 已有差异 {len(self.plan.changed)} / 相同 {len(self.plan.unchanged)} / 上游缺项 {len(self.plan.missing)}"
        )
        yield TextArea(
            json.dumps(
                self.plan.model_dump(mode="json", exclude={"snapshot"}),
                ensure_ascii=False,
                indent=2,
            ),
            read_only=True,
            id="import-report",
        )
        yield Label("填写要接受的新增键，以逗号分隔；* 表示全部新增项。留空不会写入。")
        yield Input(id="import-keys")

    def on_confirm(self) -> None:
        selected = self.query_one("#import-keys", Input).value.strip()
        if not selected:
            self._flash_error("请选择新增项，或取消预览")
            return
        try:
            keys = (
                None
                if selected == "*"
                else [key.strip() for key in selected.split(",")]
            )
            result = self.app.config.catalog_apply(self.plan, keys)
            self.dismiss(True)
            self.app.show_toast(
                f"新增 {len(result['added'])}，相同跳过 {len(result['skipped'])}"
            )
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class CatalogScreen(Container):
    def compose(self) -> ComposeResult:
        yield Select(
            [("全部档案", "all"), ("真实模型", "model"), ("虚构示例", "example")],
            value="all",
            allow_blank=False,
            id="catalog-kind",
        )
        yield Input(placeholder="按档案键、名称或类型筛选", id="catalog-filter")
        yield DataTable(id="catalog-table", cursor_type="row")
        with Horizontal(classes="action-bar"):
            yield Button("查看", id="catalog-view")
            yield Button("新建", id="catalog-create")
            yield Button("纠错", id="catalog-edit")
            yield Button("删除", id="catalog-delete", variant="error")
            yield Button("导入预览", id="catalog-import", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#catalog-table", DataTable).add_columns(
            "档案键", "名称", "种类", "类型", "来源", "上下文 / 输入上限"
        )
        self.refresh_catalog()

    def refresh_catalog(self) -> None:
        table = self.query_one("#catalog-table", DataTable)
        table.clear()
        kind = self.query_one("#catalog-kind", Select).value
        text = self.query_one("#catalog-filter", Input).value.casefold()
        for p in self.app.config.catalog_list(
            entry_kind=None if kind == "all" else kind
        ):
            if (
                text
                and text
                not in f"{p.model_key} {p.display_name} {p.model_type}".casefold()
            ):
                continue
            table.add_row(
                p.model_key,
                p.display_name,
                "虚构示例" if p.entry_kind == "example" else "真实模型",
                p.model_type,
                p.source,
                str(p.max_context_length or p.max_input_length or "未知"),
                key=p.model_key,
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            event.select.id == "catalog-kind"
            and self.query_one("#catalog-table", DataTable).columns
        ):
            self.refresh_catalog()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "catalog-filter":
            self.refresh_catalog()

    def _selected(self) -> str | None:
        table = self.query_one("#catalog-table", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        name = event.button.id
        key = self._selected()

        def refresh(_) -> None:
            self.refresh_catalog()

        if name == "catalog-create":
            self.app.push_screen(ProfileEditDialog(), refresh)
        elif name == "catalog-import":
            self.action_import()
        elif key:
            if name == "catalog-view":
                self.app.push_screen(ProfileDetailDialog(key))
            elif name == "catalog-edit":
                self.app.push_screen(ProfileEditDialog(key), refresh)
            elif name == "catalog-delete":
                refs = self.app.config.catalog_references(key)

                def delete(accepted) -> None:
                    if accepted:
                        try:
                            self.app.config.catalog_delete(key)
                            self.refresh_catalog()
                        except AgentsConfigError as exc:
                            self.app.show_toast(str(exc), error=True)

                self.app.push_screen(
                    ConfirmDialog(
                        "删除能力档案", f"{key}\n引用绑定: {', '.join(refs) or '无'}"
                    ),
                    delete,
                )

    @work(thread=True, exclusive=True, group="catalog-import")
    def action_import(self) -> None:
        # 下载线程不使用主线程的 SQLite 连接。
        try:
            snapshot = ModelsDevSource().fetch()
            self.app.call_from_thread(self._show_preview, snapshot)
        except Exception as exc:
            self.app.call_from_thread(self.app.show_toast, str(exc), error=True)

    def _show_preview(self, snapshot) -> None:
        plan = self.app.config.catalog_preview(snapshot)
        self.app.push_screen(
            ImportPreviewDialog(plan), lambda _: self.refresh_catalog()
        )
