# SPDX-License-Identifier: Apache-2.0
"""Provider、模型端点与 MCP 的独立管理页面，共用公开 SDK。"""

from __future__ import annotations

import hashlib
import json
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Select, TextArea
from agentconf import AgentsConfigError
from agentconf.operations import (
    get_resource,
    list_resources,
    resource_input,
    validate_input,
    apply_resource,
    delete_resource,
    resolve_resource,
)
from agentconf.tui.screens.dialog import DialogScreen, ConfirmDialog


def text(value):
    return "" if value is None else str(value)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def read_json(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        raise ValueError("JSON 设置格式无效") from None


class ResourceDetailDialog(DialogScreen):
    def __init__(self, domain, name, *, resolve=False):
        self.domain, self.resource_name, self.resolve = domain, name, resolve
        super().__init__(
            "静态解析预览" if resolve else "配置详情与引用",
            confirm_text="关闭",
            cancel_text="",
        )

    def compose_body(self) -> ComposeResult:
        cfg = self.app.config
        record = (
            resolve_resource(cfg, self.domain, self.resource_name)
            if self.resolve
            else get_resource(cfg, self.domain, self.resource_name)
        )
        yield TextArea(
            record.model_dump_json(indent=2),
            read_only=True,
            id="resource-detail",
            classes="resource-json",
        )
        if self.domain == "provider":
            yield Label(
                "引用端点: " + ", ".join(cfg.provider_references(self.resource_name)),
                markup=False,
            )
        yield Label("这里只展示静态配置，不联网、不启动命令。")


class ResourceEditDialog(DialogScreen):
    def __init__(self, domain, name=None):
        self.domain, self.resource_name = domain, name
        self._reviewed = None
        super().__init__(
            "编辑配置" if name else "新建配置", confirm_text="预览 / 确认保存"
        )

    def field(self, label, identifier, value="", *, secret=False):
        yield Label(label)
        yield Input(value=text(value), id=identifier, password=secret)

    def json_field(self, label, identifier, value):
        yield Label(label)
        yield TextArea(
            json_text(value), id=identifier, classes="resource-json compact-json"
        )

    def compose_body(self) -> ComposeResult:
        cfg = self.app.config
        self.old = (
            get_resource(cfg, self.domain, self.resource_name)
            if self.resource_name
            else None
        )
        data = resource_input(cfg, self.domain, self.old) if self.old else {}
        yield from self.field("名称", "res-name", data.get("name"))
        if self.domain == "provider":
            yield from self.field(
                "服务商/兼容家族（可自定义）",
                "provider-type",
                data.get("provider_type", "openai"),
            )
            yield from self.field(
                "服务根地址", "provider-url", data.get("base_url"), secret=True
            )
            yield from self.field(
                "API Key（空值清空，掩码保留）",
                "provider-key",
                data.get("api_key"),
                secret=True,
            )
            yield from self.json_field(
                "连接选项 JSON；掩码保留整个对象",
                "provider-options",
                data.get("connection_options", {}),
            )
        elif self.domain == "endpoint":
            options = [(p.name, p.name) for p in cfg.list_providers()]
            yield Label("Provider")
            yield Select(
                options,
                id="endpoint-provider",
                value=data.get(
                    "provider_name", options[0][1] if options else Select.NULL
                ),
            )
            yield from self.field(
                "实际请求模型标识", "endpoint-model", data.get("model_name")
            )
            yield from self.field(
                "模型用途（可自定义）", "endpoint-type", data.get("model_type", "llm")
            )
            yield from self.field(
                "接口格式（如 openai.chat_completions / openai.responses，可自定义）",
                "endpoint-format",
                data.get("api_format", "openai.chat_completions"),
            )
            yield Label("能力档案（留空使用用途默认值）")
            profiles = [
                (p.display_name + " / " + p.model_key, p.model_key)
                for p in cfg.catalog_list(model_type=data.get("model_type", "llm"))
            ]
            yield Select(
                profiles,
                id="endpoint-profile",
                value=data.get("capabilities_key") or Select.NULL,
            )
        else:
            yield from self.field(
                "transport（stdio / streamable_http / 自定义）",
                "mcp-transport",
                data.get("transport", "stdio"),
            )
            settings = data.get("settings", {})
            known = settings if isinstance(settings, dict) else {}
            with Vertical(id="stdio-fields", classes="settings-form"):
                yield from self.field(
                    "启动命令（只保存，不执行）",
                    "mcp-command",
                    known.get("command"),
                    secret=True,
                )
                yield from self.json_field(
                    "args JSON 数组", "mcp-args", known.get("args", [])
                )
                yield from self.json_field(
                    "env JSON 对象", "mcp-env", known.get("env", {})
                )
                yield from self.field(
                    "cwd（可空）", "mcp-cwd", known.get("cwd"), secret=True
                )
            with Vertical(id="http-fields", classes="settings-form"):
                yield from self.field(
                    "HTTP(S) URL", "mcp-url", known.get("url"), secret=True
                )
                yield from self.json_field(
                    "headers JSON 对象", "mcp-headers", known.get("headers", {})
                )
                yield from self.field(
                    "凭据引用（仅描述，由宿主处理）",
                    "mcp-credential",
                    known.get("credential_ref"),
                    secret=True,
                )
                yield from self.field(
                    "协议版本提示（可空）",
                    "mcp-version",
                    known.get("protocol_version_hint"),
                )
            with Vertical(id="known-timeout", classes="settings-form"):
                yield from self.field(
                    "连接超时秒数（正数，可空）",
                    "mcp-timeout",
                    known.get("connect_timeout"),
                )
            with Vertical(id="custom-fields", classes="settings-form"):
                yield from self.json_field(
                    "自定义连接 JSON；宿主自行支持",
                    "mcp-custom",
                    settings
                    if data.get("transport") not in {"stdio", "streamable_http", None}
                    else {},
                )
        yield Label("敏感字段默认隐藏；掩码保留原值，null/空字段按类型清空。")
        yield TextArea(
            "先预览，再确认保存。",
            read_only=True,
            id="resource-preview",
            classes="resource-json",
        )

    def on_mount(self):
        if self.domain == "mcp":
            self.refresh_transport()

    def refresh_transport(self):
        transport = self.query_one("#mcp-transport", Input).value
        self.query_one("#stdio-fields").display = transport == "stdio"
        self.query_one("#http-fields").display = transport == "streamable_http"
        self.query_one("#known-timeout").display = transport in {
            "stdio",
            "streamable_http",
        }
        self.query_one("#custom-fields").display = transport not in {
            "stdio",
            "streamable_http",
        }

    def on_input_changed(self, event):
        if not self.is_mounted:
            return
        if event.input.id == "mcp-transport":
            self.refresh_transport()
        elif event.input.id == "endpoint-type":
            profiles = (
                [
                    (p.display_name + " / " + p.model_key, p.model_key)
                    for p in self.app.config.catalog_list(model_type=event.value)
                ]
                if event.value.strip()
                else []
            )
            select = self.query_one("#endpoint-profile", Select)
            old = select.value
            select.set_options(profiles)
            select.value = old if old in {key for _, key in profiles} else Select.NULL

    def value(self, identifier):
        return self.query_one("#" + identifier, Input).value

    def json_value(self, identifier):
        return read_json(self.query_one("#" + identifier, TextArea).text)

    def payload(self):
        data = {"name": self.value("res-name")}
        if self.domain == "provider":
            data.update(
                provider_type=self.value("provider-type"),
                base_url=self.value("provider-url"),
                api_key=self.value("provider-key") or None,
                connection_options=self.json_value("provider-options"),
            )
        elif self.domain == "endpoint":
            provider = self.query_one("#endpoint-provider", Select).value
            if provider is Select.NULL:
                raise ValueError("请先创建并选择 Provider")
            profile = self.query_one("#endpoint-profile", Select).value
            data.update(
                provider_name=provider,
                model_name=self.value("endpoint-model"),
                model_type=self.value("endpoint-type"),
                api_format=self.value("endpoint-format"),
                capabilities_key=None if profile is Select.NULL else profile,
            )
        else:
            transport = self.value("mcp-transport")
            if transport == "stdio":
                settings = dict(
                    command=self.value("mcp-command"),
                    args=self.json_value("mcp-args"),
                    env=self.json_value("mcp-env"),
                    cwd=self.value("mcp-cwd") or None,
                )
            elif transport == "streamable_http":
                settings = dict(
                    url=self.value("mcp-url"),
                    headers=self.json_value("mcp-headers"),
                    credential_ref=self.value("mcp-credential") or None,
                    protocol_version_hint=self.value("mcp-version") or None,
                )
            else:
                settings = self.json_value("mcp-custom")
            if transport in {"stdio", "streamable_http"}:
                timeout = self.value("mcp-timeout")
                try:
                    settings["connect_timeout"] = float(timeout) if timeout else None
                except ValueError:
                    raise ValueError("连接超时必须为正数或空值") from None
            data.update(transport=transport, settings=settings)
        return data

    def on_confirm(self):
        try:
            candidate, patch = validate_input(
                self.app.config, self.domain, self.payload(), self.old
            )
            digest = hashlib.sha256(
                json.dumps(self.payload(), sort_keys=True).encode()
            ).hexdigest()
            if digest != self._reviewed:
                self.query_one("#resource-preview", TextArea).load_text(
                    candidate.model_dump_json(indent=2)
                )
                self._reviewed = digest
                return
            apply_resource(self.app.config, self.domain, patch, old=self.old)
            self.dismiss(True)
            self.app.show_toast("配置已保存")
        except (AgentsConfigError, ValueError, TypeError) as exc:
            self._flash_error(str(exc))


class ResourceScreen(Container):
    def __init__(self, domain):
        self.domain = domain
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Input(placeholder="按名称筛选", id=self.domain + "-filter")
        yield DataTable(id=self.domain + "-table", cursor_type="row")
        with Horizontal(classes="resource-actions"):
            for action, label in [
                ("view", "查看"),
                ("create", "新建"),
                ("edit", "编辑"),
                ("delete", "删除"),
            ]:
                yield Button(label, id=self.domain + "-" + action)
            if self.domain != "provider":
                yield Button("解析预览", id=self.domain + "-resolve")

    def on_mount(self):
        self.query_one(DataTable).add_columns(
            "名称", "用途 / transport / 服务商", "接口格式", "revision"
        )
        self.refresh_resources()

    def refresh_resources(self):
        table = self.query_one(DataTable)
        table.clear()
        search = self.query_one(Input).value.casefold()
        for record in list_resources(self.app.config, self.domain):
            if search not in record.name.casefold():
                continue
            kind = getattr(
                record,
                "model_type",
                getattr(record, "transport", getattr(record, "provider_type", "")),
            )
            table.add_row(
                record.name,
                kind,
                getattr(record, "api_format", ""),
                str(record.revision),
                key=record.name,
            )

    def on_input_changed(self, event):
        if self.is_mounted:
            self.refresh_resources()

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id.removeprefix(self.domain + "-")
        table = self.query_one(DataTable)
        name = (
            table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            if table.row_count
            else None
        )
        if action == "create":
            self.app.push_screen(
                ResourceEditDialog(self.domain), lambda _: self.refresh_resources()
            )
        elif name:
            if action == "edit":
                self.app.push_screen(
                    ResourceEditDialog(self.domain, name),
                    lambda _: self.refresh_resources(),
                )
            elif action in {"view", "resolve"}:
                self.app.push_screen(
                    ResourceDetailDialog(self.domain, name, resolve=action == "resolve")
                )
            elif action == "delete":
                record = get_resource(self.app.config, self.domain, name)
                refs = (
                    self.app.config.provider_references(name)
                    if self.domain == "provider"
                    else []
                )

                def confirm(accepted):
                    if accepted:
                        try:
                            delete_resource(
                                self.app.config,
                                self.domain,
                                name,
                                record.revision,
                                expected_id=record.id,
                            )
                            self.refresh_resources()
                        except AgentsConfigError as exc:
                            self.app.show_toast(str(exc), error=True)

                self.app.push_screen(
                    ConfirmDialog("删除配置", name + "\n引用端点: " + ", ".join(refs)),
                    confirm,
                )
