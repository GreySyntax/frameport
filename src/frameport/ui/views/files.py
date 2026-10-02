"""Files: browse and manage files on the Steam Frame (instead of a separate file-transfer app).

Locations are the folders Quest games see (Videos / Downloads / Documents, shared by every game as /sdcard/Movies,
/sdcard/Download, /sdcard/Documents), each installed game's own storage, and the Frame's home folder. Everything runs
over the app's SSH connection (SFTP); uploads and downloads are jobs in the Activity panel. Persistent like the
Library: the chosen location and folder survive switching tabs.
"""
from __future__ import annotations

import posixpath
import time
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft

from .. import components as C
from .. import theme as T
from .files_dialog import human

if TYPE_CHECKING:
    from ..app import FramePortApp

SHARED_ICONS = {"videos": ft.Icons.MOVIE_OUTLINED, "downloads": ft.Icons.DOWNLOAD_OUTLINED,
                "documents": ft.Icons.DESCRIPTION_OUTLINED}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def dropzone_available() -> bool:
    """Drag-and-drop from the file manager needs flet-dropzone's Flutter code, which only a `flet build` app has (not
    `flet run`/the desktop client used from source, nor the PyInstaller fallback)."""
    import sys

    from ... import updates

    if getattr(sys, "frozen", False) or updates.install_kind() != "bundle":
        return False
    try:
        import flet_dropzone  # noqa: F401
    except ImportError:
        return False
    return True


def file_icon(name: str, is_dir: bool) -> str:
    if is_dir:
        return ft.Icons.FOLDER_ROUNDED
    ext = Path(name).suffix.lower()
    if ext in VIDEO_EXT:
        return ft.Icons.MOVIE_OUTLINED
    if ext in IMAGE_EXT:
        return ft.Icons.IMAGE_OUTLINED
    if ext in (".zip", ".7z", ".rar", ".tar", ".gz"):
        return ft.Icons.FOLDER_ZIP_OUTLINED
    return ft.Icons.INSERT_DRIVE_FILE_OUTLINED


def crumbs(root: str, path: str, root_label: str) -> list[tuple[str, str]]:
    """(label, path) for each level from the location down to `path`."""
    out = [(root_label, root)]
    rel = posixpath.relpath(path, root) if path != root else ""
    cur = root
    for part in [p for p in rel.split("/") if p and p != "."]:
        cur = posixpath.join(cur, part)
        out.append((part, cur))
    return out


