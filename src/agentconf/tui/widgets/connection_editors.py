# SPDX-License-Identifier: Apache-2.0
"""连接集合和认证绑定的可视草稿编辑；JSON 只是同一草稿的另一种视图。"""

from __future__ import annotations

import json
from copy import deepcopy
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Select, TextArea
from agentconf.models.secure import MASK, merge_masked


class EntryRow(Horizontal):
    def __init__(self, key, value, *, mapping, sensitive):
        super().__init__(classes="entry-row")
        self.original = value
        self.key = key
        self.mapping = mapping
        self.sensitive = sensitive

    def compose(self) -> ComposeResult:
        if self.mapping:
            yield Input(str(self.key), placeholder="名称", classes="entry-key")
        value = (
            MASK
            if self.sensitive and self.original is not None
            else str(self.original or "")
        )
        yield Input(
            value, password=self.sensitive, placeholder="值", classes="entry-value"
        )
        yield Button("删除", classes="entry-delete")

    def result(self):
        value = self.query_one(".entry-value", Input).value
        if value == MASK:
            if self.original is None:
                raise ValueError("新项不能使用保留掩码")
            value = self.original
        key = self.query_one(".entry-key", Input).value if self.mapping else None
        if self.mapping and not key:
            raise ValueError("键名不得为空；请填写或删除该行")
        return key, value

    def on_button_pressed(self, event):
        if event.button.has_class("entry-delete"):
            event.stop()
            self.remove()


class CollectionEditor(Vertical):
    def __init__(self, values, *, mapping=True, sensitive=True, id):
        super().__init__(id=id, classes="collection-editor")
        self.mapping = mapping
        self.sensitive = sensitive
        self._draft = deepcopy(values)
        self._json_mode = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="editor-actions"):
            yield Button("添加项", classes="entry-add")
            yield Button("高级 JSON", classes="entry-json")
        with Vertical(classes="entry-rows"):
            yield from self.rows(self._draft)
        yield TextArea("", classes="entry-raw compact-json")

    def rows(self, values):
        entries = values.items() if self.mapping else enumerate(values)
        for key, value in entries:
            yield EntryRow(key, value, mapping=self.mapping, sensitive=self.sensitive)

    def on_mount(self):
        self.query_one(".entry-raw").display = False

    def value(self):
        if self._json_mode:
            try:
                result = json.loads(self.query_one(TextArea).text)
            except ValueError:
                raise ValueError("连接集合 JSON 格式无效") from None
            if self.mapping:
                if not isinstance(result, dict):
                    raise ValueError("应填写键值对象")
                safe = {k: MASK for k in self._draft} if self.sensitive else self._draft
                return merge_masked(result, self._draft, safe)
            if not isinstance(result, list):
                raise ValueError("应填写参数列表")
            if MASK in result and len(result) != len(self._draft):
                raise ValueError("请使用列表编辑器删除带掩码的参数，或填写完整新值")
            return [
                self._draft[i] if v == MASK and i < len(self._draft) else v
                for i, v in enumerate(result)
            ]
        values = [row.result() for row in self.query(EntryRow)]
        if self.mapping:
            if len({key for key, _ in values}) != len(values):
                raise ValueError("键名不能重复")
            return dict(values)
        return [value for _, value in values]

    async def on_button_pressed(self, event):
        if event.button.has_class("entry-add"):
            event.stop()
            await self.query_one(".entry-rows").mount(
                EntryRow("", None, mapping=self.mapping, sensitive=self.sensitive)
            )
        elif event.button.has_class("entry-json"):
            event.stop()
            try:
                self._draft = self.value()
            except ValueError as error:
                self.notify(str(error), severity="error")
                return
            self._json_mode = not self._json_mode
            self.query_one(".entry-rows").display = not self._json_mode
            self.query_one(".entry-add", Button).disabled = self._json_mode
            area = self.query_one(TextArea)
            area.display = self._json_mode
            if self._json_mode:
                safe = (
                    (
                        {k: MASK for k in self._draft}
                        if self.mapping
                        else [MASK for _ in self._draft]
                    )
                    if self.sensitive
                    else self._draft
                )
                area.load_text(json.dumps(safe, ensure_ascii=False, indent=2))
            else:
                container = self.query_one(".entry-rows")
                await container.remove_children()
                await container.mount(*self.rows(self._draft))


class BindingEditor(Vertical):
    def __init__(self, binding=None, *, id):
        super().__init__(id=id, classes="binding-editor")
        self.binding = binding

    def compose(self) -> ComposeResult:
        binding = self.binding or {}
        if hasattr(binding, "model_dump"):
            binding = binding.model_dump(mode="json")
        preset = "none"
        if binding:
            preset = "env" if binding["target"] == "stdio_env" else "header"
            if binding["name"] == "Authorization":
                preset = {"Bearer {{auth}}": "bearer", "{{auth}}": "raw"}.get(
                    binding["value_template"], "header"
                )
        yield Select(
            [
                ("无默认认证", "none"),
                ("Authorization Bearer", "bearer"),
                ("Authorization 原始 Key", "raw"),
                ("自定义 HTTP Header", "header"),
                ("stdio 环境变量", "env"),
            ],
            value=preset,
            allow_blank=False,
            classes="binding-preset",
        )
        with Vertical(classes="binding-custom"):
            yield Label("Header／环境变量名称")
            yield Input(binding.get("name", ""), classes="binding-name")
            prefix, _, suffix = binding.get("value_template", "{{auth}}").partition(
                "{{auth}}"
            )
            yield Label("Key 前缀（可空，例如 Api-Key 后加空格）")
            yield Input(prefix, classes="binding-prefix")
            yield Label("Key 后缀（可空）")
            yield Input(suffix, classes="binding-suffix")

    def on_mount(self):
        self.refresh_fields()

    def refresh_fields(self):
        self.query_one(".binding-custom").display = self.query_one(Select).value in {
            "header",
            "env",
        }

    def on_select_changed(self, event):
        if event.select.has_class("binding-preset"):
            event.stop()
            self.refresh_fields()

    def value(self):
        preset = self.query_one(Select).value
        if preset == "none":
            return None
        if preset in {"bearer", "raw"}:
            return {
                "target": "http_header",
                "name": "Authorization",
                "value_template": "Bearer {{auth}}"
                if preset == "bearer"
                else "{{auth}}",
            }
        return {
            "target": "stdio_env" if preset == "env" else "http_header",
            "name": self.query_one(".binding-name", Input).value,
            "value_template": self.query_one(".binding-prefix", Input).value
            + "{{auth}}"
            + self.query_one(".binding-suffix", Input).value,
        }
