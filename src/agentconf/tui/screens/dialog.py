# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""通用对话框组件"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class DialogScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
        Binding("ctrl+enter", "confirm", "确认", show=True),
    ]

    BASE_CSS = """
    DialogScreen {
        align: center middle;
    }
    #dialog {
        padding: 1 2;
        width: 72;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
    }
    #dialog-title {
        text-align: center;
        text-style: bold;
        width: 100%;
        margin-bottom: 1;
    }
    #dialog-body {
        width: 100%;
        height: 1fr;
    }
    #dialog-buttons {
        dock: bottom;
        width: 100%;
        height: auto;
        margin-top: 1;
        align: center middle;
    }
    #dialog-buttons Button {
        margin: 0 1;
    }
    """

    CSS = BASE_CSS

    def __init__(
        self,
        title: str,
        *,
        cancel_text: str = "取消",
        confirm_text: str = "确认",
    ) -> None:
        super().__init__()
        self._title = title
        self._cancel_text = cancel_text
        self._confirm_text = confirm_text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title, id="dialog-title")
            with Horizontal(id="dialog-buttons"):
                if self._cancel_text:
                    yield Button(self._cancel_text, variant="default", id="btn-cancel")
                yield Button(
                    self._confirm_text, variant="primary", id="btn-confirm"
                )
            with VerticalScroll(id="dialog-body"):
                yield from self.compose_body()

    def compose_body(self) -> ComposeResult:
        yield from ()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-confirm":
            self.on_confirm()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        self.on_confirm()

    def on_confirm(self) -> None:
        self.dismiss(None)

    def _flash_error(self, message: str) -> None:

        existing = self.query("#dialog-error")
        for w in existing:
            w.remove()
        error = Static(message, id="dialog-error", classes="error-toast")
        dialog = self.query_one("#dialog", Vertical)
        dialog.mount(error)
        self.set_timer(2.5, error.remove)


class ConfirmDialog(DialogScreen):
    def compose_body(self) -> ComposeResult:
        yield Label(getattr(self, "_message", ""), id="confirm-message")

    def __init__(self, title: str, message: str) -> None:
        self._message = message
        super().__init__(title, confirm_text="确认删除")

    def on_confirm(self) -> None:
        self.dismiss(True)