class FilesView:
    def __init__(self, app: FramePortApp):
        self.app = app
        self.loc: dict | None = None      # {"id", "label", "path", "android", "shared", "package"}
        self.path = ""
        self.entries = []
        self.hidden = False
        self.game_locs: dict[str, dict] = {}  # package -> resolved location (agent storage_targets, cached)
        self.shared: list[dict] = []
        self.locations = ft.Column(spacing=T.px(2), scroll=ft.ScrollMode.AUTO, expand=True)
        self.crumb_row = ft.Row(spacing=T.px(2), wrap=True, expand=True)
        self.where = C.meta("")
        self.listing = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self.status = C.meta("")
        self.toolbar = ft.Row([
            C.primary("Upload files", ft.Icons.UPLOAD_FILE_ROUNDED, self.upload_files),
            C.secondary("Upload folder", ft.Icons.DRIVE_FOLDER_UPLOAD_ROUNDED, self.upload_folder),
            C.ghost("New folder", ft.Icons.CREATE_NEW_FOLDER_OUTLINED, lambda e: self.new_folder()),
            C.icon_btn(ft.Icons.REFRESH_ROUNDED, "Refresh", lambda e: self.load()),
        ], spacing=T.S2)
        self.hidden_switch = C.switch("Show hidden files", value=False, on_change=self._toggle_hidden)
        self.selected: set[str] = set()  # paths of checked entries in the current folder
        self.checks: dict[str, ft.Checkbox] = {}
        self.select_all = ft.Checkbox(value=False, active_color=T.ACCENT, check_color=T.ON_ACCENT,
                                      tooltip="Select all", on_change=self._toggle_all)
        self.sel_label = C.body("", T.TEXT, weight=ft.FontWeight.W_500)
        self.sel_bar = ft.Container(ft.Row([
            self.sel_label, ft.Container(expand=True),
            C.secondary("Download", ft.Icons.DOWNLOAD_ROUNDED, self._download_selected),
            C.ghost("Delete", ft.Icons.DELETE_OUTLINE_ROUNDED, lambda e: self._delete_selected()),
            C.ghost("Clear", ft.Icons.CLOSE_ROUNDED, lambda e: self._clear_selection()),
        ], spacing=T.S2), padding=ft.Padding(T.S3, T.px(6), T.S2, T.px(6)), border_radius=T.RADIUS_SM,
            bgcolor=T.ACCENT_SOFT, visible=False)
        self.drop_hint = ft.Container(
            ft.Column([ft.Icon(ft.Icons.UPLOAD_ROUNDED, size=T.px(48), color=T.ACCENT), C.h2("Drop to upload"),
                       C.meta("")], horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
            left=0, right=0, top=0, bottom=0, alignment=ft.Alignment.CENTER, bgcolor=T.soft("#000000", 0.7),
            border_radius=T.RADIUS, border=ft.Border.all(2, T.ACCENT), visible=False)
        self.root = None

    # ---------------------------------------------------------------- building
    def mount(self, package: str | None = None) -> ft.Control:
        app = self.app
        if not (app.target and app.frame_state == "connected"):
            return ft.Column([
                app.top_bar("Files", "Videos, documents, mods and saves on your Steam Frame"),
                C.empty_state(ft.Icons.FOLDER_OFF_OUTLINED, "Connect your Frame first",
                              "Files on the Frame can be browsed once FramePort is connected to it.",
                              C.primary("Connect", ft.Icons.LINK_ROUNDED, lambda e: app.go("frame")))], expand=True)
        if self.root is None:
            self.root = ft.Column([
                app.top_bar("Files", "Videos, documents, mods and saves on your Steam Frame"),
                ft.Row([
                    C.card(ft.Column([C.meta("LOCATIONS"), self.locations], spacing=T.S2, expand=True),
                           padding=T.S3, width=T.px(260), expand=False),
                    ft.Column([
                        ft.Row([self.crumb_row, self.toolbar], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Row([self.select_all, self.where, ft.Container(expand=True), self.hidden_switch],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        self.sel_bar,
                        self._drop_area(ft.Stack([C.card(self.listing, padding=T.px(4), expand=True),
                                                  self.drop_hint], expand=True)),
                        self.status,
                    ], spacing=T.S2, expand=True),
                ], spacing=T.S4, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH),
            ], spacing=T.S3, expand=True)
        self.app.run_bg(self._load_locations, package)
        return self.root

    def _drop_area(self, content: ft.Control) -> ft.Control:
        """Files and folders dragged in from the computer's file manager are uploaded to the open folder. Needs the
        flet-dropzone extension, which only the packaged app contains (flet build); elsewhere: no drop area."""
        if not dropzone_available():
            return content
        import flet_dropzone as ftd

        def entered(e):
            self.drop_hint.content.controls[2].value = f"into {self.where.value or self.path}"
            self.drop_hint.visible = True
            C.update(self.drop_hint)

        def exited(e):
            self.drop_hint.visible = False
            C.update(self.drop_hint)

        def dropped(e):
            exited(e)
            paths = [Path(f.path) for f in e.files if f.path and not f.path.startswith("blob:")]
            if paths:
                self.upload(paths)
        return ftd.Dropzone(content=content, expand=True, on_entered=entered, on_exited=exited, on_dropped=dropped)

    def _location_row(self, loc: dict, icon: str, sub: str = "") -> ft.Control:
        selected = self.loc is not None and self.loc["id"] == loc["id"]
        return ft.Container(
            ft.Row([ft.Icon(icon, size=T.px(18), color=T.ACCENT if selected else T.TEXT_2),
                    ft.Column([C.body(loc["label"], T.TEXT if selected else T.TEXT_2, weight=ft.FontWeight.W_500),
                               *([C.meta(sub)] if sub else [])], spacing=0, expand=True)], spacing=T.S2),
            padding=ft.Padding(T.S2, T.px(6), T.S2, T.px(6)), border_radius=T.RADIUS_SM, ink=True,
            bgcolor=T.ACCENT_SOFT if selected else None, on_click=lambda e, loc_=loc: self.open_location(loc_))

    def _render_locations(self) -> None:
        rows = [self._location_row(loc_, SHARED_ICONS.get(loc_["id"], ft.Icons.FOLDER_OUTLINED), loc_["android"])
                for loc_ in self.shared]
        games = self._installed_games()
        if games:
            rows.append(ft.Container(C.meta("GAME STORAGE"), padding=ft.Padding(T.S2, T.S3, 0, T.px(2))))
            for pkg, title in games:
                loc = self.game_locs.get(pkg) or {"id": f"app:{pkg}", "label": title, "package": pkg}
                rows.append(self._location_row(loc, ft.Icons.SPORTS_ESPORTS_OUTLINED))
        rows.append(ft.Container(C.meta("ADVANCED"), padding=ft.Padding(T.S2, T.S3, 0, T.px(2))))
        home = {"id": "home", "label": "Home folder", "path": self.app.target.frame.home, "android": "",
                "shared": False}
        rows.append(self._location_row(home, ft.Icons.HOME_OUTLINED, "everything in ~ (not seen by games)"))
        self.locations.controls = rows
        C.update(self.locations)

    def _installed_games(self) -> list[tuple[str, str]]:
        from ...core import library

        out = []
        for d in (self.app.frame_info or {}).get("installed", []):
            if d.get("kind") == "pcvr":
                continue
            g = library.game(d["package"]) or {}
            out.append((d["package"], g.get("title") or d.get("title") or d["package"]))
        return sorted(out, key=lambda x: x[1].lower())

    # ---------------------------------------------------------------- loading (background threads)
    def _load_locations(self, package: str | None) -> None:
        from ...install import files

        frame = self.app.target.frame
        try:
            if not self.shared:
                self.shared = [{**t, "label": t["id"].capitalize(), "package": None}
                               for t in files.storage_targets(frame)]
        except Exception as exc:  # noqa: BLE001
            self.status.value = f"Couldn't read the Frame's folders: {exc}"
            C.update(self.status)
        self._render_locations()
        if package:
            self.open_location({"id": f"app:{package}", "package": package,
                                "label": dict(self._installed_games()).get(package, package)})
        elif self.loc is None and self.shared:
            self.open_location(self.shared[0])
        elif self.loc is not None:
            self.load()

    def open_location(self, loc: dict) -> None:
        def work():
            nonlocal loc
            if loc.get("package") and not loc.get("path"):
                from ...install import files

                try:
                    t = {x["id"]: x for x in files.storage_targets(self.app.target.frame, loc["package"])}["app"]
                except Exception as exc:  # noqa: BLE001
                    self.app.toast(f"Couldn't open the game's storage: {exc}", error=True)
                    return
                loc = {**loc, "path": t["path"], "android": t["android"], "shared": False}
                self.game_locs[loc["package"]] = loc
            self.loc, self.path = loc, loc["path"]
            self._render_locations()
            self.load()
        self.app.run_bg(work)

    def load(self) -> None:
        if not self.loc:
            return
        loc, path = self.loc, self.path
        self.status.value = "Loading…"
        C.update(self.status)

        def work():
            from ...install import files

            try:
                entries = files.list_dir(self.app.target.frame, loc["path"], path, hidden=self.hidden)
            except Exception as exc:  # noqa: BLE001
                self.status.value = f"Couldn't list {path}: {exc}"
                C.update(self.status)
                return
            if (self.loc, self.path) != (loc, path):
                return  # the user moved on meanwhile
            self.entries = entries
            self.selected &= {e.path for e in entries}
            self._render_listing()
        self.app.run_bg(work)

    # ---------------------------------------------------------------- listing
    def _render_listing(self) -> None:
        loc = self.loc
        self.crumb_row.controls = []
        for i, (label, p) in enumerate(crumbs(loc["path"], self.path, loc["label"])):
            if i:
                self.crumb_row.controls.append(ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=T.px(16), color=T.TEXT_3))
            self.crumb_row.controls.append(ft.TextButton(label, on_click=lambda e, p=p: self.cd(p),
                                                         style=ft.ButtonStyle(color=T.TEXT if p == self.path
                                                                              else T.TEXT_2)))
        android = loc.get("android")
        rel = posixpath.relpath(self.path, loc["path"]) if self.path != loc["path"] else ""
        self.where.value = (f"Games see this folder as {posixpath.join(android, rel) if rel else android}"
                            if android else self.path)
        rows = []
        self.checks = {}
        if self.path != loc["path"]:
            rows.append(self._row(None))
        rows += [self._row(e) for e in self.entries]
        if not self.entries:
            rows.append(ft.Container(C.meta("This folder is empty. Upload files with the buttons above."),
                                     padding=T.S4))
        self.listing.controls = rows
        n_dirs = sum(e.is_dir for e in self.entries)
        size = sum(e.size for e in self.entries if not e.is_dir)
        self.status.value = f"{n_dirs} folder(s), {len(self.entries) - n_dirs} file(s), {human(size)}"
        self._update_selection(render=False)
        for c in (self.crumb_row, self.where, self.listing, self.status):
            C.update(c)

    def _row(self, e) -> ft.Control:
        if e is None:  # ".."
            return ft.Container(ft.Row([ft.Icon(ft.Icons.ARROW_UPWARD_ROUNDED, size=T.px(20), color=T.TEXT_2),
                                        C.body("..", T.TEXT_2)], spacing=T.S3),
                                padding=ft.Padding(T.S3, T.px(8), T.S3, T.px(8)), border_radius=T.RADIUS_SM, ink=True,
                                on_click=lambda ev: self.cd(posixpath.dirname(self.path)))
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.mtime)) if e.mtime else ""
        info = ("folder" if e.is_dir else human(e.size)) + (" · link" if e.link else "") + (f" · {when}" if when else "")
        actions = [C.icon_btn(ft.Icons.DOWNLOAD_ROUNDED, "Download to this PC", lambda ev, x=e: self.download([x]))]
        if not self._protected(e):
            actions += [C.icon_btn(ft.Icons.DRIVE_FILE_RENAME_OUTLINE_ROUNDED, "Rename", lambda ev, x=e: self.rename(x)),
                        C.icon_btn(ft.Icons.DELETE_OUTLINE_ROUNDED, "Delete", lambda ev, x=e: self.delete([x]))]
        check = ft.Checkbox(value=e.path in self.selected, active_color=T.ACCENT, check_color=T.ON_ACCENT,
                            on_change=lambda ev, p=e.path: self._toggle(p, ev.control.value),
                            disabled=self._protected(e))
        self.checks[e.path] = check
        return ft.Container(
            ft.Row([check,
                    ft.Icon(file_icon(e.name, e.is_dir), size=T.px(20), color=T.ACCENT if e.is_dir else T.TEXT_2),
                    ft.Column([C.body(e.name, T.TEXT, weight=ft.FontWeight.W_500, max_lines=1,
                                      overflow=ft.TextOverflow.ELLIPSIS), C.meta(info)], spacing=0, expand=True),
                    *actions], spacing=T.S3),
            padding=ft.Padding(T.S3, T.px(6), T.S2, T.px(6)), border_radius=T.RADIUS_SM, ink=e.is_dir,
            on_click=(lambda ev, p=e.path: self.cd(p)) if e.is_dir else None)

    def cd(self, path: str) -> None:
        self.path = path
        self.selected.clear()
        self.load()

    def _toggle(self, path: str, on: bool) -> None:
        (self.selected.add if on else self.selected.discard)(path)
        check = self.checks.get(path)
        if check is not None and check.value != on:
            check.value = on
            C.update(check)
        self._update_selection()

    def _toggle_all(self, e) -> None:
        self.selected = {x.path for x in self.entries if self._selectable(x)} if e.control.value else set()
        self._render_listing()

    def _clear_selection(self) -> None:
        self.selected.clear()
        self._render_listing()

    def _selectable(self, e) -> bool:
        return not self._protected(e)

    def _protected(self, e) -> bool:
        """Lepton's links at the top of a game's storage (Movies → ~/Videos, …): renaming/deleting them breaks them."""
        return e.link and posixpath.dirname(e.path) == posixpath.normpath(self.loc["path"])

    def _update_selection(self, render: bool = True) -> None:
        n = len(self.selected)
        size = sum(x.size for x in self.entries if x.path in self.selected and not x.is_dir)
        self.sel_label.value = f"{n} selected" + (f" · {human(size)} in files" if size else "")
        self.sel_bar.visible = bool(n)
        self.select_all.value = bool(self.entries) and n == len([x for x in self.entries if self._selectable(x)])
        if render:
            for c in (self.sel_bar, self.select_all):
                C.update(c)

    def _chosen(self) -> list:
        return [x for x in self.entries if x.path in self.selected]

    async def _download_selected(self, e=None):
        if self.selected:
            await self.download(self._chosen())

    def _delete_selected(self) -> None:
        items = [x for x in self._chosen() if not self._protected(x)]
        if items:
            self.delete(items)

    def _toggle_hidden(self, e) -> None:
        self.hidden = bool(e.control.value)
        self.load()

    # ---------------------------------------------------------------- actions
    async def upload_files(self, e=None):
        files = await ft.FilePicker().pick_files(allow_multiple=True)
        paths = [Path(f.path) for f in files or [] if f.path]
        if paths:
            self.upload(paths)

    async def upload_folder(self, e=None):
        path = await ft.FilePicker().get_directory_path(dialog_title="Folder to upload to the Frame")
        if path:
            self.upload([Path(path)])

    def upload(self, paths: list[Path]) -> None:
        if not self.loc:
            return
        app, loc, dest = self.app, self.loc, self.path

        def run(job):
            from ...install import files

            sent, skipped, total = files.upload(app.target.frame, paths, dest, job.reporter)
            return f"Uploaded {len(sent)} file(s)" + (f", {len(skipped)} already there" if skipped else "")
        app.submit(f"Upload to {loc['label']}", run, loc.get("package"), kind="tool-frame", open_panel=True)

    async def download(self, items: list) -> None:
        folder = await ft.FilePicker().get_directory_path(dialog_title="Download to which folder on this PC?")
        if not folder:
            return
        app, root = self.app, self.loc["path"]

        def run(job):
            from ...install import files

            r = files.download(app.target.frame, root, [x.path for x in items], Path(folder), job.reporter)
            return f"Downloaded {r['files']} file(s) to {r['folder']}"
        app.submit(f"Download {items[0].name}" + (f" and {len(items) - 1} more" if len(items) > 1 else ""), run,
                   None, kind="tool-frame", open_panel=True)

    def new_folder(self) -> None:
        self._ask_name("New folder", "Folder name", "", "Create", lambda name: self._fs(
            lambda files, frame: files.make_dir(frame, self.loc["path"], self.path, name)))

    def rename(self, e) -> None:
        self._ask_name(f"Rename {e.name}", "New name", e.name, "Rename", lambda name: self._fs(
            lambda files, frame: files.rename(frame, self.loc["path"], e.path, name)))

    def delete(self, items: list) -> None:
        names = ", ".join(x.name for x in items[:3]) + (" …" if len(items) > 3 else "")
        what = "folder and everything in it" if any(x.is_dir for x in items) else "file"
        C.confirm(self.app.page, f"Delete {names}?", f"This deletes the {what} on the Frame. It can't be undone.",
                  "Delete", lambda: (self.selected.difference_update(x.path for x in items),
                                     self._fs(lambda files, frame: files.delete(frame, self.loc["path"],
                                                                                [x.path for x in items]))),
                  danger=True)

    def _fs(self, op) -> None:
        """A quick file operation in the background, then reload the folder (errors become a toast)."""
        def work():
            from ...install import files

            try:
                op(files, self.app.target.frame)
            except Exception as exc:  # noqa: BLE001
                self.app.toast(str(exc), error=True)
            self.load()
        self.app.run_bg(work)

    def _ask_name(self, heading: str, label: str, value: str, ok: str, on_ok) -> None:
        page = self.app.page
        field = ft.TextField(label=label, value=value, autofocus=True, width=T.px(380))

        def go(e=None):
            page.pop_dialog()
            if (field.value or "").strip():
                on_ok(field.value.strip())
        field.on_submit = go
        page.show_dialog(ft.AlertDialog(title=ft.Text(heading, color=T.TEXT, weight=ft.FontWeight.W_600),
                                        bgcolor=T.SURFACE_2, content=field,
                                        actions=[C.ghost("Cancel", on_click=lambda e: page.pop_dialog()),
                                                 C.primary(ok, on_click=go)]))
