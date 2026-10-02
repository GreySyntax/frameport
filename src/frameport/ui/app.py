"""FramePort desktop GUI (Flet). The shell: sidebar (navigation, Frame connection, activity), routing, background
jobs and the actions views call. Views live in ui/views/, styling in ui/theme.py + ui/components.py.

    Library → Game (one click: patch/check → install → add to Steam → launch test) · Frame · Settings · Welcome

The UI only calls `frameport.pipeline`, the targets and the toolchain; long work runs as queued background jobs
(ui/jobs.py) shown in the activity panel.
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

import flet as ft

from .. import pipeline
from ..core import applog, library
from ..recommend import catalog
from . import components as C
from . import theme as T
from .components import install_state  # noqa: F401  (re-exported: tests and older callers import it from here)
from .jobs import Job, JobManager

NAV = [("library", "Library", ft.Icons.GRID_VIEW_ROUNDED), ("frame", "Steam Frame", ft.Icons.VIEW_IN_AR_ROUNDED),
       ("files", "Files", ft.Icons.FOLDER_OPEN_ROUNDED), ("settings", "Settings", ft.Icons.TUNE_ROUNDED)]
POLL_SECONDS = 30


class FramePortApp:
    def __init__(self, page: ft.Page):
        from .views.activity import ActivityPanel
        from .views.library import load_filters

        self.page = page
        self.target = None  # FrameLeptonTarget when connected
        self.frame_info: dict | None = None
        self.frame_state = "none"  # none | connecting | connected | offline
        self.pairing = None
        self.route: tuple = ("library",)
        self.lib_filters = load_filters()
        self.search_field: ft.TextField | None = None
        self.welcome_started = False
        self._pc_cache: tuple[float, dict] | None = None
        self.library_view = None  # created once (views/library.LibraryView), re-mounted on every visit
        self.files_view = None  # likewise (views/files.FilesView): keeps the location/folder between visits
        self.exe_queue: list[str] = []  # games whose executable the user should confirm (after a scan)
        self._failures: list[Job] = []  # failed installs/tests, shown together when the queue is done
        self.jobs = JobManager(self._on_job)
        from .updater import Updater

        self.updater = Updater(self)  # new FramePort releases (sidebar card, Library bar, one-click update)

        page.title = "FramePort"
        T.apply(page)
        page.padding = 0
        page.window.min_width, page.window.min_height = 1000, 680
        page.on_keyboard_event = self._on_key
        if page.web:  # the library's right-click menu; otherwise the browser shows its own
            try:
                page.run_task(ft.BrowserContextMenu().disable)
            except Exception:  # noqa: BLE001
                pass
        self.body = ft.Container(expand=True, padding=ft.Padding(T.S6, T.S5, T.S5, 0))
        self.nav_col = ft.Column(spacing=T.px(2))
        self.conn_card = ft.Container()
        self.activity_card = ft.Container()
        self.activity = ActivityPanel(self)
        sidebar = ft.Container(ft.Column([
            ft.Container(ft.Row([
                ft.Container(ft.Icon(ft.Icons.VIEW_IN_AR_ROUNDED, size=T.px(18), color=T.ON_ACCENT), width=T.px(32), height=T.px(32),
                             border_radius=T.px(9), bgcolor=T.ACCENT, alignment=ft.Alignment.CENTER),
                ft.Text("FramePort", size=T.px(17), weight=ft.FontWeight.W_800, color=T.TEXT)], spacing=T.S3),
                padding=ft.Padding(T.S2, T.S2, 0, T.S5)),
            self.nav_col,
            ft.Container(expand=True),
            self.updater.card,
            self.activity_card,
            self.conn_card,
        ], spacing=T.S2), width=T.px(236), bgcolor=T.SIDEBAR, padding=T.S4,
            border=ft.Border(right=ft.BorderSide(1, T.BORDER)))
        page.add(ft.Row([sidebar, self.body, self.activity.root], expand=True, spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        from .views.welcome import needed

        self.go("welcome" if needed() else "library")
        threading.Thread(target=self._startup, daemon=True).start()
        threading.Thread(target=self._poll, daemon=True).start()
        self.updater.start()

    # ================================================================== shell
    def top_bar(self, heading: str, subtitle: str = "", actions: list[ft.Control] | None = None) -> ft.Control:
        return ft.Row([ft.Column([C.title(heading), C.body(subtitle)] if subtitle else [C.title(heading)], spacing=T.px(2),
                                 expand=True),
                       *(actions or [])], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=T.S3)

    def _build_sidebar_controls(self) -> None:
        """The sidebar's controls are created once and only their properties change: rebuilding them while a job
        reports progress (several times a second) swallowed clicks — the pressed control was gone on release."""
        nav = {}
        for key, label, icon in NAV:
            ic = ft.Icon(icon, size=T.px(20), color=T.TEXT_2)
            tx = ft.Text(label, size=T.px(14), weight=ft.FontWeight.W_500, color=T.TEXT_2, expand=True)
            badge = C.dot(T.OK, 7)
            badge.visible = False
            box = ft.Container(ft.Row([ic, tx, badge], spacing=T.S3), padding=ft.Padding(T.S3, T.px(10), T.S3, T.px(10)),
                               border_radius=T.RADIUS_SM, ink=True, on_click=lambda e, k=key: self.go(k))
            nav[key] = (box, ic, tx, badge)
        self.nav_col.controls = [v[0] for v in nav.values()]
        # activity card: a "running" layout and an "idle" layout, switched by visibility
        self._act_title = ft.Text("", size=T.px(12), weight=ft.FontWeight.W_600, color=T.TEXT, expand=True, max_lines=1,
                                  overflow=ft.TextOverflow.ELLIPSIS)
        self._act_pct = C.meta("")
        self._act_bar = C.progress_bar(None)
        self._act_stage = C.meta("", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self._act_running = ft.Column([
            ft.Row([ft.ProgressRing(width=T.px(14), height=T.px(14), stroke_width=T.px(2), color=T.ACCENT), self._act_title,
                    self._act_pct], spacing=T.S2), self._act_bar, self._act_stage], spacing=T.px(6), visible=False)
        self._act_idle_text = C.meta("No activity", expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self._act_idle = ft.Row([ft.Icon(ft.Icons.HISTORY_ROUNDED, size=T.px(16), color=T.TEXT_3), self._act_idle_text],
                                spacing=T.S2)
        self._act_box = ft.Container(ft.Column([self._act_running, self._act_idle], spacing=0), padding=T.S3,
                                     border_radius=T.RADIUS_SM, border=ft.Border.all(1, T.BORDER), ink=True,
                                     tooltip="Activity",
                                     on_click=lambda e: self.show_activity(not self.activity.open))
        self.activity_card.content = self._act_box
        # connection card
        self._conn_dot = C.dot(T.TEXT_3, 9)
        self._conn_name = C.body("Steam Frame", T.TEXT, weight=ft.FontWeight.W_600, max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS)
        self._conn_line = C.meta("Not set up")
        self._conn_extra = ft.Container(C.meta(""), visible=False, tooltip=C.tip(C.HELP["frame_summary"]))
        self.conn_card.content = ft.Container(ft.Row([
            ft.Stack([ft.Icon(ft.Icons.VIEW_IN_AR_ROUNDED, size=T.px(22), color=T.TEXT_2),
                      ft.Container(self._conn_dot, right=0, bottom=0)], width=T.px(24), height=T.px(24)),
            ft.Column([self._conn_name, self._conn_line, self._conn_extra], spacing=1, expand=True),
        ], spacing=T.S3), padding=T.S3, border_radius=T.RADIUS_SM, bgcolor=T.SURFACE, ink=True,
            border=ft.Border.all(1, T.BORDER), on_click=lambda e: self.go("frame"))
        self._nav = nav  # last: _refresh_sidebar (also called from job threads) treats it as "all built"

    def _refresh_sidebar(self, update: bool = True) -> None:
        if not hasattr(self, "_nav"):
            self._build_sidebar_controls()
        for key, (box, ic, tx, badge) in self._nav.items():
            on = self.route[0] == key or (key == "library" and self.route[0] == "game")
            box.bgcolor = T.ACCENT_SOFT if on else None
            ic.color = T.ACCENT if on else T.TEXT_2
            tx.color = T.TEXT if on else T.TEXT_2
            tx.weight = ft.FontWeight.W_600 if on else ft.FontWeight.W_500
            badge.visible = key == "frame" and self.frame_state == "connected"
        cur = self.jobs.current()
        queued = len(self.jobs.pending())
        self._act_running.visible, self._act_idle.visible = bool(cur), not cur
        self._act_box.bgcolor = T.SURFACE if cur else None
        self._act_box.border = ft.Border.all(1, T.ACCENT if cur else T.BORDER)
        if cur:
            self._act_title.value = cur.title
            self._act_pct.value = (f"{cur.fraction:.0%}" if cur.fraction is not None else "") + \
                (f" · {cur.speed.split(' · ')[0]}" if cur.speed else "")
            self._act_bar.value = cur.fraction
            self._act_stage.value = (cur.stage or "Starting…") + (f" · {queued} more queued" if queued else "")
        else:
            recent = next((j for j in self.jobs.recent(1)), None)
            self._act_idle_text.value = "No activity" if not recent else \
                f"{recent.title} · " + {"done": "done", "failed": "failed", "cancelled": "cancelled"}.get(recent.state,
                                                                                                         "")
            self._act_idle_text.color = T.ERROR if recent and recent.state == "failed" else T.TEXT_3
        st = self.frame_state
        color = {"connected": T.OK, "connecting": T.WARN, "offline": T.ERROR}.get(st, T.TEXT_3)
        self._conn_dot.bgcolor = color
        self._conn_name.value = (self.target.label if self.target else None) or self._saved_name() or "Steam Frame"
        self._conn_line.value = {"connected": "Connected", "connecting": "Connecting…", "offline": "Offline"}.get(
            st, "Not set up")
        self._conn_line.color = color
        if st == "connected" and self.frame_info:
            pr = (self.frame_info.get("proton") or {}).get("ready")
            self._conn_extra.content.value = ("Quest ✓" if self.frame_info.get("lepton") else "Quest ✗") + "   " + \
                ("PC VR ✓" if pr else "PC VR —")
            self._conn_extra.visible = True
        else:
            self._conn_extra.visible = False
        if update:
            C.update(self.nav_col, self.activity_card, self.conn_card)

    def _saved_name(self) -> str | None:
        from ..frame.connection import saved_targets

        s = saved_targets()
        return s[0].label if s else None

    def go(self, route: str, *args) -> None:
        self.route = (route, *args)
        self.render()

    def render(self) -> None:
        from .views.frame import FrameView
        from .views.game import GameView
        from .views.library import LibraryView
        from .views.settings import SettingsView
        from .views.welcome import WelcomeView

        kind = self.route[0]
        try:
            if kind == "library":
                if self.library_view is None:
                    self.library_view = LibraryView(self)
                view = self.library_view.mount()
            elif kind == "game":
                view = GameView(self, *self.route[1:]).build()
            elif kind == "frame":
                view = FrameView(self).build()
            elif kind == "files":
                from .views.files import FilesView

                if self.files_view is None:
                    self.files_view = FilesView(self)
                view = self.files_view.mount(*self.route[1:])
            elif kind == "settings":
                view = SettingsView(self).build()
            else:
                view = ft.Row([WelcomeView(self).build()], alignment=ft.MainAxisAlignment.CENTER, expand=True,
                              vertical_alignment=ft.CrossAxisAlignment.START)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            view = C.empty_state(ft.Icons.ERROR_OUTLINE_ROUNDED, "Something went wrong", str(exc),
                                 C.primary("Back to library", on_click=lambda e: self.go("library")))
        self.body.content = view
        self._refresh_sidebar(update=False)
        self.page.update()
        if kind == "library":
            self.library_view.refresh_async()

    def refresh_view(self) -> None:
        """Bring the visible view up to date after a state change without blocking: the library updates only the
        cards that changed; other views re-render."""
        if self.route[0] == "library" and self.library_view is not None:
            self._refresh_sidebar()
            self.library_view.refresh_async()
        elif self.route[0] in ("game", "frame", "welcome", "settings"):
            self.render()
        elif self.route[0] == "files" and (self.files_view is None or self.files_view.root is None
                                           or self.frame_state != "connected"):
            self.render()  # connected / disconnected: switch between the browser and "connect first"
        else:
            self._refresh_sidebar()

    def open_game(self, package: str, advanced: bool = False, show_all: bool = False) -> None:
        self.go("game", package, advanced, show_all)

    def navigate(self, index: int, **kw) -> None:  # older callers (scripts)
        self.go(("library", "frame", "settings")[index] if index < 3 else "library")

    def show_activity(self, on: bool) -> None:
        self.activity.set_open(on)
        self.page.update()

    def toast(self, message: str, error: bool = False, action: str | None = None, on_action=None) -> None:
        self.page.show_dialog(ft.SnackBar(
            ft.Text(message, color=T.TEXT), bgcolor=T.soft(T.ERROR, 0.9) if error else T.SURFACE_3,
            action=action, on_action=on_action, behavior=ft.SnackBarBehavior.FLOATING, width=T.px(520),
            shape=ft.RoundedRectangleBorder(radius=T.RADIUS_SM), duration=5000 if action else 3500))

    def run_bg(self, fn, *args) -> None:
        def wrapper():
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                applog.log.exception("background task failed")
                self.toast(f"{exc}", error=True)
        self.page.run_thread(wrapper)

    def copy(self, text: str) -> None:
        try:
            self.page.run_task(ft.Clipboard().set, text)
            self.toast("Copied")
        except Exception:  # noqa: BLE001
            self.toast("Copy failed; select the text instead", error=True)

    def _on_key(self, e: ft.KeyboardEvent) -> None:
        if e.key == "Escape" and self.activity.open:
            self.show_activity(False)
        elif e.key.upper() == "F" and (e.ctrl or e.meta) and self.route[0] == "library" and self.search_field:
            try:
                self.page.run_task(self.search_field.focus)
            except Exception:  # noqa: BLE001
                pass

    # ================================================================== jobs
    def _on_job(self, job: Job | None) -> None:
        self._refresh_sidebar()
        self.activity.refresh()
        if job and job.state in ("done", "failed", "cancelled") and job.finished and not getattr(job, "_handled", 0):
            job._handled = 1
            if job.kind == "app-update" and job.state != "done":
                self.updater.restart = None  # the update didn't get ready: don't quit
            self.updater.on_jobs_changed()  # an update waiting for the queue to empty installs now
            if job.kind in ("install", "test", "uninstall", "tool-frame") and self.target:
                self.refresh_frame(quiet=True)
            if job.kind == "tool-frame" and self.route[0] == "files" and self.files_view is not None:
                self.files_view.load()  # show what an upload added  # re-renders when the Frame's list changed (e.g. after an uninstall)
            if job.kind == "install" and job.package and job.package in self._records():
                self._record(job.package, to=job.to, state="paused" if job.state == "cancelled" else "failed",
                             error=job.error, stage=job.stage)
            summary = job.summary or {}
            if job.kind in ("install", "test") and (job.state == "failed" or
                                                    (job.state == "done" and summary.get("verdict") == "fail")):
                self._failures.append(job)
            if not any(j.active and j.kind in ("install", "test") for j in self.jobs.jobs) and self._failures:
                failures, self._failures = self._failures, []
                self.page.run_thread(lambda: self.show_failures(failures))
            if job.state == "done":
                msg = job.result if isinstance(job.result, str) else f"{job.title}: done"
                pkg = job.package
                self.toast(msg, action="Open" if pkg and self.route[:2] != ("game", pkg) else "Details",
                           on_action=(lambda e: self.open_game(pkg)) if pkg and self.route[:2] != ("game", pkg)
                           else (lambda e: self.show_activity(True)))
            elif job.state == "failed":
                self.toast(f"{job.title} failed: {job.error}", error=True, action="Details",
                           on_action=lambda e: self.show_activity(True))
            self.refresh_view()
            if job.kind == "scan" and self.exe_queue:
                self.page.run_thread(self.next_exe_choice)
        elif job and job.state == "running" and job.package and self.route[:2] == ("game", job.package) and \
                job.stage and getattr(job, "_shown_stage", None) != job.stage:
            job._shown_stage = job.stage  # keep the hero's progress button current
            self.render()
        elif job and job.state == "running" and job.package and self.route[0] == "library" and \
                not getattr(job, "_card_marked", 0):
            job._card_marked = 1  # "Working…" badge on the card
            self.refresh_view()

    def submit(self, title: str, run, package: str | None = None, kind: str = "task", open_panel: bool = False) -> Job:
        job = self.jobs.submit(Job(title, run, package, kind))
        if open_panel:
            self.show_activity(True)
        elif self.route[0] == "game":
            self.render()
        else:
            self.refresh_view()
        return job

    def job_followups(self, job: Job) -> list[ft.Control]:
        out = []
        summary = getattr(job, "summary", None)
        if job.kind == "install" and job.state in ("failed", "cancelled") and job.package and \
                job.package in self._records():
            out.append(C.primary("Resume", ft.Icons.PLAY_ARROW_ROUNDED,
                                 lambda e: self._submit_install(job.package, getattr(job, "to", "frame"))))
        if summary and summary.get("suggestions") and job.package:
            sugg = summary["suggestions"]
            out.append(C.primary("Apply suggested fixes & reinstall", ft.Icons.HEALING_ROUNDED,
                                 lambda e: (pipeline.apply_suggestions(job.package, sugg),
                                            self.install(job.package, getattr(job, "to", "frame")))))
        if job.package and job.state != "running" and library.game(job.package):
            out.append(C.ghost("Open game", ft.Icons.ARROW_FORWARD_ROUNDED, lambda e: self.open_game(job.package)))
        return out

    # ================================================================== game actions
    def pc_installs(self) -> dict:
        from ..targets.pc_revive import local_installs

        if not self._pc_cache or time.time() - self._pc_cache[0] > 2:
            self._pc_cache = (time.time(), local_installs())
        return self._pc_cache[1]

    def _title(self, pkg: str) -> str:
        from .views.library import display_title, twins

        g = library.game(pkg)
        return display_title(g, twins(library.games())) if g else pkg

    def install_options(self, g: dict) -> list[tuple]:
        """[(label, icon, on_click, disabled, tooltip)] — the first is the primary action."""
        pkg = g["package"]
        connected = self.frame_state == "connected"
        st = C.install_state(g, self.frame_info)
        frame_label = {"installed": "Reinstall on Frame", "outdated": "Update on Frame"}.get(st, "Install on Frame")
        blocked = (g.get("recipe") or {}).get("status") == "unsupported" and g.get("kind") != "rift"
        if blocked and connected:  # known blocker: still allowed (e.g. to try a fix), after a warning
            frame_label = {"installed": "Reinstall anyway", "outdated": "Update anyway"}.get(st, "Install anyway")
            return [(frame_label, ft.Icons.WARNING_AMBER_ROUNDED, lambda e: self.install_blocked(pkg), False,
                     "Marked \"Can't run\": " + ((g.get("recipe") or {}).get("notes") or "a known blocker"))]
        frame_opt = (frame_label, ft.Icons.VIEW_IN_AR_ROUNDED, lambda e: self.install(pkg, "frame"), False, None) \
            if connected else ("Connect your Frame", ft.Icons.LINK_ROUNDED, lambda e: self.go("frame"), False,
                               "Set up the connection to your Steam Frame first")
        if g.get("kind") != "rift":
            return [frame_opt]
        from ..core import winhost

        on_pc = pkg in self.pc_installs()
        stale = on_pc and C.pc_outdated(g, self.pc_installs()[pkg])
        pc_opt = ("Update on this PC" if stale else "Reinstall on this PC" if on_pc else "Install on this PC",
                  ft.Icons.COMPUTER_ROUNDED, lambda e: self.install(pkg, "pc"), not winhost.available(),
                  "The launch settings changed since it was installed: update the Steam shortcut" if stale else
                  None if winhost.available() else "Needs Windows (or WSL on Windows)")
        return [frame_opt, pc_opt] if connected or not winhost.available() else [pc_opt, frame_opt]

    def play_options(self, g: dict) -> list[tuple]:
        """[(label, icon, on_click, disabled, tooltip)] for where the game is installed and can be started now."""
        pkg = g["package"]
        out = []
        if self.frame_state == "connected" and C.install_state(g, self.frame_info) in ("installed", "outdated"):
            out.append(("Play on Frame", ft.Icons.PLAY_ARROW_ROUNDED, lambda e: self.play(pkg, "frame"), False,
                        "Starts the game through the Frame's Steam — put the headset on"))
        if g.get("kind") == "rift" and pkg in self.pc_installs():
            out.append(("Play on this PC", ft.Icons.PLAY_ARROW_ROUNDED, lambda e: self.play(pkg, "pc"), False,
                        "Starts the game through Steam on this PC (SteamVR + Revive)"))
        return out

    def play(self, pkg: str, to: str = "frame") -> None:
        title = self._title(pkg)

        def work():
            res = self._target_for(to).launch(pkg) or {}
            library.upsert_game(pkg, last_played=time.time())
            if to == "frame":
                self.toast(f"Starting {title} on the Frame — put the headset on")
            elif res.get("steamvr") is False:
                self.toast(f"Starting {title}, but SteamVR isn't running — it may open as a flat window. Start SteamVR "
                           "and relaunch.", error=True)
            else:
                self.toast(f"Starting SteamVR and {title} — put your headset on")
        self.run_bg(work)

    def quick_action(self, g: dict) -> tuple[str | None, str | None]:
        play = self.play_options(g)
        if play and not self.jobs.busy_with(g["package"]):
            return "Play", play[0][1]
        opts = [o for o in self.install_options(g) if o[2] and not o[3]]
        if self.jobs.busy_with(g["package"]) or not opts:
            return None, None
        label = opts[0][0].replace(" on Frame", "").replace(" on this PC", " on PC")
        return (label if not label.startswith("Connect") else None), opts[0][1]

    def primary_action(self, pkg: str) -> None:
        g = library.game(pkg)
        opts = [o for o in self.play_options(g) + self.install_options(g) if o[2] and not o[3]]
        if opts:
            opts[0][2](None)

    def game_actions(self, pkg: str, quick: bool = True) -> list[tuple | None]:
        """A game's menu as [(label, icon, handler)], None = divider. quick=True is the library's right-click menu
        (open, install, test, uninstall first); False is the game page's "…" menu (those have buttons there)."""
        g = library.game(pkg)
        if not g:
            return []
        rift = g.get("kind") == "rift"
        job = self.jobs.busy_with(pkg)
        out: list[tuple | None] = []
        if quick:
            out.append(("Open", ft.Icons.OPEN_IN_NEW_ROUNDED, lambda e: self.open_game(pkg)))
            if job:
                out.append(("Show progress", ft.Icons.SYNC_ROUNDED, lambda e: self.show_activity(True)))
                out.append(("Cancel", ft.Icons.CLOSE_ROUNDED, lambda e: self.jobs.cancel(job)))
            else:
                out += [(label, icon, handler) for label, icon, handler, disabled, _ in
                        self.play_options(g) + self.install_options(g) if handler and not disabled]
                on_frame = self.frame_state == "connected" and \
                    C.install_state(g, self.frame_info) in ("installed", "outdated")
                on_pc = rift and pkg in self.pc_installs()
                if on_frame:
                    out.append(("Launch test on Frame", ft.Icons.SCIENCE_OUTLINED,
                                lambda e: self.test_game(pkg, "frame")))
                if on_pc:
                    out.append(("Launch test on this PC", ft.Icons.SCIENCE_OUTLINED,
                                lambda e: self.test_game(pkg, "pc")))
                if on_frame and not rift:
                    out.append(("Adapter settings…", ft.Icons.TUNE_ROUNDED, lambda e: self.settings_dialog(pkg)))
                    out.append(("Add videos & files…", ft.Icons.VIDEO_LIBRARY_OUTLINED,
                                lambda e: self.go("files", pkg)))
                if on_frame:
                    out.append(("Uninstall from Frame", ft.Icons.DELETE_OUTLINE_ROUNDED,
                                lambda e: self.uninstall(pkg, "frame")))
                if on_pc:
                    out.append(("Remove from this PC", ft.Icons.DELETE_OUTLINE_ROUNDED,
                                lambda e: self.uninstall(pkg, "pc")))
            out.append(None)
            if self.library_view is not None:
                lv = self.library_view
                out.append(("Select", ft.Icons.CHECKLIST_ROUNDED,
                            lambda e: (lv.selected.add(pkg), lv.set_select_mode(True))))
        if rift:
            out.append(("Change executable…", ft.Icons.TERMINAL_ROUNDED, lambda e: self.choose_exe(pkg)))
        out.append(("Find artwork…", ft.Icons.IMAGE_SEARCH_ROUNDED, lambda e: self.find_artwork(pkg)))
        if not job and self.frame_state == "connected" and \
                C.install_state(g, self.frame_info) in ("installed", "outdated"):
            out.append(("Update Steam art on Frame", ft.Icons.WALLPAPER_ROUNDED, lambda e: self.update_steam_art(pkg)))
        out.append(("Refresh store details", ft.Icons.SYNC_ROUNDED, lambda e: self.refresh_details(pkg)))
        if not job:
            out.append(("Rebuild only (no install)" if not rift else "Check game files", ft.Icons.BUILD_ROUNDED,
                        lambda e: self.build_game(pkg)))
        out += [("Reset to suggested recipe", ft.Icons.RESTART_ALT_ROUNDED,
                 lambda e: (pipeline.reset_recipe(pkg), self.toast("Recipe reset"), self.refresh_view())),
                ("Save as known-good recipe", ft.Icons.VERIFIED_ROUNDED, lambda e: self.save_known_good(pkg)),
                ("Share working config…", ft.Icons.SHARE_ROUNDED, lambda e: self.share_config_dialog(pkg)),
                ("Collect logs", ft.Icons.FOLDER_ZIP_OUTLINED, lambda e: self.collect_logs(pkg)),
                ("Report a problem…", ft.Icons.BUG_REPORT_OUTLINED, lambda e: self.report_problem_dialog(pkg)),
                None,
                ("Remove from library", ft.Icons.DELETE_OUTLINE_ROUNDED, lambda e: self.remove_from_library(pkg))]
        return out

    def install(self, pkg: str, to: str = "frame", confirmed: bool = False) -> Job | None:
        if confirmed:
            return self._submit_install(pkg, to)
        self.install_many([pkg], to)
        return None

    # ---------------------------------------------------------------- queueing several installs
    def install_many(self, pkgs: list[str], to: str = "frame", allow_blocked: bool = False) -> None:
        """Queue installs. Everything that needs a decision is asked first, one game at a time (which program
        starts a Rift game; games that check their Oculus license), then all of them run in the background.
        Games marked "Can't run" are skipped unless allow_blocked (the user chose "Install anyway")."""
        from .views.exe_dialog import show_exe_dialog

        games = [library.game(p) for p in pkgs]
        games = [g for g in games if g and not self.jobs.busy_with(g["package"])]
        skipped = [g for g in games if to == "pc" and g.get("kind") != "rift" or
                   not allow_blocked and g.get("kind") != "rift" and (g.get("recipe") or {}).get("status") == "unsupported"]
        games = [g for g in games if g not in skipped]
        if not games:
            self.toast("Nothing to install" + (f" ({len(skipped)} can't be installed there)" if skipped else ""))
            return
        need_exe = [g["package"] for g in games if g.get("kind") == "rift" and g.get("exe_confirmed") is False]

        def ask_exe(i=0):
            if i < len(need_exe):
                show_exe_dialog(self, need_exe[i], remaining=len(need_exe) - i - 1, on_done=lambda: ask_exe(i + 1))
            else:
                ask_frame_oculus()

        def ask_frame_oculus():
            # Installing an Oculus/LibOVR Rift game on the Frame: warn that it needs Revive (which can't run there)
            if to != "frame":
                return ask_license()
            oculus = [g for g in games if g.get("kind") == "rift"
                      and "pcvr.revive" in (g.get("recipe") or {}).get("patches", {})]
            if not oculus:
                return ask_license()
            boxes = {g["package"]: ft.Checkbox(label=self._title(g["package"]), value=False, active_color=T.ACCENT)
                     for g in oculus}

            def ok(e):
                nonlocal games
                self.page.pop_dialog()
                keep = {p for p, b in boxes.items() if b.value}
                games = [g for g in games if g not in oculus or g["package"] in keep]
                if not games:
                    self.toast("Nothing to install on the Frame — those Oculus games need PC mode (SteamVR + Revive).")
                    return
                ask_license()
            self.page.show_dialog(ft.AlertDialog(
                title=ft.Text("These games can't run on the Steam Frame", weight=ft.FontWeight.W_600),
                content=ft.Container(ft.Column([
                    C.body("They're Oculus games that need Revive to reach VR, and Revive can't run on the Frame. "
                           "Play them on this PC instead (Install on this PC — SteamVR + Revive). Tick any you still "
                           "want to put on the Frame to experiment (they'll likely run flat or crash)."),
                    *boxes.values()], spacing=T.S2, tight=True, scroll=ft.ScrollMode.AUTO), width=T.px(520)),
                bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
                actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()),
                         C.primary("Continue", on_click=ok)]))

        def ask_license():
            from ..core import winhost

            sdk = [g for g in games if g.get("kind") == "rift" and (g["analysis"].get("extra") or {}).get("platform_sdk")]
            if not sdk or to == "pc" and winhost.oculus_platform_dir():
                return go([g["package"] for g in games])  # the Meta Horizon app provides the Platform SDK here
            frame = to == "frame"
            boxes = {g["package"]: ft.Checkbox(label=self._title(g["package"]), value=not frame,
                                               active_color=T.ACCENT) for g in sdk}

            def ok(e):
                self.page.pop_dialog()
                keep = {p for p, b in boxes.items() if b.value}
                go([g["package"] for g in games if g not in sdk or g["package"] in keep])
            self.page.show_dialog(ft.AlertDialog(
                title=ft.Text("These games check their Oculus license", weight=ft.FontWeight.W_600),
                content=ft.Container(ft.Column([
                    C.body("They use the Oculus Platform SDK, which comes with the Meta Horizon (Oculus) app. It "
                           "doesn't exist on the Steam Frame, so there they crash right at startup (seen with Robo "
                           "Recall, Lies Beneath and Lone Echo) — play them on this PC. Tick any you still want to "
                           "try on the Frame." if frame else
                           "They use the Oculus Platform SDK, which comes with the Meta Horizon (Oculus) app — it "
                           "isn't installed on this PC, so they may quit right after starting. FramePort doesn't "
                           "change how a game checks its license. Untick the ones you'd rather skip."),
                    *boxes.values()], spacing=T.S2, tight=True, scroll=ft.ScrollMode.AUTO), width=T.px(520)),
                bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
                actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()),
                         C.primary("Install", on_click=ok)]))

        def go(final: list[str]):
            for p in final:
                self._submit_install(p, to)
            if len(final) > 1:
                self.toast(f"Queued {len(final)} installs — they run one after another in the background",
                           action="Activity", on_action=lambda e: self.show_activity(True))
            if self.library_view:
                self.library_view.set_select_mode(False)
        ask_exe()

    def show_failures(self, jobs: list[Job]) -> None:
        """One pop-up for everything that went wrong in a batch: what happened, and Resume / Uninstall / log."""
        installed = {d["package"] for d in (self.frame_info or {}).get("installed", [])}
        recs = self._records()
        rows = []
        for job in jobs:
            pkg, to = job.package, getattr(job, "to", "frame")
            s = job.summary or {}
            fatal = [f for f in s.get("findings", []) if f.get("severity") == "fatal"] or s.get("findings", [])
            if job.state == "failed" and pkg in recs:
                why = f"Didn't finish ({job.stage or 'install'}): {job.error}"
            elif job.state == "failed":
                why = f"Failed: {job.error}"
            else:
                why = "Installed, but it didn't start properly: " + (
                    fatal[0]["diagnosis"] if fatal else f"it stopped at '{s.get('milestone') or 'the start'}'")
            buttons = []
            if pkg in recs:
                buttons.append(C.primary("Resume", ft.Icons.PLAY_ARROW_ROUNDED,
                                         lambda e, p=pkg, t=to: (self.page.pop_dialog(), self._submit_install(p, t))))
            if to == "frame" and (pkg in installed or pkg in recs):
                buttons.append(C.secondary("Uninstall from Frame", ft.Icons.DELETE_OUTLINE_ROUNDED,
                                           lambda e, p=pkg: (self.page.pop_dialog(), self.uninstall(p, "frame"))))
            elif to == "pc" and pkg in self.pc_installs():
                buttons.append(C.secondary("Remove from this PC", ft.Icons.DELETE_OUTLINE_ROUNDED,
                                           lambda e, p=pkg: (self.page.pop_dialog(), self.uninstall(p, "pc"))))
            if job.log_path:
                buttons.append(C.ghost("Launch log", ft.Icons.DESCRIPTION_ROUNDED,
                                       lambda e, j=job: self.show_log_file(j.log_path, j.title)))
            buttons.append(C.ghost("Report problem", ft.Icons.BUG_REPORT_OUTLINED,
                                   lambda e, p=pkg: (self.page.pop_dialog(), self.report_problem_dialog(p))))
            rows.append(C.card(ft.Column([
                C.body(self._title(pkg) if pkg else job.title, T.TEXT, weight=ft.FontWeight.W_600),
                C.body(why, T.TEXT_2, selectable=True),
                ft.Row(buttons, spacing=T.S2, wrap=True),
            ], spacing=T.S2)))
        n = len(jobs)
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"{n} game{'s' if n != 1 else ''} didn't work out" if n > 1 else
                          f"{self._title(jobs[0].package) if jobs[0].package else jobs[0].title} didn't work out",
                          weight=ft.FontWeight.W_600),
            content=ft.Container(ft.Column(rows, spacing=T.S3, scroll=ft.ScrollMode.AUTO, tight=True), width=T.px(600),
                                 height=min(160 * n + 20, 520)),
            bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
            actions=[C.ghost("Details", on_click=lambda e: (self.page.pop_dialog(), self.show_activity(True))),
                     C.primary("Close", on_click=lambda e: self.page.pop_dialog())]))

    def show_log_file(self, path: str | None, title: str = "") -> None:
        """The full launch log in a viewer where it can be selected and copied as a whole."""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace") if path else ""
        except OSError as exc:
            text = f"Couldn't read {path}: {exc}"
        lines = text.splitlines()
        shown = "\n".join(lines[-4000:])
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Launch log · {title}", weight=ft.FontWeight.W_600),
            content=ft.Container(ft.Column([
                C.meta(f"{path}  ({len(lines)} lines" + (", last 4000 shown)" if len(lines) > 4000 else ")"),
                       selectable=True),
                ft.Container(ft.Column([ft.Text(shown, size=T.px(11), font_family="monospace", color=T.TEXT_2,
                                                selectable=True)], scroll=ft.ScrollMode.AUTO),
                             bgcolor=T.BG, border_radius=T.RADIUS_SM, padding=T.S3, expand=True),
            ], spacing=T.S2), width=T.px(980), height=T.px(620)),
            bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
            actions=[C.ghost("Copy all", ft.Icons.CONTENT_COPY_ROUNDED, lambda e: self.copy(text)),
                     C.primary("Close", on_click=lambda e: self.page.pop_dialog())]))

    def _records(self) -> dict:
        return dict(library.setting("ui.installs") or {})

    def _record(self, pkg: str, **fields) -> None:
        recs = self._records()
        if fields.get("remove"):
            recs.pop(pkg, None)
        else:
            recs[pkg] = {**recs.get(pkg, {}), **fields, "time": time.time()}
        library.set_setting("ui.installs", recs)

    def unfinished_installs(self) -> dict:
        """Installs that were queued/running when the app closed, or were cancelled or failed."""
        return {p: r for p, r in self._records().items() if not self.jobs.busy_with(p) and library.game(p)}

    def resume_installs(self) -> None:
        for pkg, r in self.unfinished_installs().items():
            self._submit_install(pkg, r.get("to", "frame"))
        self.toast("Resuming — files already copied are skipped", action="Activity",
                   on_action=lambda e: self.show_activity(True))

    def forget_installs(self) -> None:
        library.set_setting("ui.installs", {})
        self.refresh_view()

    def _submit_install(self, pkg: str, to: str = "frame") -> Job | None:
        if self.jobs.busy_with(pkg):
            return None
        g = library.game(pkg)
        rift = g.get("kind") == "rift"
        where = "your Frame" if to == "frame" else "this PC"
        self._record(pkg, to=to, state="queued")

        def run(job: Job):
            rep = job.reporter
            self._record(pkg, to=to, state="running")
            as_is = library.recipe_from_dict(library.game(pkg)["recipe"]).as_is
            rep.stage("Checking the game" if rift or as_is else "Patching the game")
            info = pipeline.build_game(pkg, rep)
            if not info["ok"]:
                raise RuntimeError("the game didn't pass its checks (see the list above)")
            rep.check_cancel()
            target = self._target_for(to)
            pipeline.install_game(pkg, target, rep, apk_only=False)
            self._record(pkg, remove=True)  # installed; the launch test below is a separate question
            if to == "pc":  # a PC launch test would start the game on the user's desktop — skip it
                rep.stage("Installed")
                return (f"{g.get('title')} is installed on this PC — launch it from your Steam library or the Play "
                        "button (SteamVR starts with it)")
            rep.check_cancel()
            if not library.setting("install.launch_test", True):  # Settings → Installing
                rep.stage("Installed")
                return (f"{g.get('title')} is installed on {where}: put the headset on and launch it from your Steam "
                        "library (automatic launch test is off in Settings)")
            summary = pipeline.test_game(pkg, target, rep)
            job.summary, job.to, job.log_path = summary, to, summary.get("log_path")
            rep.stage(f"Launch test: {'passed' if summary['verdict'] == 'pass' else summary['verdict']}")
            ok = summary["verdict"] == "pass"
            return (f"{g.get('title')} is ready on {where}: put the headset on and launch it from your Steam library"
                    if ok else f"{g.get('title')} is installed on {where}, but the launch test needs a look")
        job = self.submit(f"Install {self._title(pkg)} on {'Frame' if to == 'frame' else 'this PC'}", run, pkg,
                          "install")
        job.to = to
        return job

    def updatable(self) -> list[tuple[str, str]]:
        """[(package, "frame" | "pc")] installs with an update ready (a newer build or changed patch settings)."""
        out = []
        frame_ok = self.frame_state == "connected"
        pc = self.pc_installs() if any(g.get("kind") == "rift" for g in library.games()) else {}
        for g in library.games():
            pkg = g["package"]
            if self.jobs.busy_with(pkg):
                continue
            if frame_ok and C.install_state(g, self.frame_info) == "outdated":
                out.append((pkg, "frame"))
            if pkg in pc and C.pc_outdated(g, pc[pkg]):
                out.append((pkg, "pc"))
        return out

    def update_all(self) -> None:
        """Queue an update for every install marked "update ready" (one at a time, like any install)."""
        todo = self.updatable()
        if not todo:
            self.toast("Everything is up to date")
            return
        for pkg, to in todo:
            self.install(pkg, to)
        self.toast(f"Updating {len(todo)} game{'s' if len(todo) != 1 else ''}")
        self.show_activity(True)

    def install_blocked(self, pkg: str) -> None:
        """Install a game marked "Can't run" after saying why it's marked so."""
        g = library.game(pkg) or {}
        notes = (g.get("recipe") or {}).get("notes") or "It has a known blocker on the Steam Frame."

        def go(e):
            self.page.pop_dialog()
            self.install_many([pkg], "frame", allow_blocked=True)
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Install {self._title(pkg)} anyway?"), bgcolor=T.SURFACE_2,
            content=ft.Column([C.body("This game is marked \"Can't run\" on the Steam Frame:", T.TEXT_2), C.body(notes),
                               C.body("Install it anyway to try it, e.g. with different patches.", T.TEXT_2)],
                              tight=True, width=T.px(520)),
            actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()),
                     C.primary("Install anyway", ft.Icons.WARNING_AMBER_ROUNDED, go)]))

    def test_game(self, pkg: str, to: str = "frame") -> Job:
        title = self._title(pkg)

        def run(job: Job):
            target = self._target_for(to)
            if library.game(pkg):
                summary = pipeline.test_game(pkg, target, job.reporter)
            else:  # installed on the Frame but not in this library
                res, _ = target.launch_test(pkg, job.reporter)
                summary = {"verdict": res.verdict, "milestone": res.milestone, "suggestions": []}
            job.summary, job.to, job.log_path = summary, to, summary.get("log_path")
            return f"{title}: launch test {summary['verdict']} (furthest: {summary.get('milestone') or '—'})"
        return self.submit(f"Launch test: {title}", run, pkg, "test")

    def update_steam_art(self, pkg: str) -> Job:
        """Send the game's current artwork to its Steam entry on the Frame (Steam restarts once)."""
        def run(job: Job):
            self._target_for("frame").update_steam_art(pkg, job.reporter)
            return f"{self._title(pkg)}: Steam artwork updated on the Frame"
        return self.submit(f"Update Steam art: {self._title(pkg)}", run, pkg, "art")

    def build_game(self, pkg: str) -> Job:
        def run(job: Job):
            info = pipeline.build_game(pkg, job.reporter)
            return f"{self._title(pkg)}: {'ready' if info['ok'] else 'checks failed'}"
        return self.submit(f"Prepare {self._title(pkg)}", run, pkg, "build")

    def uninstall(self, pkg: str, to: str = "frame") -> None:
        title = self._title(pkg)
        text = (f"Removes {title}'s game files from the Frame. Saves are kept; the Steam entry disappears after the "
                "next Steam restart.") if to == "frame" else \
            f"Removes {title} from this PC's Steam library (Steam restarts once). The game folder isn't touched."

        def run(job: Job):
            self._target_for(to).uninstall(pkg, keep_data=True)
            self._pc_cache = None
            return f"Uninstalled {title}"
        C.confirm(self.page, f"Uninstall {title}?", text, "Uninstall",
                  lambda: self.submit(f"Uninstall {title}", run, pkg, "uninstall"), danger=True)

    def remove_from_library(self, pkg: str) -> None:
        title = self._title(pkg)
        C.confirm(self.page, f"Remove {title} from the library?",
                  "Only FramePort's entry is removed. Your game files and anything installed stay.", "Remove",
                  lambda: (library.remove_game(pkg), self.go("library"), self.toast(f"Removed {title}")), danger=True)

    def _target_for(self, to: str):
        if to == "pc":
            from ..targets.pc_revive import PcReviveTarget

            self._pc_cache = None
            return PcReviveTarget()
        if not self.target:
            raise RuntimeError("the Frame isn't connected")
        return self.target

    def save_known_good(self, package: str) -> None:
        catalog.save_user_entry(catalog.entry_from_library(library.game(package)))
        self.toast("Saved as a known-good recipe")

    # ================================================================== sharing / diagnostics
    def open_url(self, url: str) -> None:
        from ..core import winhost

        if winhost.is_wsl() and winhost.open_url(url):  # the desktop client would open a Linux browser in WSL
            return
        try:
            self.page.run_task(ft.UrlLauncher().launch_url, url)
        except Exception:  # noqa: BLE001
            winhost.open_url(url)

    def _diag_target(self, pkg: str | None):
        g = library.game(pkg) if pkg else None
        if g and g.get("kind") == "rift" and pkg in self.pc_installs() and \
                C.install_state(g, self.frame_info) not in ("installed", "outdated"):
            return self._target_for("pc")
        return self.target if self.frame_state == "connected" else None

    def collect_logs(self, pkg: str | None = None, report: str | None = None) -> None:
        """Diagnostics zip (redacted) → shown in the file manager; report (a description, may be "") also opens a
        prefilled GitHub problem report to attach it to."""
        from ..core import winhost

        target, info = self._diag_target(pkg), self.frame_info

        def run(job: Job):
            path = pipeline.collect_diagnostics([pkg] if pkg else None, target, job.reporter)
            winhost.open_folder(path, select=True)
            if report is not None:
                self.open_url(pipeline.problem_report(pkg, report, path, info if target is self.target else None))
                return f"Saved {path.name}: drag it into the GitHub issue that just opened"
            return f"Saved {path.name} (in {path.parent})"
        self.submit(f"Collect logs: {self._title(pkg)}" if pkg else "Collect app logs", run, pkg, "diag")

    def report_problem_dialog(self, pkg: str | None = None) -> None:
        text = ft.TextField(label="What happens? (optional: you can also write it on GitHub)", multiline=True,
                            min_lines=3, max_lines=8, width=T.px(560), border_color=T.BORDER)

        def go(e):
            self.page.pop_dialog()
            self.collect_logs(pkg, text.value or "")
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Report a problem · {self._title(pkg)}" if pkg else "Report a problem"),
            bgcolor=T.SURFACE_2,
            content=ft.Column([
                C.body("FramePort saves a diagnostics zip (logs, recipe, device info; no game files, personal data "
                       "removed) and opens a prefilled GitHub issue. Drag the zip into it, check the text, submit.",
                       T.TEXT_2),
                ft.Row([text, C.help_icon("diag_bundle")]),
            ], tight=True, spacing=T.S3, width=T.px(600)),
            actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()),
                     C.primary("Collect & open GitHub", ft.Icons.OPEN_IN_NEW_ROUNDED, on_click=go)]))

    def share_config_dialog(self, pkg: str) -> None:
        g = library.game(pkg) or {}
        last = g.get("last_test") or {}
        status = ft.RadioGroup(ft.Row([ft.Radio(value="works", label="Works"),
                                       ft.Radio(value="issues", label="Works with issues")]), value="works")
        notes = ft.TextField(label="Notes (what you checked, known issues)", multiline=True, min_lines=2,
                             max_lines=6, width=T.px(560), border_color=T.BORDER)
        played = ft.Checkbox(label="I played it in the headset with this recipe", value=False)
        send = C.primary("Open GitHub issue", ft.Icons.OPEN_IN_NEW_ROUNDED, on_click=None)

        def sync(e=None):
            send.disabled = not played.value
            C.update(send)
        played.on_change = sync
        sync()

        def go(e):
            self.page.pop_dialog()
            info = self.frame_info if self.frame_state == "connected" else None

            def work():
                url = pipeline.share_working_config(pkg, status.value or "works", notes.value or "", info)
                self.open_url(url)
                self.toast("Saved as known-good. Check the issue on GitHub and submit it")
            self.run_bg(work)
        send.on_click = go
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Share working config · {self._title(pkg)}"), bgcolor=T.SURFACE_2,
            content=ft.Column([
                C.body("Opens a prefilled GitHub issue with this game's recipe, so it can join the built-in catalog "
                       "(no account token needed; you review and submit it on GitHub). No game files or personal "
                       "data are sent.", T.TEXT_2),
                *([C.body(f"Last launch test: {last.get('verdict')} · furthest: {last.get('milestone') or '—'}",
                          T.TEXT_3)] if last else []),
                ft.Row([status, C.help_icon("share_config")]), notes, played,
                C.body("Launch tests run without the headset worn, so only you can confirm the picture and controls.",
                       T.TEXT_3),
            ], tight=True, spacing=T.S3, width=T.px(600)),
            actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()), send]))

    def settings_dialog(self, package: str) -> None:
        from ..patches.settings import SETTINGS

        fields = {key: ft.TextField(label=title, value="", hint_text=str(default), width=T.px(200), dense=True,
                                    border_color=T.BORDER) for key, kind, default, title, _ in SETTINGS}

        def save(e):
            vals = {k: f.value for k, f in fields.items() if f.value.strip()}
            self.run_bg(lambda: self.toast(f"Saved: {self.target.set_settings(package, vals)['settings']}"))
            self.page.pop_dialog()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Adapter settings · {self._title(package)}"), bgcolor=T.SURFACE_2,
            content=ft.Column([C.body("Only filled-in values change. Restart the game afterwards."),
                               ft.Row(list(fields.values()), wrap=True, width=T.px(640))], tight=True,
                              scroll=ft.ScrollMode.AUTO),
            actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()), C.primary("Save", on_click=save)]))

    # ================================================================== library
    async def pick_folder(self, e=None):
        path = await ft.FilePicker().get_directory_path(dialog_title="Folder with Quest or Oculus Rift games")
        if path:
            self.scan(path)

    async def pick_game_folder(self, e=None):
        path = await ft.FilePicker().get_directory_path(dialog_title="One Oculus Rift game folder")
        if path:
            self.scan(path, single=True)

    async def pick_apk(self, e=None):
        files = await ft.FilePicker().pick_files(allow_multiple=True, allowed_extensions=["apk"])
        for f in files or []:
            if f.path:
                self.scan(f.path)

    def scan_roots(self) -> list[str]:
        """Folders to rescan: the ones scanned before (remembered from now on), else the folders the library's games
        came from (one level up from each game folder)."""
        roots = [r for r in library.setting("scan.roots", []) if Path(r).exists()]
        if not roots:
            found = set()
            for g in library.load().get("games", {}).values():
                origin = g.get("origin") or (str(Path(g["apk"]).parent) if g.get("apk") else None)
                if origin and Path(origin).parent.exists():
                    found.add(str(Path(origin).parent))
            roots = sorted(found)
        # a folder inside another one is scanned with it (scanning it again would only repeat work)
        return [r for r in roots if not any(o != r and Path(r).is_relative_to(o) for o in roots)]

    def rescan(self, e=None) -> None:
        """Scan the library's folders again for games added since (unchanged games aren't analyzed again)."""
        roots = self.scan_roots()
        if not roots:
            self.toast("No folders to rescan yet: add games with Scan a folder")
            return
        for r in roots:
            self.scan(r, only_new=True)
        self.toast(f"Rescanning {len(roots)} folder{'s' if len(roots) != 1 else ''} for new games")

    def scan(self, path: str, single: bool = False, only_new: bool = False) -> Job:
        last = {"t": 0.0}
        if not single:  # remembered for "Rescan folders"
            roots = [r for r in library.setting("scan.roots", []) if r != str(path)]
            library.set_setting("scan.roots", roots + [str(path)])

        def added_one(entry: dict):
            from ..artwork import thumbs

            try:
                thumbs.prewarm(entry["package"])
            except Exception:  # noqa: BLE001
                pass
            if entry.get("kind") == "rift" and entry.get("exe_confirmed") is False:
                self.exe_queue.append(entry["package"])
            if self.route[0] == "library" and time.time() - last["t"] > 1.0:  # stream new cards in
                last["t"] = time.time()
                self.refresh_view()

        def run(job: Job):
            rep = job.reporter
            rep.stage("Looking for games")
            added = pipeline.add_path(Path(path), rep, on_added=added_one, force_rift=single, art=True,
                                      only_new=only_new)
            if self.route[0] == "welcome" and added:
                library.set_setting("ui.welcome_done", True)
                self.route = ("library",)
            n = len(added)
            if only_new:
                return f"{n} new game{'s' if n != 1 else ''} in {Path(path).name}" if added else \
                    f"No new games in {Path(path).name}"
            return f"Added {n} game{'s' if n != 1 else ''}" if added else "No games found in that folder"
        return self.submit(f"Scan {Path(path).name}", run, None, "scan")

    # ------------------------------------------------------------------ executable choice / artwork
    def next_exe_choice(self) -> None:
        from .views.exe_dialog import show_exe_dialog

        while self.exe_queue:
            pkg = self.exe_queue.pop(0)
            g = library.game(pkg)
            if g and g.get("exe_confirmed") is False:
                show_exe_dialog(self, pkg, remaining=len(self.exe_queue), on_done=self.next_exe_choice)
                return

    def choose_exe(self, pkg: str) -> None:
        from .views.exe_dialog import show_exe_dialog

        show_exe_dialog(self, pkg)

    def refresh_details(self, pkg: str) -> Job:
        def run(job: Job):
            d = pipeline.fetch_details(pkg, job.reporter)
            n = len(d.get("screenshots") or [])
            return f"{self._title(pkg)}: details from {', '.join(d.get('sources') or []) or 'nowhere'}" + \
                (f", {n} screenshots" if n else "")
        return self.submit(f"Store details: {self._title(pkg)}", run, pkg, "art")

    def find_artwork(self, pkg: str) -> None:
        from .views.art_dialog import show_art_dialog

        show_art_dialog(self, pkg)

    # ================================================================== Frame
    def _startup(self):
        from ..frame.connection import parse_target, saved_targets

        self._art_backfill()
        saved = saved_targets()
        if saved:
            self.connect(saved[0], quiet=True)
            return
        from ..frame.discovery import browse

        for f in browse(4, scan=False):
            self.connect(parse_target(f"{f.user}@{f.host}"), quiet=True)
            break

    def _art_backfill(self) -> None:
        """Rift games added before automatic artwork (or while offline): fetch it once in the background."""
        from ..artwork import sources

        games = library.games()
        art = [g["package"] for g in games
               if g.get("kind") == "rift" and not g.get("art_source") and not sources.has_art(g["package"])]
        info = [g["package"] for g in games if "details" not in g]
        if not art and not info:
            return

        def run(job: Job):
            n = len(art) + len(info)
            for i, pkg in enumerate(art):
                job.reporter.check_cancel()
                job.reporter.progress(i / n, self._title(pkg))
                pipeline.fetch_art(pkg, job.reporter)
            for i, pkg in enumerate(info, len(art)):
                job.reporter.check_cancel()
                job.reporter.progress(i / n, self._title(pkg))
                pipeline.fetch_details(pkg, job.reporter)
            return f"Store details for {n} game{'s' if n != 1 else ''}"
        self.submit("Find artwork and store details", run, kind="art")

    def _poll(self):
        """Keep the connection card honest: refresh when connected, retry quietly when offline."""
        from ..frame.connection import saved_targets

        while True:
            time.sleep(POLL_SECONDS)
            if self.jobs.current():
                continue  # the job is using the connection
            try:
                if self.frame_state == "connected":
                    self.refresh_frame(quiet=True, background=False)
                elif self.frame_state == "offline" and saved_targets():
                    self.connect(saved_targets()[0], quiet=True)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def connect(self, target, password=None, quiet=False):
        from ..frame.connection import save_target
        from ..targets.frame_lepton import FrameLeptonTarget

        self.frame_state = "connecting"
        self._refresh_sidebar()

        def work():
            try:
                t = FrameLeptonTarget(target, password).connect()
                if password:
                    t.frame.install_key()
                info = t.describe()
                target.name = info.get("hostname") or target.name
                t.label = target.label
                save_target(target)
                self.target, self.frame_info, self.frame_state = t, info, "connected"
                if not quiet:
                    self.toast(f"Connected to {target.label}")
            except Exception as exc:  # noqa: BLE001
                self.frame_state = "offline"
                self.frame_info = None
                if not quiet:
                    self.toast(f"Couldn't connect: {exc}", error=True)
            if self.route[0] in ("frame", "library", "game", "welcome"):
                self.refresh_view()
            else:
                self._refresh_sidebar()
        self.page.run_thread(work)

    def connect_manual(self, address: str, password: str | None):
        from ..frame.connection import parse_target

        if not (address or "").strip():
            self.toast("Enter the Frame's address (e.g. steamos@frame.local or its IP)", error=True)
            return
        try:
            target = parse_target(address)
        except ValueError as exc:
            self.toast(str(exc), error=True)
            return
        self.connect(target, password or None)

    def refresh_frame(self, quiet: bool = False, rerender: bool = True, background: bool = True):
        def work():
            if not self.target:
                return
            try:
                info = self.target.describe()
                changed = info.get("installed") != (self.frame_info or {}).get("installed") or \
                    self.frame_state != "connected"
                self.frame_info, self.frame_state = info, "connected"
            except Exception as exc:  # noqa: BLE001
                changed = self.frame_state == "connected"
                self.frame_state, self.frame_info = "offline", None
                try:
                    self.target.close()
                except Exception:  # noqa: BLE001
                    pass
                if not quiet:
                    self.toast(f"The Frame went offline: {exc}", error=True)
            if changed and rerender and self.route[0] in ("frame", "library", "game"):
                self.refresh_view()
            else:
                self._refresh_sidebar()
        if background:
            self.page.run_thread(work)
        else:
            work()

    def disconnect(self):
        if self.target:
            try:
                self.target.close()
            except Exception:  # noqa: BLE001
                pass
        self.target, self.frame_info, self.frame_state = None, None, "none"
        self.go("frame")

    def install_lepton(self):
        def run(job: Job):
            r = self.target.install_lepton()
            return "Lepton is installed" if r.get("installed") else r.get("hint") or "Asked Steam to install Lepton"
        self.submit("Install Lepton on the Frame", run, kind="tool-frame")

    def install_proton(self):
        def go():
            from ..install.installer import ensure_proton

            def run(job: Job):
                tool = ensure_proton(self.target.frame, job.reporter)
                return f"{tool['display_name']} is installed on your Frame"
            self.submit("Install Proton on the Frame", run, kind="tool-frame", open_panel=True)
        C.confirm(self.page, "Install Proton on the Frame?",
                  "FramePort has Steam on the Frame download Proton and the runtime it needs (about 1 GB). Steam "
                  "restarts once, which closes a running game.", "Install", go)

    def test_proton(self):
        def run(job: Job):
            job.reporter.stage("Running a Windows program under Proton")
            r = self.target.proton_selftest()
            job.reporter.check("Windows program ran", bool(r.get("ran")), f"{r.get('seconds')} s")
            job.reporter.check("OpenXR bridge registered", bool(r.get("openxr_runtime")), r.get("openxr_runtime") or "")
            if not r.get("ran"):
                raise RuntimeError("Proton couldn't run a test program (see the log)")
            return f"Proton works on your Frame ({r['tool']})"
        self.submit("Test Proton on the Frame", run, kind="tool-frame")

    def cleanup_frame(self):
        def go():
            def run(job: Job):
                r = self.target.frame.agent("cleanup", rollback=True, paths=[])
                return f"Freed {r['freed_bytes'] / 2**30:.1f} GiB on the Frame"
            self.submit("Free up space on the Frame", run, kind="tool-frame")
        C.confirm(self.page, "Free up space?", "Removes the previous version kept after each reinstall and any "
                  "leftover uploads. Games and saves aren't touched.", "Free up space", go)

    # ================================================================== tools / welcome
    def update_tools(self, update: bool = False, quiet: bool = False) -> Job:
        from ..tools import toolchain

        def run(job: Job):
            rep = job.reporter
            rep.stage("Checking for updates" if update else "Downloading tools")
            statuses = toolchain.status(check_latest=update)
            todo = [s for s in statuses if not s.optional and (not s.installed or (update and s.latest and s.version
                                                                                      and s.latest != s.version))]
            for i, s in enumerate(todo):
                rep.stage(f"Installing {s.name}")
                installer = {"java": toolchain.install_java, "overport": toolchain.install_overport,
                             "apksigner": toolchain.install_apksigner}[s.name]
                installer(lambda f, i=i: rep.progress((i + f) / len(todo), s.name))
                rep.check(s.name, True, "ready")
            from ..patches import overport as op
            from ..tools import overport as ov

            try:
                op.refresh(ov.list_patches)
            except Exception:  # noqa: BLE001
                pass
            return "Tools are up to date" if not todo else f"Installed {', '.join(s.name for s in todo)}"
        return self.submit("Update tools" if update else "Get FramePort ready", run, kind="tools",
                           open_panel=not quiet)

    def uninstall_app(self) -> None:
        from .. import uninstall as un

        connected = self.frame_state == "connected" and self.target is not None
        pl = un.plan(self.frame_info if connected else None)
        from ..core import winhost

        backup_dir = un.default_backup_dir()
        shown_dir = winhost.to_windows(backup_dir) if winhost.is_wsl() else str(backup_dir)
        cb_keys = ft.Checkbox(label=f"Back up the signing keys ({len(pl.keys)}) to {shown_dir} first",
                              value=bool(pl.keys), active_color=T.ACCENT)
        cb_frame = ft.Checkbox(label="Also remove FramePort's games and files from the Frame" +
                               ("" if connected else " (connect the Frame first)"), value=connected,
                               disabled=not connected, active_color=T.ACCENT)
        cb_saves = ft.Checkbox(label="Keep game saves on the Frame", value=True, active_color=T.ACCENT)
        frame_items = [i for i in pl.items if i.kind == "frame"]
        steam_items = [i for i in pl.items if i.kind == "steam"]
        other = [i for i in pl.items if i.kind == "pc"]
        lines = [C.body(f"• {i.what}" + (f" — {i.size / 2**30:.1f} GiB" if i.size > 2**28 else ""), T.TEXT_2)
                 for i in other]
        if steam_items:
            lines.append(C.body(f"• {len(steam_items)} Steam shortcut{'s' if len(steam_items) != 1 else ''} on this PC "
                                "for PC VR games", T.TEXT_2))
        if frame_items:
            size = sum(i.size for i in frame_items) / 2**30
            lines.append(C.body(f"• On the Frame (if selected below): {len(frame_items)} game"
                                f"{'s' if len(frame_items) != 1 else ''} ({size:.0f} GiB), their Steam entries and "
                                "FramePort's files", T.TEXT_2))

        def go(e):
            self.page.pop_dialog()
            keys = backup_dir if cb_keys.value else None
            frame = self.target.frame if (cb_frame.value and connected) else None
            keep = cb_saves.value

            def run(job: Job):
                out = un.run(job.reporter, frame, keep, keys, remove_frame=frame is not None)
                self.target, self.frame_info, self.frame_state = None, None, "none"
                self.page.run_thread(lambda: self._uninstalled(out))
                return "FramePort was removed"
            self.submit("Uninstall FramePort", run, kind="uninstall-app", open_panel=True)
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Uninstall FramePort?", weight=ft.FontWeight.W_600),
            content=ft.Container(ft.Column([C.body("This removes:", T.TEXT), *lines, ft.Container(height=T.S2),
                                            cb_keys, cb_frame, cb_saves,
                                            C.meta("Signing keys matter: game updates must be signed with the same key "
                                                   "or their saves are lost on reinstall.")],
                                           spacing=T.S2, tight=True, scroll=ft.ScrollMode.AUTO), width=T.px(560)),
            bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
            actions=[C.ghost("Cancel", on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton("Uninstall", icon=ft.Icons.DELETE_FOREVER_ROUNDED, on_click=go,
                                     style=ft.ButtonStyle(bgcolor=T.ERROR, color=T.ON_ACCENT,
                                                          shape=ft.RoundedRectangleBorder(radius=T.RADIUS_SM)))]))

    def _uninstalled(self, out: dict) -> None:
        async def close(e=None):
            try:
                await self.page.window.close()
            except Exception:  # noqa: BLE001
                pass
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("FramePort was removed", weight=ft.FontWeight.W_600),
            content=ft.Container(ft.Column([
                C.body("All of FramePort's data on this PC is gone" +
                       (f", and your signing keys were saved to {out['backup']}." if out.get("backup") else ".")),
                C.body("To finish, close FramePort and delete its program folder."),
            ], spacing=T.S2, tight=True), width=T.px(520)),
            bgcolor=T.SURFACE_2, actions=[C.primary("Close FramePort", on_click=close)]))

    def finish_welcome(self):
        library.set_setting("ui.welcome_done", True)
        self.go("library")

    # ================================================================== compatibility (scripts/ui_smoke.py)
    @property
    def job_running(self) -> bool:
        return self.jobs.current() is not None or bool(self.jobs.pending())

    def start_job(self, package: str, build: bool = True, install: bool = False, test: bool | None = None,
                  to: str = "frame"):
        if install:
            return self.install(package, to)
        if test or test is None and not build:
            return self.test_game(package, to)
        return self.build_game(package)


def assets_dir() -> str:
    """The GUI's assets folder is the user data dir, so artwork thumbnails load by URL (/artwork/<pkg>/…)."""
    from ..core.paths import user_data_dir

    return str(user_data_dir())


def main(argv=None):
    applog.setup("gui")
    from ..core import library

    from .updater import apply_pending_at_start

    if apply_pending_at_start():  # "Install updates automatically": the new version starts instead of this one
        return
    T.set_scale(T.scale_from_setting(library.setting("ui.scale", "auto")))  # before any view is built
    applog.log.info("ui scale %.2f", T.SCALE)
    ft.run(lambda page: FramePortApp(page), assets_dir=assets_dir())


if __name__ == "__main__":
    main()
