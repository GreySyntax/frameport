"""Settings: the portable toolchain (incl. Revive), this PC for PC VR games, data folder, about."""
from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from ... import REPO_URL, __version__
from ...core.paths import user_data_dir
from ...recommend import catalog
from .. import components as C
from .. import theme as T

if TYPE_CHECKING:
    from ..app import FramePortApp

TOOL_TITLES = {"java": "Java runtime", "overport": "overport", "apksigner": "apksigner", "revive": "Revive"}
TOOL_WHY = {"java": "Runs overport and apksigner", "overport": "Converts Quest games to OpenXR",
            "apksigner": "Signs rebuilt games", "revive": "Runs Oculus Rift games on OpenXR"}


class SettingsView:
    def __init__(self, app: FramePortApp):
        self.app = app
        self.tools = ft.Column(spacing=0)
        self.pc = ft.Column(spacing=0)

    def fill_tools(self, check_latest=False):
        from ...tools import toolchain

        rows = []
        for s in toolchain.status(check_latest):
            newer = s.latest and s.version and s.latest != s.version and s.version not in ("external", "system")
            detail = TOOL_WHY.get(s.name, "")
            if s.installed:
                detail += f" · {s.version or 'installed'}" + (f" (update: {s.latest})" if newer else "")
                if s.name == "revive" and "using" in (s.detail or ""):
                    detail += " · " + s.detail.split("·")[-1].strip()
            else:
                detail += " · downloaded when first needed" if s.optional else " · not installed yet"
            rows.append(C.status_row(True if s.installed else (None if s.optional else False),
                                     TOOL_TITLES.get(s.name, s.name), detail,
                                     help="revive" if s.name == "revive" else None))
        self.tools.controls = rows
        C.update(self.tools)

    def fill_pc(self):
        from ...core import winhost

        if not winhost.available():
            self.pc.controls = [C.status_row(None, "Windows not detected",
                                             "PC VR games can run on this PC only with Windows (or WSL on Windows)")]
        else:
            try:
                from ...targets.pc_revive import PcReviveTarget

                d = PcReviveTarget().describe()
                self.pc.controls = [
                    C.status_row(bool(d["steam"]), "Steam", "Found" if d["steam"] else "Steam for Windows not found"),
                    C.status_row(d["steamvr"] or None, "SteamVR",
                                 "Installed" if d["steamvr"] else "Install SteamVR from Steam to play PC VR games",
                                 help="steamvr_pc"),
                    C.status_row(bool(d["revive"]), "Revive", (f"{d['revive_version']} · {d['revive']}"
                                                              if d["revive"] else "Downloaded when first needed"),
                                 help="revive"),
                ]
            except Exception as exc:  # noqa: BLE001
                self.pc.controls = [C.status_row(False, "Couldn't check this PC", str(exc))]
        C.update(self.pc)

    def installing(self) -> ft.Control:
        from ...core import library

        def changed(e):
            library.set_setting("install.launch_test", bool(e.control.value))
        return C.switch("Launch test after installing on the Frame (starts the game once without the headset "
                               "and checks its log)", value=bool(library.setting("install.launch_test", True)),
                         on_change=changed)

    def updates_card(self) -> ft.Control:
        """Settings → Updates: FramePort's own updates (ui/updater.py, frameport/updates.py)."""
        import time

        from ... import updates
        from ...core import library

        app = self.app
        last = library.setting("update.last_check")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "never"
        found = app.updater.found
        status = (C.callout(ft.Row([C.body(f"FramePort {found.version} is available.", T.TEXT, expand=True),
                                    C.primary("Update now", ft.Icons.SYSTEM_UPDATE_ROUNDED,
                                              lambda e: app.updater.install())], spacing=T.S3), "info")
                  if found else C.meta(f"You have the latest version as of the last check ({when})."))

        def auto_check(e):
            library.set_setting("update.auto_check", bool(e.control.value))

        def auto_install(e):
            library.set_setting("update.auto_install", bool(e.control.value))
        kind = {"bundle": "the downloaded app", "source": "a source checkout (git pull + uv sync)",
                "wheel": "an installed Python package (reinstalled from the release)"}[updates.install_kind()]
        return ft.Column([
            ft.Row([C.kv("Installed", f"FramePort {__version__} · {kind}"),
                    ft.Container(expand=True),
                    C.secondary("Check for updates", ft.Icons.REFRESH_ROUNDED, lambda e: app.updater.check_now())],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            status,
            C.switch("Check for new versions automatically", value=bool(library.setting("update.auto_check", True)),
                      on_change=auto_check),
            C.switch("Install updates automatically (downloads in the background, installs when FramePort "
                            "next starts)", value=bool(library.setting("update.auto_install", False)),
                      on_change=auto_install),
        ], spacing=T.S3)

    def appearance(self) -> ft.Control:
        from ...core import library

        current = library.setting("ui.scale", "auto")
        auto = T.detect_scale()
        options = [ft.dropdown.Option("auto", f"Automatic ({auto:.0%})")] + [
            ft.dropdown.Option(str(f), f"{f:.0%}") for f in T.SCALE_CHOICES]
        note = C.meta(f"Now {T.SCALE:.0%}. Changes apply the next time FramePort starts.")

        def changed(e):
            library.set_setting("ui.scale", e.control.value)
            new = T.scale_from_setting(e.control.value)
            note.value = (f"Now {T.SCALE:.0%}; {new:.0%} after restarting FramePort." if abs(new - T.SCALE) > 0.01
                          else f"Now {T.SCALE:.0%}.")
            C.update(note)

        dd = ft.Dropdown(label="Text and layout size", value=str(current) if current != "auto" else "auto",
                         options=options, width=T.px(260), on_select=changed)
        return ft.Column([dd, note], spacing=T.S2)

    def agent_text(self) -> str:
        """The agent version this app ships, and the one on the connected Frame (it's replaced on the next command
        whenever the files differ)."""
        from ...frame.connection import bundled_agent_version

        mine = bundled_agent_version()
        text = f"v{mine}" if mine else "unknown"
        info = self.app.frame_info or {}
        remote = info.get("agent_version")
        if self.app.frame_state == "connected" and remote:
            text += " · on the Frame: " + (f"v{remote}" if remote == mine else f"v{remote} (updates on the next action)")
        else:
            text += " · Frame not connected"
        return text

    def build(self) -> ft.Control:
        app = self.app
        self.tools.controls = [ft.Row([ft.ProgressRing(width=T.px(16), height=T.px(16), stroke_width=T.px(2), color=T.ACCENT),
                                       C.meta("Checking tools…")], spacing=T.S2)]
        app.run_bg(self.fill_tools)
        self.pc.controls = [ft.Row([ft.ProgressRing(width=T.px(16), height=T.px(16), stroke_width=T.px(2), color=T.ACCENT),
                                    C.meta("Checking this PC…")], spacing=T.S2)]
        app.run_bg(self.fill_pc)
        from ... import __version__ as ver
        data = str(user_data_dir())
        return ft.Column([
            app.top_bar("Settings", "Updates, tools, this PC and where FramePort keeps its data"),
            C.section("Updates", C.card(self.updates_card(), padding=T.S4), help="app_updates"),
            C.section("Tools", C.card(self.tools, padding=ft.Padding(T.S4, T.S2, T.S4, T.S2)),
                      subtitle="FramePort manages its own copies; nothing is installed system-wide",
                      action=ft.Row([C.ghost("Update tools", ft.Icons.UPDATE_ROUNDED,
                                             lambda e: app.update_tools(update=True)),
                                     C.secondary("Install missing", ft.Icons.DOWNLOAD_ROUNDED,
                                                 lambda e: app.update_tools())], spacing=T.S2)),
            C.section("This PC (for PC VR games)", C.card(self.pc, padding=ft.Padding(T.S4, T.S2, T.S4, T.S2))),
            C.section("Data", C.card(ft.Column([
                C.kv("Data folder", ft.Row([C.body(data, T.TEXT, selectable=True, expand=True),
                                            C.icon_btn(ft.Icons.CONTENT_COPY_ROUNDED, "Copy path",
                                                       lambda e: app.copy(data))]), "data_folder"),
                C.kv("Catalog", f"{len(catalog.load())} known-good recipes (bundled, remote and yours)", "catalog"),
            ], spacing=T.S2))),
            C.section("Problems & feedback", C.card(ft.Row([
                C.body("Something not working? Collect a diagnostics zip (logs, settings, device info; personal "
                       "data removed) and attach it to a GitHub issue. For one game, use its menu instead.",
                       expand=True),
                C.ghost("Collect app logs", ft.Icons.FOLDER_ZIP_OUTLINED, lambda e: app.collect_logs()),
                C.secondary("Report a problem…", ft.Icons.BUG_REPORT_OUTLINED, lambda e: app.report_problem_dialog()),
            ], spacing=T.S3)), help="diag_bundle"),
            C.section("Remove FramePort", C.card(ft.Row([
                C.body("Removes everything FramePort created: its data and tools on this PC, the Steam entries it "
                       "added, and (optionally) its games and files on the Frame. Your game dumps aren't touched.",
                       expand=True),
                ft.OutlinedButton("Uninstall FramePort…", icon=ft.Icons.DELETE_FOREVER_ROUNDED,
                                  on_click=lambda e: app.uninstall_app(),
                                  style=ft.ButtonStyle(color=T.ERROR, side=ft.BorderSide(1, T.soft(T.ERROR, 0.6)),
                                                       shape=ft.RoundedRectangleBorder(radius=T.RADIUS_SM))),
            ], spacing=T.S4))),
            C.section("Installing", C.card(self.installing(), padding=T.S4), help="launch_test"),
            C.section("Appearance", C.card(self.appearance(), padding=T.S4), help="ui_scale"),
            C.section("About", C.card(ft.Column([
                C.kv("Version", ver),
                C.kv("Frame agent", self.agent_text(), "frame_agent"),
                C.kv("Source", ft.TextButton(REPO_URL.removeprefix("https://"), icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                             url=REPO_URL)),
                C.meta("Uses overport, Revive (LibreVR), Valve's Lepton and Proton. Not affiliated with Valve or Meta."),
            ], spacing=T.S2))),
        ], spacing=T.S5, scroll=ft.ScrollMode.AUTO, expand=True)
