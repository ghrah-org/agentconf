# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""agentconf TUI 主应用"""

from __future__ import annotations


from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Label, TabbedContent, TabPane

from agentconf import AgentsConfig


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
        self._config = AgentsConfig(db_path=self._db_path)

    @property
    def config(self) -> AgentsConfig:
        return self._config

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Provider", id="tab-provider"):
                from agentconf.tui.screens.provider_screens import ProviderScreen
                yield ProviderScreen()
            with TabPane("Model", id="tab-model"):
                from agentconf.tui.screens.model_screens import ModelScreen
                yield ModelScreen()
            with TabPane("Agent", id="tab-agent"):
                from agentconf.tui.screens.agent_screens import AgentScreen
                yield AgentScreen()
            with TabPane("Resolve", id="tab-resolve"):
                from agentconf.tui.screens.resolve_screen import ResolveScreen
                yield ResolveScreen()
        db_display = self._db_path or AgentsConfig.DEFAULT_DB_PATH
        yield StatusBar(f" 已连接: {db_display}", id="status-bar")
        yield Footer()

    def on_unmount(self) -> None:
        self._config.close()

    def show_toast(self, message: str, *, error: bool = False) -> None:
        from textual.widgets import Static

        cls = "error-toast" if error else "success-toast"
        toast = Static(message, classes=cls, id="toast")
        self.mount(toast)
        self.set_timer(2.5, toast.remove)
