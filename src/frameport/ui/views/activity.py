"""Activity panel (right side): running, queued and finished background jobs with steps, checks and the log."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import flet as ft

from .. import components as C
from .. import theme as T
from ..jobs import Job

if TYPE_CHECKING:
    from ..app import FramePortApp

CHECK_ICON = {True: (ft.Icons.CHECK_CIRCLE_ROUNDED, T.OK), False: (ft.Icons.CANCEL_ROUNDED, T.ERROR),
              None: (ft.Icons.WARNING_AMBER_ROUNDED, T.WARN)}
STATE_STYLE = {"queued": (ft.Icons.SCHEDULE_ROUNDED, T.TEXT_3, "Waiting"),
               "running": (ft.Icons.SYNC_ROUNDED, T.ACCENT, "Working"),
               "done": (ft.Icons.CHECK_CIRCLE_ROUNDED, T.OK, "Done"),
               "failed": (ft.Icons.ERROR_ROUNDED, T.ERROR, "Failed"),
               "cancelled": (ft.Icons.DO_NOT_DISTURB_ON_OUTLINED, T.TEXT_3, "Cancelled")}


def _dur(job: Job) -> str:
    if not job.started:
        return ""
    s = int((job.finished or time.time()) - job.started)
    return f"{s // 60}:{s % 60:02d}"


class ActivityPanel:
    def __init__(self, app: "FramePortApp"):
        self.app = app
        self.expanded: set[int] = set()
        self.logs: set[int] = set()
        self._tiles: dict[int, tuple[tuple, ft.Control]] = {}  # job id -> (state key, tile)
        self._live: dict[int, tuple] = {}  # running job id -> (progress bar, message, stage text)
        self.list = ft.Column(spacing=T.S3, scroll=ft.ScrollMode.AUTO, expand=True)
        self.root = ft.Container(
            ft.Column([
                ft.Row([C.h2("Activity"), ft.Container(expand=True),
                        C.ghost("Clear finished", on_click=lambda e: (app.jobs.clear_finished(), self.refresh())),
                        C.icon_btn(ft.Icons.CLOSE_ROUNDED, "Close", lambda e: app.show_activity(False))]),
                self.list,
            ], spacing=T.S3, expand=True),
            width=0, bgcolor=T.SIDEBAR, padding=ft.Padding(T.S4, T.S4, T.S4, T.S4),
            border=ft.Border(left=ft.BorderSide(1, T.BORDER)), animate_size=ft.Animation(180, ft.AnimationCurve.EASE_OUT),
            clip_behavior=ft.ClipBehavior.HARD_EDGE)

    @property
    def open(self) -> bool:
        return bool(self.root.width)

    def set_open(self, on: bool):
        self.root.width = 400 if on else 0
        if on:
            self.refresh(update=False)

    def refresh(self, update: bool = True):
        if not self.open:
            return
        jobs = self.app.jobs.recent(30)
        tiles = []
        for j in jobs:
            running = j.state == "running"
            # a running job's tile is rebuilt only when its structure changes (new stage/check, log shown); progress
            # updates go to its live controls — rebuilding on every tick swallowed clicks on Cancel
            key = (j.state, len(j.stages), len(j.checks), j.id in self.expanded, j.id in self.logs,
                   len(j.log) if j.id in self.logs else 0) if running else \
                (j.version, j.id in self.expanded, j.id in self.logs)
            cached = self._tiles.get(j.id)
            if not cached or cached[0] != key:  # unchanged tiles are reused, so text selections survive refreshes
                cached = (key, self.tile(j))
                self._tiles[j.id] = cached
            elif running and j.id in self._live:
                bar, msg, meta = self._live[j.id]
                bar.value = j.fraction
                msg.value = j.message or ""
                meta.value = j.stage or ""
            tiles.append(cached[1])
        self._tiles = {j.id: self._tiles[j.id] for j in jobs}
        self.list.controls = tiles or [
            ft.Container(C.body("Nothing running. Installs, launch tests and downloads show up here.",
                                text_align=ft.TextAlign.CENTER), padding=T.S6, alignment=ft.Alignment.CENTER)]
        if update:
            C.update(self.root)

    def tile(self, job: Job) -> ft.Control:
        icon, color, word = STATE_STYLE[job.state]
        if job.state == "done" and (job.summary or {}).get("verdict") == "fail":
            icon, color, word = ft.Icons.WARNING_AMBER_ROUNDED, T.WARN, "Installed · launch test failed"
        running = job.state == "running"
        expanded = running or job.id in self.expanded
        meta = C.meta(job.stage if running and job.stage else
                      (job.error or word) + (f" · {_dur(job)}" if job.finished else ""),
                      T.ERROR if job.state == "failed" else T.TEXT_2, max_lines=2)
        head = ft.Row([
            ft.ProgressRing(width=18, height=18, stroke_width=2, color=T.ACCENT) if running
            else ft.Icon(icon, color=color, size=20),
            ft.Column([C.body(job.title, T.TEXT, weight=ft.FontWeight.W_600, max_lines=2,
                              overflow=ft.TextOverflow.ELLIPSIS), meta],
                      spacing=2, expand=True),
            *([C.icon_btn(ft.Icons.CLOSE_ROUNDED, "Cancel", lambda e: self.app.jobs.cancel(job))] if job.active else []),
        ], vertical_alignment=ft.CrossAxisAlignment.START, spacing=T.S3)
        parts: list[ft.Control] = [head]
        if running:
            bar = C.progress_bar(job.fraction)
            msg = C.meta(job.message or "", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
            parts += [bar, msg]
            self._live[job.id] = (bar, msg, meta)
        if expanded:
            if job.stages:
                parts.append(ft.Column([
                    ft.Row([ft.Icon(ft.Icons.CHECK_ROUNDED if (i < len(job.stages) - 1 or not running)
                                    else ft.Icons.ARROW_RIGHT_ROUNDED, size=14,
                                    color=T.OK if (i < len(job.stages) - 1 or job.state == "done") else T.ACCENT),
                            C.meta(s, T.TEXT_2 if i < len(job.stages) - 1 else T.TEXT)], spacing=6)
                    for i, s in enumerate(job.stages[-8:])], spacing=2))
            if job.checks:
                parts.append(ft.Column([
                    ft.Row([ft.Icon(CHECK_ICON[c["ok"]][0], color=CHECK_ICON[c["ok"]][1], size=14),
                            ft.Text(f"{c['name']}" + (f" — {c['detail']}" if c.get("detail") else ""), size=T.T_META,
                                    color=T.TEXT_2, expand=True, selectable=True)],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)
                    for c in job.checks[-40:]], spacing=3))
            extra = self.app.job_followups(job)
            if extra:
                parts.append(ft.Row(extra, spacing=T.S2, wrap=True))
            show_log = job.id in self.logs
            parts.append(ft.Row([
                C.ghost("Hide log" if show_log else "Show log", ft.Icons.TERMINAL_ROUNDED,
                        lambda e: self._toggle(self.logs, job.id)),
                C.ghost("Copy log", ft.Icons.CONTENT_COPY_ROUNDED, lambda e: self.app.copy(self.job_text(job))),
                *([C.ghost("Full launch log", ft.Icons.DESCRIPTION_ROUNDED,
                           lambda e: self.app.show_log_file(job.log_path, job.title))] if job.log_path else []),
            ], spacing=0, wrap=True))
            if show_log:
                parts.append(ft.Container(ft.Column([ft.Text(
                    "\n".join(job.log[-400:]), size=11, font_family="monospace", color=T.TEXT_2, selectable=True)],
                    scroll=ft.ScrollMode.AUTO, auto_scroll=job.state == "running"),
                    bgcolor=T.BG, border_radius=T.RADIUS_SM, padding=T.S2, height=240))
        return ft.Container(ft.Column(parts, spacing=T.S2), bgcolor=T.SURFACE, border_radius=T.RADIUS,
                            border=ft.Border.all(1, T.ACCENT if running else T.BORDER), padding=T.S3,
                            on_click=None if running else lambda e: self._toggle(self.expanded, job.id))

    @staticmethod
    def job_text(job: Job) -> str:
        checks = [("PASS" if c["ok"] else "FAIL" if c["ok"] is False else "WARN") + f"  {c['name']}"
                  + (f": {c['detail']}" if c.get("detail") else "") for c in job.checks]
        return "\n".join([f"{job.title} — {job.state}" + (f": {job.error}" if job.error else ""), "", "Checks:", *checks,
                          "", "Log:", *job.log] + ([f"", f"Full launch log: {job.log_path}"] if job.log_path else []))

    def _toggle(self, s: set[int], jid: int):
        s.symmetric_difference_update({jid})
        self.refresh()
