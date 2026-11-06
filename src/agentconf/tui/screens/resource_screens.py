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
    resource_references,
)
from agentconf.tui.screens.dialog import DialogScreen, ConfirmDialog
from agentconf.tui.widgets.connection_editors import CollectionEditor, BindingEditor


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
        if self.domain in {"provider", "mcp-provider"}:
            yield Label(
                "引用: "
                + str(resource_references(cfg, self.domain, self.resource_name)),
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
        raw = (
            resource_input(cfg, self.domain, self.old, show_secrets=True)
            if self.old
            else {}
        )
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
        elif self.domain == "mcp-provider":
            yield from self.field(
                "厂商／账号说明（可空）", "mcp-provider-vendor", data.get("vendor")
            )
            yield from self.field(
                "API Key（掩码保留，空值清空）",
                "mcp-provider-key",
                data.get("api_key"),
                secret=True,
            )
            yield Label("共享默认认证方式")
            yield BindingEditor(data.get("default_binding"), id="provider-binding")
        else:
            transport = data.get("transport", "stdio")
            yield Label("传输方式")
            yield Select(
                [
                    ("标准输入输出（stdio）", "stdio"),
                    ("Streamable HTTP", "streamable_http"),
                    ("自定义", "custom"),
                ],
                value=transport
                if transport in {"stdio", "streamable_http"}
                else "custom",
                allow_blank=False,
                id="mcp-transport",
            )
            with Vertical(id="custom-transport-field", classes="settings-form"):
                yield from self.field(
                    "自定义 transport 名称",
                    "mcp-custom-transport",
                    transport if transport not in {"stdio", "streamable_http"} else "",
                )
            known = raw.get("settings", {})
            safe_settings = (
                data.get("settings") if isinstance(data.get("settings"), dict) else {}
            )
            with Vertical(id="stdio-fields", classes="settings-form"):
                yield from self.field(
                    "启动命令（只保存，不执行）",
                    "mcp-command",
                    safe_settings.get("command"),
                    secret=True,
                )
                yield Label("命令参数（逐项添加／删除）")
                yield CollectionEditor(
                    known.get("args", []), mapping=False, id="mcp-args"
                )
                yield Label("环境变量")
                yield CollectionEditor(known.get("env", {}), id="mcp-env")
                yield from self.field(
                    "cwd（可空）",
                    "mcp-cwd",
                    safe_settings.get("cwd"),
                    secret=True,
                )
            with Vertical(id="http-fields", classes="settings-form"):
                yield from self.field(
                    "HTTP(S) URL",
                    "mcp-url",
                    safe_settings.get("url"),
                    secret=True,
                )
                yield Label("连接 headers（认证头由下方认证方式生成）")
                yield CollectionEditor(known.get("headers", {}), id="mcp-headers")
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
                    "自定义连接 JSON",
                    "mcp-custom",
                    data.get("settings", {})
                    if transport not in {"stdio", "streamable_http"}
                    else {},
                )
            auth = data.get("auth", {"mode": "none"})
            yield Label("认证来源")
            yield Select(
                [
                    ("无需认证", "none"),
                    ("MCP Provider", "provider"),
                    ("静态认证（存于 headers／env）", "inline"),
                    ("高级宿主引用", "external"),
                ],
                value=auth["mode"],
                allow_blank=False,
                id="mcp-auth-mode",
            )
            with Vertical(id="mcp-auth-provider", classes="settings-form"):
                yield Label("MCP Provider")
                yield Select(
                    [(p.name, p.id) for p in cfg.list_mcp_providers()],
                    value=auth.get("provider_id", Select.NULL),
                    id="mcp-provider-select",
                )
                yield Button("创建 MCP Provider", id="mcp-new-provider")
                yield Label("", id="mcp-provider-hint", markup=False)
                yield Select(
                    [("继承 Provider", "inherit"), ("显式覆盖认证方式", "override")],
                    value=auth.get("binding_mode", "inherit"),
                    allow_blank=False,
                    id="mcp-binding-mode",
                )
            with Vertical(id="mcp-auth-external", classes="settings-form"):
                yield from self.field(
                    "宿主凭据引用（此库不解释）",
                    "mcp-external-ref",
                    auth.get("reference"),
                    secret=True,
                )
            yield BindingEditor(auth.get("binding"), id="mcp-auth-binding")
        yield Label("敏感项：掩码保留原值；空值清空。预览后再次确认保存。")
        yield TextArea(
            "先预览，再确认保存。",
            read_only=True,
            id="resource-preview",
            classes="resource-json",
        )

    def on_mount(self):
        self.query_one("#resource-preview").display = False
        if self.domain == "mcp":
            self.refresh_transport()
            self.refresh_auth()

    def refresh_transport(self):
        transport = self.query_one("#mcp-transport", Select).value
        self.query_one("#stdio-fields").display = transport == "stdio"
        self.query_one("#http-fields").display = transport == "streamable_http"
        self.query_one("#known-timeout").display = transport in {
            "stdio",
            "streamable_http",
        }
        self.query_one("#custom-fields").display = transport == "custom"
        self.query_one("#custom-transport-field").display = transport == "custom"

    def refresh_auth(self):
        mode = self.query_one("#mcp-auth-mode", Select).value
        self.query_one("#mcp-auth-provider").display = mode == "provider"
        self.query_one("#mcp-auth-external").display = mode == "external"
        self.query_one("#mcp-auth-binding").display = mode == "external" or (
            mode == "provider"
            and self.query_one("#mcp-binding-mode", Select).value == "override"
        )
        selected = self.query_one("#mcp-provider-select", Select).value
        if selected is not Select.NULL:
            try:
                provider = self.app.config.get_mcp_provider_by_id(selected)
                binding = provider.default_binding
                description = (
                    f"{binding.name}：{binding.value_template}" if binding else "未设置"
                )
                self.query_one("#mcp-provider-hint", Label).update(
                    f"{provider.name}：{'密钥已配置' if provider.api_key else '缺少密钥'}；默认认证 {description}"
                )
            except AgentsConfigError:
                self.query_one("#mcp-provider-hint", Label).update(
                    "关联 Provider 已变化，请重新选择"
                )

    def on_select_changed(self, event):
        if self.domain == "mcp" and self.is_mounted:
            if event.select.id == "mcp-transport":
                self.refresh_transport()
            elif event.select.id in {
                "mcp-auth-mode",
                "mcp-provider-select",
                "mcp-binding-mode",
            }:
                self.refresh_auth()

    def on_button_pressed(self, event):
        if event.button.id == "mcp-new-provider":
            event.stop()

            def created(record):
                if record:
                    select = self.query_one("#mcp-provider-select", Select)
                    select.set_options(
                        [(p.name, p.id) for p in self.app.config.list_mcp_providers()]
                    )
                    select.value = record.id
                    self.refresh_auth()

            self.app.push_screen(ResourceEditDialog("mcp-provider"), created)

    def on_input_changed(self, event):
        if not self.is_mounted:
            return
        if event.input.id == "endpoint-type":
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
        elif self.domain == "mcp-provider":
            data.update(
                vendor=self.value("mcp-provider-vendor") or None,
                api_key=self.value("mcp-provider-key") or None,
                default_binding=self.query_one(
                    "#provider-binding", BindingEditor
                ).value(),
            )
        else:
            transport = self.query_one("#mcp-transport", Select).value
            if transport == "custom":
                transport = self.value("mcp-custom-transport")
                if not transport.strip() or transport in {"stdio", "streamable_http"}:
                    raise ValueError(
                        "请填写非空自定义 transport；已知方式请直接下拉选择"
                    )
            if transport == "stdio":
                settings = dict(
                    command=self.value("mcp-command"),
                    args=self.query_one("#mcp-args", CollectionEditor).value(),
                    env=self.query_one("#mcp-env", CollectionEditor).value(),
                    cwd=self.value("mcp-cwd") or None,
                )
            elif transport == "streamable_http":
                settings = dict(
                    url=self.value("mcp-url"),
                    headers=self.query_one("#mcp-headers", CollectionEditor).value(),
                    protocol_version_hint=self.value("mcp-version") or None,
                )
            else:
                settings = self.json_value("mcp-custom")
            mode = self.query_one("#mcp-auth-mode", Select).value
            auth = {"mode": mode}
            if mode == "provider":
                provider = self.query_one("#mcp-provider-select", Select).value
                if provider is Select.NULL:
                    raise ValueError("请选择或创建 MCP Provider")
                auth.update(
                    provider_id=provider,
                    binding_mode=self.query_one("#mcp-binding-mode", Select).value,
                )
                if auth["binding_mode"] == "override":
                    auth["binding"] = self.query_one(
                        "#mcp-auth-binding", BindingEditor
                    ).value()
            elif mode == "external":
                auth.update(
                    reference=self.value("mcp-external-ref"),
                    binding=self.query_one("#mcp-auth-binding", BindingEditor).value(),
                )
            data["auth"] = auth
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
                preview = self.query_one("#resource-preview", TextArea)
                preview.display = True
                preview.load_text(candidate.model_dump_json(indent=2))
                preview.scroll_visible()
                self._reviewed = digest
                return
            saved = apply_resource(self.app.config, self.domain, patch, old=self.old)
            self.dismiss(saved)
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
        with Vertical(classes="action-bar"):
            with Horizontal(classes="action-row"):
                for action, label in [
                    ("view", "查看"),
                    ("create", "新建"),
                    ("edit", "编辑"),
                    ("delete", "删除"),
                ]:
                    yield Button(label, id=self.domain + "-" + action)
                if self.domain not in {"provider", "mcp-provider"}:
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
            detail = getattr(record, "api_format", "")
            if self.domain == "mcp-provider":
                kind = record.vendor or "MCP"
                detail = (
                    ("密钥已配置" if record.api_key else "缺少密钥")
                    + " / "
                    + str(
                        record.default_binding.target
                        if record.default_binding
                        else "无默认认证"
                    )
                )
            elif self.domain == "mcp":
                detail = record.auth.mode
                if detail == "provider":
                    provider = self.app.config.get_mcp_provider_by_id(
                        record.auth.provider_id
                    )
                    detail = provider.name + " / " + record.auth.binding_mode
            table.add_row(
                record.name,
                kind,
                detail,
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
                    resource_references(self.app.config, self.domain, name)
                    if self.domain in {"provider", "mcp-provider"}
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
                    ConfirmDialog("删除配置", name + "\n引用: " + str(refs)),
                    confirm,
                )
