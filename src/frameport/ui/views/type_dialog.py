"""Type on Frame: this computer's keyboard becomes a keyboard on the Frame while the dialog is open (frame/keyboard.py).
Keys go to whatever has focus on the Frame: an app's text field, Steam, the desktop, a PC game."""
from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from ...errors import explain
from ...i18n import tr
from .. import components as C
from .. import theme as T

if TYPE_CHECKING:
    from ..app import FramePortApp


def show_type_dialog(app: FramePortApp) -> None:
    target = app.target
    if target is None or app.frame_state != "connected":
        app.toast(tr("Connect your Frame first"), error=True)
        return
    state = {"session": None, "closed": False}
    status = C.meta(tr("Connecting the keyboard…"))
    last = ft.Text("", size=T.px(28), weight=ft.FontWeight.W_600, color=T.ACCENT)
    hint = C.body(tr("Click here, then type. Everything you type goes to the Frame (Esc and shortcuts too)."), T.TEXT)

    def forward(action):
        def handler(e):
            s = state["session"]
            if s is None or state["closed"]:
                return
            try:
                if s.key(e.key, action) and action == "down":
                    last.value = e.key if len(e.key) > 1 else e.key.upper()
                    C.update(last)
            except Exception as exc:  # noqa: BLE001 - the connection dropped
                fail(exc)
        return handler

    pad = ft.Container(ft.Column([hint, last], spacing=T.S2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                       padding=T.S5, border_radius=T.RADIUS, bgcolor=T.BG, border=ft.Border.all(1, T.BORDER),
                       alignment=ft.Alignment.CENTER, height=T.px(150), ink=True)
    listener = ft.KeyboardListener(pad, autofocus=True, on_key_down=forward("down"), on_key_up=forward("up"),
                                   on_key_repeat=forward("repeat"))
    pad.on_click = lambda e: focus_keys()
    paste = ft.TextField(hint_text=tr("Or paste text to type it in one go"), expand=True, dense=True,
                         border_color=T.BORDER, on_submit=lambda e: send_text(None))

    def focus_keys():
        try:
            app.page.run_task(listener.focus)
        except Exception:  # noqa: BLE001
            pass

    def send_text(e):
        s, text = state["session"], paste.value or ""
        if s is None or not text:
            return

        def work():
            skipped = s.text(text)
            paste.value = ""
            C.update(paste)
            focus_keys()
            if skipped:
                app.toast(tr("Typed it, except characters the Frame's US keyboard layout doesn't have: {chars}")
                          .format(chars=skipped), error=True)
        app.run_bg(work)

    def fail(exc):
        status.value = tr("Keyboard disconnected: {error}").format(error=explain(exc))
        status.color = T.ERROR
        C.update(status)

    def close(e=None):
        state["closed"] = True
        app._typing_on_frame = False
        s = state["session"]
        if s is not None:
            s.close()
        if e is not None:
            app.page.pop_dialog()

    pick = C.one_choice()
    app._typing_on_frame = True  # app._on_key leaves Esc / Ctrl+F alone: they go to the Frame
    app.page.show_dialog(ft.AlertDialog(
        modal=True, bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
        title=ft.Row([ft.Icon(ft.Icons.KEYBOARD_ROUNDED, color=T.ACCENT),
                      ft.Text(tr("Type on Frame"), weight=ft.FontWeight.W_600)], spacing=T.S2),
        content=ft.Column([
            C.body(tr("This computer's keyboard works as a keyboard on your Frame while this window is open. In the "
                      "headset, select a text field (in a game or app, in Steam or on the desktop), then type here."),
                   T.TEXT_2),
            listener,
            ft.Row([paste, C.secondary(tr("Type it"), ft.Icons.SEND_ROUNDED, send_text)], spacing=T.S2),
            status,
        ], tight=True, spacing=T.S3, width=T.px(560)),
        on_dismiss=pick(lambda e: close()),
        actions=[C.primary(tr("Done"), ft.Icons.CHECK_ROUNDED, on_click=pick(close))]))

    def connect():
        from ...frame.keyboard import KeyboardSession

        try:
            state["session"] = KeyboardSession(target.frame)
        except Exception as exc:  # noqa: BLE001
            fail(exc)
            return
        if state["closed"]:  # closed while connecting
            state["session"].close()
            return
        status.value = tr("Keyboard connected to {label}.").format(label=target.label)
        status.color = T.OK
        C.update(status)
        focus_keys()
    app.run_bg(connect)
