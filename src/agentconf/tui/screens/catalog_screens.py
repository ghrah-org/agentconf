# SPDX-License-Identifier: Apache-2.0
"""能力目录：离线查看、显式导入预览与受保护的人工纠错。"""

from __future__ import annotations

import json

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, DataTable, Input, Label, Select, TextArea

from agentconf import AgentsConfigError, CapabilityProfile, ModelsDevSource
from agentconf.catalog import ImportPlan, UpdatePlan, content, revision
from agentconf.tui.screens.dialog import ConfirmDialog, DialogScreen


class ProfileDetailDialog(DialogScreen):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__("能力档案（原始声明）", confirm_text="关闭", cancel_text="")

    def compose_body(self) -> ComposeResult:
        profile = self.app.config.catalog_get(self.key)
        if profile.origin == "example":
            yield Label("虚构示范档案：请求模型名由模型绑定单独填写。")
        yield TextArea(profile.model_dump_json(indent=2), read_only=True)
        if profile.origin == "derived":
            yield Label(
                json.dumps(
                    self.app.config.catalog_diff(self.key), ensure_ascii=False, indent=2
                ),
                markup=False,
            )
        if profile.origin == "upstream":
            yield Label(
                "派生: "
                + ", ".join(
                    p.model_key for p in self.app.config.catalog_variants(self.key)
                ),
                markup=False,
            )
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
        yield Label("仅 local/derived 可编辑；upstream/example 请先派生或复制。")
        yield TextArea(template, id="profile-json")
        yield Label(
            "先预览差异与引用影响，再按一次确认保存。", id="edit-preview", markup=False
        )

    def on_confirm(self) -> None:
        try:
            profile = CapabilityProfile.model_validate_json(
                self.query_one("#profile-json", TextArea).text
            )
            if self.old and self.old.origin not in {"local", "derived"}:
                raise ValueError("原件只读，请先派生/复制")
            if not self.old and profile.origin != "local":
                raise ValueError("人工新建只能使用 local 来源")
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
        super().__init__(
            "手动升级预览（保护用户数据）"
            if isinstance(plan, UpdatePlan)
            else "导入预览（已有差异保留）",
            confirm_text="接受所选新增与更新"
            if isinstance(plan, UpdatePlan)
            else "接受所选新增项",
        )

    def compose_body(self) -> ComposeResult:
        yield Label(
            f"来源: {self.plan.snapshot.source_url}\nSHA256: {self.plan.snapshot.source_sha256}"
        )
        yield Label(
            f"新增 {len(self.plan.added)} / 已有差异 {len(self.plan.changed)} / 相同 {len(self.plan.unchanged)} / 上游缺项 {len(self.plan.missing)}"
        )
        if isinstance(self.plan, UpdatePlan):
            yield Label(
                f"可更新 {len(self.plan.updatable)} / 用户保护 {len(self.plan.protected)} / 类型冲突 {len(self.plan.conflicts)}"
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
        yield Label(
            "填写要接受的新增/可更新键，以逗号分隔；* 接受全部可写入项。留空不会写入。"
        )
        yield Input(id="import-keys")

    def on_confirm(self) -> None:
        selected = self.query_one("#import-keys", Input).value.strip()
        if not selected:
            self._flash_error("请选择要接受的条目，或取消预览")
            return
        try:
            keys = (
                None
                if selected == "*"
                else [key.strip() for key in selected.split(",")]
            )
            apply = (
                self.app.config.catalog_update_apply
                if isinstance(self.plan, UpdatePlan)
                else self.app.config.catalog_apply
            )
            result = apply(self.plan, keys)
            self.dismiss(True)
            self.app.show_toast(
                f"新增 {len(result['added'])}，更新 {len(result.get('updated', []))}，相同跳过 {len(result['skipped'])}"
            )
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class ProfileCopyDialog(DialogScreen):
    def __init__(self, key: str) -> None:
        self.key = key
        self._reviewed = None
        super().__init__("派生上游 / 复制示例", confirm_text="预览 / 确认创建")

    def compose_body(self) -> ComposeResult:
        self.old = self.app.config.catalog_get(self.key)
        yield Label(
            f"来源 {self.old.origin}: {self.key}；完整复制原始声明，不继承后续变更。"
        )
        yield Input(value="local/" + self.key.split("/")[-1], id="copy-key")
        yield Label("填写新键，先预览再确认。", id="copy-preview", markup=False)

    def on_confirm(self) -> None:
        try:
            key = self.query_one("#copy-key", Input).value.strip()
            if self.old.origin not in {"upstream", "example"}:
                raise ValueError("只能派生 upstream 或复制 example")
            if self._reviewed != key:
                self.query_one("#copy-preview", Label).update(
                    json.dumps(
                        {"source": self.old.model_dump(mode="json"), "new_key": key},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                self._reviewed = key
                return
            if self.app.config.catalog_get(self.key).revision != self.old.revision:
                raise ValueError("预览后原件已变，请关闭并重新预览")
            method = (
                self.app.config.catalog_derive
                if self.old.origin == "upstream"
                else self.app.config.catalog_clone
            )
            method(self.key, key, expected_revision=self.old.revision)
            self.dismiss(True)
            self.app.show_toast("副本已创建")
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class ProfileRebuildDialog(DialogScreen):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__("重建派生基线（保留人工能力）", confirm_text="确认重建基线")

    def compose_body(self) -> ComposeResult:
        self.old = self.app.config.catalog_get(self.key)
        self.diff = self.app.config.catalog_diff(self.key)
        yield TextArea(
            json.dumps(self.diff, ensure_ascii=False, indent=2), read_only=True
        )
        yield Label("只确认当前上游基线，不覆盖人工声明。需要变更能力请另行编辑。")

    def on_confirm(self) -> None:
        try:
            if (
                self.app.config.catalog_diff(self.key)["parent_fingerprint"]
                != self.diff["parent_fingerprint"]
            ):
                raise ValueError("预览后上游已变，请重新预览")
            self.app.config.catalog_rebuild(
                self.key,
                expected_revision=self.old.revision,
                expected_parent_fingerprint=self.diff["parent_fingerprint"],
            )
            self.dismiss(True)
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class ProfileRelocateDialog(DialogScreen):
    def __init__(self, key: str) -> None:
        self.key = key
        self._reviewed = None
        super().__init__("搬迁人工占用键", confirm_text="预览 / 确认搬迁")

    def compose_body(self) -> ComposeResult:
        self.old = self.app.config.catalog_get(self.key)
        yield Input(value="local/" + self.key.split("/")[-1], id="relocate-key")
        yield Label(
            "搬迁会保留原能力并更新全部模型引用。", id="relocate-preview", markup=False
        )

    def on_confirm(self) -> None:
        try:
            key = self.query_one("#relocate-key", Input).value.strip()
            refs = self.app.config.catalog_references(self.key)
            if self.old.origin != "local":
                raise ValueError("只有 local 档案可搬迁")
            if self._reviewed != (key, refs):
                self.query_one("#relocate-preview", Label).update(
                    json.dumps(
                        {"old_key": self.key, "new_key": key, "references": refs},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                self._reviewed = (key, refs)
                return
            self.app.config.catalog_relocate(
                self.key,
                key,
                expected_revision=self.old.revision,
                expected_references=refs,
            )
            self.dismiss(True)
        except (AgentsConfigError, ValueError) as exc:
            self._flash_error(str(exc))


class CatalogScreen(Container):
    def compose(self) -> ComposeResult:
        yield Select(
            [
                ("全部来源", "all"),
                ("上游原件", "upstream"),
                ("人工派生", "derived"),
                ("本地档案", "local"),
                ("虚构示例", "example"),
            ],
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
            yield Button("派生/复制", id="catalog-copy")
            yield Button("仅新增", id="catalog-import")
            yield Button("手动升级", id="catalog-update", variant="primary")

            yield Button("重建派生基线", id="catalog-rebuild")
            yield Button("搬迁人工键", id="catalog-relocate")

    def on_mount(self) -> None:
        self.query_one("#catalog-table", DataTable).add_columns(
            "档案键",
            "名称",
            "来源类别",
            "类型",
            "来源",
            "派生状态",
            "上下文 / 输入上限",
        )
        self.refresh_catalog()

    def refresh_catalog(self) -> None:
        table = self.query_one("#catalog-table", DataTable)
        table.clear()
        kind = self.query_one("#catalog-kind", Select).value
        text = self.query_one("#catalog-filter", Input).value.casefold()
        for p in self.app.config.catalog_list(origin=None if kind == "all" else kind):
            if (
                text
                and text
                not in f"{p.model_key} {p.display_name} {p.model_type}".casefold()
            ):
                continue
            table.add_row(
                p.model_key,
                p.display_name,
                p.origin,
                p.model_type,
                p.source,
                ("stale" if self.app.config.catalog_stale(p.model_key) else "当前基线")
                if p.origin == "derived"
                else "—",
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
        elif name == "catalog-update":
            self.action_import(update=True)
        elif key:
            if name in {"catalog-copy", "catalog-rebuild", "catalog-relocate"}:
                cls = {
                    "catalog-copy": ProfileCopyDialog,
                    "catalog-rebuild": ProfileRebuildDialog,
                    "catalog-relocate": ProfileRelocateDialog,
                }[name]
                profile = self.app.config.catalog_get(key)
                valid = {
                    "catalog-copy": {"upstream", "example"},
                    "catalog-rebuild": {"derived"},
                    "catalog-relocate": {"local"},
                }[name]
                if profile.origin not in valid:
                    self.app.show_toast("当前来源不支持此操作", error=True)
                else:
                    self.app.push_screen(cls(key), refresh)
                return
            if name == "catalog-view":
                self.app.push_screen(ProfileDetailDialog(key))
            elif name == "catalog-edit":
                if self.app.config.catalog_get(key).origin not in {"local", "derived"}:
                    self.app.show_toast("原件只读，请先派生/复制", error=True)
                else:
                    self.app.push_screen(ProfileEditDialog(key), refresh)
            elif name == "catalog-delete":
                profile = self.app.config.catalog_get(key)
                refs = self.app.config.catalog_references(key)

                def delete(accepted) -> None:
                    if accepted:
                        try:
                            self.app.config.catalog_delete(
                                key, expected_revision=profile.revision
                            )
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
    def action_import(self, update: bool = False) -> None:
        # 下载线程不使用主线程的 SQLite 连接。
        try:
            snapshot = ModelsDevSource().fetch()
            self.app.call_from_thread(self._show_preview, snapshot, update)
        except Exception as exc:
            self.app.call_from_thread(self.app.show_toast, str(exc), error=True)

    def _show_preview(self, snapshot, update: bool = False) -> None:
        preview = (
            self.app.config.catalog_update_preview
            if update
            else self.app.config.catalog_preview
        )
        plan = preview(snapshot)
        self.app.push_screen(
            ImportPreviewDialog(plan), lambda _: self.refresh_catalog()
        )
