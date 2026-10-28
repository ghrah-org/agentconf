# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agentconf TUI 主应用"""

from __future__ import annotations


from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    TabbedContent,
    TabPane,
    TextArea,
)

from agentconf import AgentsConfig, MigrationError, MigrationRequiredError


class StatusBar(Label):
    DEFAULT_CLASSES = "status-bar"


class agentconfApp(App[None]):
    TITLE = "agentconf"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("q", "quit", "退出", show=True),
        Binding("ctrl+q", "quit", "退出", show=False),
    ]

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self._db_path = db_path
        self._migration_error = None
        try:
            self._config = AgentsConfig(db_path=db_path)
        except MigrationError as exc:
            self._config = None
            self._migration_error = str(exc)

    @property
    def config(self) -> AgentsConfig:
        if self._config is None:
            raise MigrationRequiredError(self._migration_error or "需要迁移")
        return self._config

    def compose(self) -> ComposeResult:
        yield Header()
        if self._config is None:
            yield Label(self._migration_error, markup=False)
            yield Label(
                "旧库不会自动升级。使用 migrate prepare --plan-out 生成计划，再用 migrate apply --plan 显式应用。"
            )
            yield Button("只读迁移检查", id="migration-preview")
            yield TextArea(read_only=True, id="migration-report")
            yield Footer()
            return
        with TabbedContent():
            for domain, label in (
                ("provider", "Provider"),
                ("endpoint", "模型端点"),
                ("mcp", "MCP"),
            ):
                from agentconf.tui.screens.resource_screens import ResourceScreen

                with TabPane(label, id="tab-" + domain):
                    yield ResourceScreen(domain)
            with TabPane("能力目录", id="tab-catalog"):
                from agentconf.tui.screens.catalog_screens import CatalogScreen

                yield CatalogScreen()
            with TabPane("迁移帮助", id="tab-migration"):
                yield Label(
                    "模型与 MCP 独立解析。旧 Agent 组合及请求参数请通过迁移导出后在下游配置。"
                )
                yield Label(
                    "旧库：migrate prepare --formats mappings.json --plan-out plan.json；核对后 migrate apply --plan plan.json。"
                )
        db_display = self._db_path or AgentsConfig.DEFAULT_DB_PATH
        yield StatusBar(f" 已连接: {db_display}", id="status-bar")
        yield Footer()

    def on_unmount(self) -> None:
        if self._config is not None:
            self._config.close()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "migration-preview":
            from agentconf.migrations import prepare_migration

            try:
                plan = prepare_migration(self._db_path or AgentsConfig.DEFAULT_DB_PATH)
                self.query_one("#migration-report", TextArea).load_text(
                    plan.model_dump_json(indent=2)
                )
            except (MigrationError, ValueError) as exc:
                self.query_one("#migration-report", TextArea).load_text(str(exc))

    def show_toast(self, message: str, *, error: bool = False) -> None:
        self.notify(message, severity="error" if error else "information", timeout=2.5)
