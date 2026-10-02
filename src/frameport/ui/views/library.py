"""Library: search, filters, tags and a grid of game cards with hover quick actions.

Performance: the view is created once and re-mounted; cards are built in a background thread and streamed in batches;
search and filters only toggle visibility / order of existing cards; images are small thumbnails by URL."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import flet as ft

from ...artwork import thumbs
from ...core import library
from .. import components as C
from .. import theme as T
from ..help import HELP

if TYPE_CHECKING:
    from ..app import FramePortApp

DEFAULT_FILTERS = {"q": "", "where": "all", "platform": "all", "status": "all", "sort": "name", "tags": []}
STATUS_ORDER = {"works": 0, "issues": 1, "unknown": 2, "unsupported": 3}


def auto_tags(game: dict) -> list[str]:
    """Tags FramePort derives from the analysis (not stored): platform, engine, XR API, mixed reality."""
    a = game.get("analysis") or {}
    out = ["PC VR" if game.get("kind") == "rift" else "Quest"]
    if a.get("engine") and a["engine"] not in ("Other", "?"):
        out.append(a["engine"])
    xr = a.get("xr") or ""
    out += [t for t in ("OpenXR", "VrApi", "LibOVR") if t in xr]
    patches = (game.get("recipe") or {}).get("patches") or {}
    if "patch_force_passthrough" in patches or "adapter.scene_emul" in patches:
        out.append("Mixed reality")
    out += ((game.get("details") or {}).get("genres") or [])[:4]  # from the store
    return out


def user_tags(game: dict) -> list[str]:
    return list(game.get("tags") or [])


def game_tags(game: dict) -> list[str]:
    seen, out = set(), []
    for t in user_tags(game) + auto_tags(game):
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def all_tags(games: list[dict]) -> list[str]:
    """Every tag in the library: the user's own first (alphabetical), then the automatic ones."""
    mine = sorted({t for g in games for t in user_tags(g)}, key=str.lower)
    auto = sorted({t for g in games for t in auto_tags(g)} - set(mine), key=str.lower)
    return mine + auto


def last_used(game: dict) -> float:
    """Latest play, install or launch test (seconds since epoch; 0 if never)."""
    times = [(game.get("last_test") or {}).get("time") or 0, game.get("last_played") or 0]
    times += [i.get("time") or 0 for i in (game.get("installs") or {}).values() if isinstance(i, dict)]
    return max(times)


def game_size(game: dict) -> int:
    if game.get("kind") == "rift":
        return ((game.get("analysis") or {}).get("extra") or {}).get("data_bytes") or 0
    return game.get("data_bytes") or 0


from ...core.titles import counterparts, display_title, twins  # noqa: E402,F401  (used by the views)


def normalize_tag(text: str) -> str:
    return " ".join((text or "").replace(",", " ").split())[:32]


# ------------------------------------------------------------------------------------------ pure helpers (tested)
def location(game: dict, frame_info: dict | None, pc_installs: set[str]) -> set[str]:
    st = C.install_state(game, frame_info)
    out = set()
    if st in ("installed", "outdated"):
        out.add("frame")
    if game["package"] in pc_installs:
        out.add("pc")
    return out


def filter_games(games: list[dict], f: dict, frame_info: dict | None = None,
                 pc_installs: set[str] | frozenset = frozenset()) -> list[dict]:
    q = (f.get("q") or "").strip().lower()
    out = []
    for g in games:
        tags = [t.lower() for t in game_tags(g)]
        if q and q not in f"{g.get('title') or ''} {g['package']} {' '.join(tags)}".lower():
            continue
        if any(t.lower() not in tags for t in f.get("tags") or []):
            continue
        rift = g.get("kind") == "rift"
        if f.get("platform") == "quest" and rift or f.get("platform") == "pcvr" and not rift:
            continue
        if f.get("status", "all") != "all" and (g.get("recipe") or {}).get("status", "unknown") != f["status"]:
            continue
        where = f.get("where", "all")
        loc = location(g, frame_info, set(pc_installs))
        if where == "frame" and "frame" not in loc or where == "pc" and "pc" not in loc or \
                where == "none" and loc:
            continue
        out.append(g)
    key = {
        "name": lambda g: (g.get("title") or g["package"]).lower(),
        "recent": lambda g: -(g.get("added") or 0),
        "played": lambda g: -last_used(g),
        "size": lambda g: -game_size(g),
        "status": lambda g: (STATUS_ORDER.get((g.get("recipe") or {}).get("status", "unknown"), 9),
                             (g.get("title") or g["package"]).lower()),
    }.get(f.get("sort", "name"))
    return sorted(out, key=key)


def load_filters() -> dict:
    saved = library.setting("ui.library") or {}
    return {**DEFAULT_FILTERS, "tags": [], **{k: v for k, v in saved.items() if k in DEFAULT_FILTERS and k != "q"}}


def save_filters(f: dict) -> None:
    library.set_setting("ui.library", {k: v for k, v in f.items() if k != "q"})


# ------------------------------------------------------------------------------------------ view
BATCH = 8
def card_shadow(hover: bool = False) -> ft.BoxShadow:
    """Library cards float on the dark background; hovering lifts them further."""
    if hover:
        return ft.BoxShadow(blur_radius=36, spread_radius=2, color=T.soft("#000000", 0.75), offset=ft.Offset(0, 14))
    return ft.BoxShadow(blur_radius=22, spread_radius=1, color=T.soft("#000000", 0.55), offset=ft.Offset(0, 8))


def quick_icon(label: str) -> str:
    """The card's round quick button shows one symbol per action; the full label is its tooltip."""
    for prefix, icon in (("Play", ft.Icons.PLAY_ARROW_ROUNDED), ("Update", ft.Icons.UPGRADE_ROUNDED),
                         ("Reinstall", ft.Icons.REFRESH_ROUNDED)):
        if label.startswith(prefix):
            return icon
    return ft.Icons.DOWNLOAD_ROUNDED


class LibraryView:
    """Created once per app; `mount()` returns the same control tree, `reload()` refreshes it in the background."""

    def __init__(self, app: FramePortApp):
        self.app = app
        self.f = app.lib_filters
        self.cards: dict[str, tuple[tuple, ft.Control]] = {}  # package -> (state key, card)
        self.checks: dict[str, ft.Control] = {}  # package -> selection checkbox overlay
        self.selected: set[str] = set()
        self.select_mode = False
        self.sel_bar = ft.Container(visible=False)
        self.resume_bar = ft.Container(visible=False)
        self.update_bar = ft.Container(visible=False)  # "FramePort x.y is available" (ui/updater.py)
        self.games: list[dict] = []
        self._lock = threading.Lock()
        self._search_timer: threading.Timer | None = None
        self._gen = 0
        self.grid = ft.GridView(expand=True, max_extent=T.px(196), child_aspect_ratio=0.62, spacing=T.S4,
                                run_spacing=T.S4, padding=ft.Padding(0, T.S2, T.S2, T.S5))
        self.count = C.meta("")
        self.subtitle = C.body("", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.search = ft.TextField(
            value=self.f["q"], hint_text="Search games and tags", prefix_icon=ft.Icons.SEARCH_ROUNDED, dense=True,
            width=T.px(260), border_radius=T.RADIUS_SM, bgcolor=T.SURFACE_3, border_color=ft.Colors.TRANSPARENT,
            focused_border_color=T.ACCENT, content_padding=ft.Padding(T.px(12), T.px(8), T.px(12), T.px(8)), text_size=T.T_BODY,
            on_change=self._on_search)
        app.search_field = self.search
        self.filters = ft.Container()
        self.hint = ft.Container(visible=False)
        # one right-click menu for every card: filled with that game's actions when it opens (open_menu)
        self.menu = ft.ContextMenu(content=self.grid, secondary_trigger=None, tertiary_trigger=None, expand=True)
        self.body = ft.Container(self.menu, expand=True)
        add = ft.PopupMenuButton(
            content=ft.Container(ft.Row([ft.Icon(ft.Icons.ADD_ROUNDED, color=T.ON_ACCENT, size=T.px(18)),
                                         ft.Text("Add games", color=T.ON_ACCENT, weight=ft.FontWeight.W_600, size=T.px(13))],
                                        spacing=T.px(6), tight=True),
                                 bgcolor=T.ACCENT, border_radius=T.RADIUS_SM, padding=ft.Padding(T.px(14), T.px(9), T.px(16), T.px(9))),
            items=[ft.PopupMenuItem(content=ft.Text("Scan a folder…"), icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                                    on_click=app.pick_folder),
                   ft.PopupMenuItem(content=ft.Text("Add one game folder…"), icon=ft.Icons.CREATE_NEW_FOLDER_ROUNDED,
                                    on_click=app.pick_game_folder),
                   ft.PopupMenuItem(content=ft.Text("Add an APK file…"), icon=ft.Icons.ANDROID_ROUNDED,
                                    on_click=app.pick_apk)],
            bgcolor=T.SURFACE_2, tooltip="")
        self.rescan_btn = C.secondary("Rescan folders", ft.Icons.REFRESH_ROUNDED, app.rescan,
                                      tooltip=C.tip(HELP["rescan"]))
        self.update_all_btn = C.secondary("Update all", ft.Icons.SYSTEM_UPDATE_ALT_ROUNDED, lambda e: app.update_all(),
                                          tooltip=C.tip(HELP["update_all"]))
        self.update_all_btn.visible = False
        self.select_btn = C.secondary("Select", ft.Icons.CHECKLIST_ROUNDED, lambda e: self.set_select_mode(True),
                                      tooltip=C.tip(HELP["select"]))
        self.root = ft.Column([
            ft.Row([ft.Column([ft.Text("Library", size=T.T_TITLE, weight=ft.FontWeight.W_700, color=T.TEXT,
                                       no_wrap=True), self.subtitle], spacing=T.px(2), expand=True), self.search,
                    self.update_all_btn, self.rescan_btn, self.select_btn, add], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=T.S3),
            self.update_bar, self.resume_bar, self.hint, self.filters, self.body, self.sel_bar,
        ], expand=True, spacing=T.S4)
        # skeleton cards until the first batch arrives
        n = min(len(library.games()), 15)
        self.grid.controls = [ft.Container(bgcolor=T.SURFACE, border_radius=T.RADIUS, border=ft.Border.all(1, T.BORDER),
                                           opacity=0.6) for _ in range(n)]

    # ---------------------------------------------------------------- public
    def mount(self) -> ft.Control:
        self._update_hint()
        self.update_resume_bar()
        self.update_update_bar()
        self._update_sel_bar()
        return self.root

    def reload(self) -> None:
        """Rebuild changed cards (streamed in batches), then apply filters. Call from a background thread."""
        from ...targets.pc_revive import local_installs

        self._gen += 1  # before waiting for the lock: an older reload still running sees it and stops early
        gen = self._gen
        with self._lock:
            if gen != self._gen:
                return  # an even newer reload is queued behind this one
            games = library.games()
            self.games = games
            pc = set(local_installs())
            tw = twins(games)
            self._update_header(games, pc)
            self.update_update_bar()
            if not games:
                self.body.content = C.empty_state(
                    ft.Icons.LIBRARY_ADD_ROUNDED, "Add your games",
                    "Point FramePort at a folder with Quest game dumps (APK + OBB) or Oculus Rift PC games (one folder "
                    "per game, or a folder of them). It finds them, works out what each needs and fetches artwork.",
                    C.primary("Scan a folder", ft.Icons.FOLDER_OPEN_ROUNDED, self.app.pick_folder, big=True),
                    C.secondary("Add an APK file", ft.Icons.ANDROID_ROUNDED, self.app.pick_apk))
                self.cards.clear()
                C.update(self.root)
                return
            if self.body.content is not self.menu:
                self.body.content = self.menu
            first = not self.cards
            if first:
                C.update(self.root)
            pending = []
            for g in games:
                key = self._key(g, pc, tw)
                old = self.cards.get(g["package"])
                if old and old[0] == key:
                    continue
                pending.append((g, key))
            for i in range(0, len(pending), BATCH):
                if gen != self._gen:
                    return  # a newer reload took over
                for g, key in pending[i:i + BATCH]:
                    self.cards[g["package"]] = (key, self.card(g, pc, tw))
                if first or i + BATCH >= len(pending):
                    self._apply(update=True)
            gone = set(self.cards) - {g["package"] for g in games}
            for pkg in gone:
                self.cards.pop(pkg, None)
            if gone or not pending:
                self._apply(update=True)

    # ---------------------------------------------------------------- selection (queue several installs)
    def set_select_mode(self, on: bool) -> None:
        self.select_mode = on
        if not on:
            self.selected.clear()
        for pkg, chk in self.checks.items():
            chk.visible = on
            chk.content.value = pkg in self.selected
        self._update_sel_bar()
        C.update(self.grid, self.sel_bar, self.select_btn)

    def toggle_selected(self, pkg: str) -> None:
        self.selected.symmetric_difference_update({pkg})
        chk = self.checks.get(pkg)
        if chk:
            chk.content.value = pkg in self.selected
            C.update(chk)
        self._update_sel_bar()

    def select_visible(self) -> None:
        self.selected |= {pkg for pkg, (_, card) in self.cards.items() if card.visible}
        self.set_select_mode(True)

    def _update_sel_bar(self) -> None:
        app = self.app
        n = len(self.selected)
        self.select_btn.visible = not self.select_mode
        rift = [p for p in self.selected if p.startswith("rift.")]
        from ...core import winhost

        self.sel_bar.visible = self.select_mode
        self.sel_bar.content = ft.Container(ft.Row([
            ft.Icon(ft.Icons.CHECKLIST_ROUNDED, color=T.ACCENT),
            C.body(f"{n} selected" if n else "Select games to install", T.TEXT, weight=ft.FontWeight.W_600),
            ft.Container(expand=True),
            C.ghost("Select all shown", on_click=lambda e: self.select_visible()),
            C.ghost("Clear", on_click=lambda e: (self.selected.clear(), self.set_select_mode(True))),
            *([C.secondary(f"Install {len(rift)} on this PC", ft.Icons.COMPUTER_ROUNDED,
                           lambda e: app.install_many(sorted(rift), "pc"), disabled=not winhost.available())]
              if rift else []),
            C.primary(f"Install {n} on Frame" if n else "Install on Frame", ft.Icons.VIEW_IN_AR_ROUNDED,
                      lambda e: app.install_many(sorted(self.selected), "frame"),
                      disabled=not n or app.frame_state != "connected",
                      tooltip=None if app.frame_state == "connected" else "Connect your Frame first"),
            C.ghost("Done", on_click=lambda e: self.set_select_mode(False)),
        ], spacing=T.S2), bgcolor=T.SURFACE_2, border_radius=T.RADIUS, padding=ft.Padding(T.S4, T.S2, T.S2, T.S2),
            border=ft.Border.all(1, T.ACCENT))
        C.update(self.sel_bar, self.select_btn)

    def update_update_bar(self) -> None:
        from ..updater import library_bar

        bar = library_bar(self.app)
        self.update_bar.visible = bar is not None
        self.update_bar.content = bar
        C.update(self.update_bar)

    def update_resume_bar(self) -> None:
        """Installs that didn't finish (cancelled, failed, or the app closed): resume them from here."""
        app = self.app
        pending = app.unfinished_installs()
        self.resume_bar.visible = bool(pending)
        if pending:
            names = ", ".join(app._title(p) for p in list(pending)[:3]) + ("…" if len(pending) > 3 else "")
            self.resume_bar.content = C.callout(ft.Row([
                C.body(f"{len(pending)} install{'s' if len(pending) != 1 else ''} didn't finish: {names}. What was "
                       "already copied is kept, so resuming continues where it stopped.", T.TEXT, expand=True),
                C.primary("Resume", ft.Icons.PLAY_ARROW_ROUNDED, lambda e: app.resume_installs()),
                C.ghost("Dismiss", on_click=lambda e: app.forget_installs()),
            ], spacing=T.S3), "warn", ft.Icons.PAUSE_CIRCLE_OUTLINE_ROUNDED)
        C.update(self.resume_bar)

    def refresh_async(self) -> None:
        self.app.page.run_thread(self.reload)

    # ---------------------------------------------------------------- filtering (no rebuilds)
    def _on_search(self, e):
        self.f["q"] = e.control.value
        if self._search_timer:
            self._search_timer.cancel()
        self._search_timer = threading.Timer(0.18, lambda: self._apply(update=True))
        self._search_timer.daemon = True
        self._search_timer.start()

    def _set(self, key, value):
        self.f[key] = value
        save_filters(self.f)
        self._update_header(self.games, set(self.app.pc_installs()))
        self._apply(update=True)
        C.update(self.filters)

    def _apply(self, update: bool = False) -> None:
        pc = set(self.app.pc_installs())
        shown = filter_games(self.games, self.f, self.app.frame_info, pc)
        order = [g["package"] for g in shown]
        visible = set(order)
        rest = [p for p in self.cards if p not in visible]
        controls = []
        for pkg in order + rest:
            entry = self.cards.get(pkg)
            if not entry:
                continue
            card = entry[1]
            card.visible = pkg in visible
            controls.append(card)
        self.grid.controls = controls
        n = len(self.games)
        self.count.value = f"Showing {len(shown)} of {n}" if len(shown) != n else ""
        if update:
            C.update(self.grid, self.count)

    # ---------------------------------------------------------------- header / filter bar
    def _update_hint(self):
        app = self.app
        if app.frame_state != "connected":
            self.hint.content = C.callout(ft.Row([
                C.body("Connect your Steam Frame to install games and see what's on it.", T.TEXT, expand=True),
                C.ghost("Connect", ft.Icons.ARROW_FORWARD_ROUNDED, lambda e: app.go("frame"), color=T.ACCENT)]),
                "info", ft.Icons.VIEW_IN_AR_ROUNDED)
            self.hint.visible = True
        else:
            self.hint.visible = False

    def _update_header(self, games: list[dict], pc: set[str]) -> None:
        n = len(games)
        on = sum(1 for g in games if C.install_state(g, self.app.frame_info) in ("installed", "outdated"))
        n_pc = len(pc & {g["package"] for g in games})
        parts = [f"{n} game{'s' if n != 1 else ''}"]
        if self.app.frame_info is not None:
            parts.append(f"{on} on your Frame")
        if n_pc:
            parts.append(f"{n_pc} on this PC")
        self.subtitle.value = " · ".join(parts)
        updates = len(self.app.updatable())
        self.update_all_btn.visible = updates > 0
        self.update_all_btn.content = f"Update all ({updates})" if updates else "Update all"
        C.update(self.update_all_btn)
        self._update_hint()
        has_rift = any(g.get("kind") == "rift" for g in games)
        where = [("all", "All"), ("frame", "On Frame"), ("none", "Not installed")]
        if has_rift:
            where.insert(2, ("pc", "On this PC"))
        self.filters.content = ft.Row([
            self._seg("where", where),
            self._seg("platform", [("all", "All"), ("quest", "Quest"), ("pcvr", "PC VR")]) if has_rift else
            ft.Container(),
            self._menu_chip("status", "Status", [("all", "Any"), ("works", "Works"), ("issues", "Works with issues"),
                                                 ("unknown", "Untested"), ("unsupported", "Can't run")]),
            C.help_icon("status"),
            self._tag_menu(games),
            ft.Container(expand=True),
            self.count,
            self._menu_chip("sort", "Sort", [("name", "Name"), ("recent", "Recently added"), ("played", "Recently used"),
                                             ("status", "Status"), ("size", "Size")]),
        ], spacing=T.S2, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.filters.visible = bool(games)
        C.update(self.subtitle, self.hint, self.filters)

    def _seg(self, key: str, options: list[tuple[str, str]]) -> ft.Control:
        items = []
        for value, label in options:
            on = self.f.get(key) == value
            items.append(ft.Container(
                ft.Text(label, size=T.T_META, weight=ft.FontWeight.W_600, color=T.TEXT if on else T.TEXT_2),
                padding=ft.Padding(T.px(12), T.px(6), T.px(12), T.px(6)), border_radius=T.px(20), bgcolor=T.SURFACE_3 if on else None,
                on_click=lambda e, v=value: self._set(key, v), ink=True))
        return ft.Container(ft.Row(items, spacing=T.px(2), tight=True), padding=T.px(3), border_radius=T.px(22),
                            border=ft.Border.all(1, T.BORDER))

    def _menu_chip(self, key: str, label: str, options: list[tuple[str, str]]) -> ft.Control:
        current = dict(options).get(self.f.get(key), options[0][1])
        return ft.PopupMenuButton(
            content=ft.Container(ft.Row([C.meta(label + ":"), C.body(current, T.TEXT, size=T.T_META),
                                         ft.Icon(ft.Icons.EXPAND_MORE_ROUNDED, size=T.px(16), color=T.TEXT_2)],
                                        spacing=T.px(4), tight=True),
                                 padding=ft.Padding(T.px(12), T.px(7), T.px(8), T.px(7)), border_radius=T.px(20), border=ft.Border.all(1, T.BORDER)),
            items=[ft.PopupMenuItem(content=ft.Text(text), checked=self.f.get(key) == value,
                                    on_click=lambda e, v=value: self._set(key, v)) for value, text in options],
            bgcolor=T.SURFACE_2, tooltip="")

    def _toggle_tag(self, tag: str):
        tags = list(self.f.get("tags") or [])
        tags.remove(tag) if tag in tags else tags.append(tag)
        self._set("tags", tags)

    def _tag_menu(self, games: list[dict]) -> ft.Control:
        chosen = self.f.get("tags") or []
        label = ", ".join(chosen) if chosen else "Any"
        items = [ft.PopupMenuItem(content=ft.Text(t), checked=t in chosen, on_click=lambda e, t=t: self._toggle_tag(t))
                 for t in all_tags(games)]
        if chosen:
            items.append(ft.PopupMenuItem(content=ft.Text("Clear tags"), icon=ft.Icons.CLEAR_ROUNDED,
                                          on_click=lambda e: self._set("tags", [])))
        return ft.PopupMenuButton(
            content=ft.Container(ft.Row([ft.Icon(ft.Icons.SELL_OUTLINED, size=T.px(14), color=T.TEXT_2), C.meta("Tags:"),
                                         C.body(label if len(label) < 28 else f"{len(chosen)} selected", T.TEXT,
                                                size=T.T_META),
                                         ft.Icon(ft.Icons.EXPAND_MORE_ROUNDED, size=T.px(16), color=T.TEXT_2)],
                                        spacing=T.px(4), tight=True),
                                 padding=ft.Padding(T.px(12), T.px(7), T.px(8), T.px(7)), border_radius=T.px(20),
                                 border=ft.Border.all(1, T.ACCENT if chosen else T.BORDER)),
            items=items, bgcolor=T.SURFACE_2, tooltip=C.tip(HELP["tags"]))

    # ---------------------------------------------------------------- cards
    def _key(self, g: dict, pc: set[str], tw: set[str]) -> tuple:
        pkg = g["package"]
        job = self.app.jobs.busy_with(pkg)
        return (display_title(g, tw), (g.get("recipe") or {}).get("status"), C.install_state(g, self.app.frame_info),
                pkg in pc, bool(job), self.app.quick_action(g)[0], thumbs.url(pkg, ("portrait", "square", "icon")),
                self.app.frame_state)

    def card(self, g: dict, pc: set[str], tw: set[str]) -> ft.Control:
        app = self.app
        pkg = g["package"]
        rift = g.get("kind") == "rift"
        art = thumbs.url(pkg, ("portrait", "square", "icon"))
        state = C.install_state(g, app.frame_info)
        on_pc = pkg in pc
        status = (g.get("recipe") or {}).get("status", "unknown")
        s_label, s_color = C.STATUS_STYLE.get(status, C.STATUS_STYLE["unknown"])
        job = app.jobs.busy_with(pkg)
        badges = []
        if job:
            badges.append(C.pill("Working…", T.ACCENT, ft.Icons.SYNC_ROUNDED, solid=True))
        elif state in ("installed", "outdated"):
            badges.append(C.install_badge(state))
        if on_pc:
            badges.append(C.install_badge("on_pc"))
        if rift and g.get("exe_confirmed") is False:
            badges.append(C.pill("Check exe", T.WARN, ft.Icons.HELP_OUTLINE_ROUNDED, overlay=True,
                                 tooltip=C.tip(HELP["check_exe"])))
        platform = C.pill("PC VR" if rift else "Quest", T.PC if rift else T.TEXT,
                          ft.Icons.COMPUTER_ROUNDED if rift else ft.Icons.VIEW_IN_AR_ROUNDED, overlay=True,
                          tooltip=C.tip(HELP["platform_pcvr" if rift else "platform_quest"]))
        check = ft.Container(ft.Checkbox(value=pkg in self.selected, active_color=T.ACCENT, check_color=T.ON_ACCENT,
                                         on_change=lambda e: self.toggle_selected(pkg)),
                             bgcolor=T.soft("#000000", 0.6), border_radius=T.px(8), left=T.px(6), top=T.px(40),
                             visible=self.select_mode)
        self.checks[pkg] = check
        quick_label, _ = app.quick_action(g)
        circle = ft.Container(
            ft.Icon(quick_icon(quick_label), size=T.px(56), color=T.ON_ACCENT),
            width=T.px(96), height=T.px(96), border_radius=T.px(48), bgcolor=T.ACCENT, alignment=ft.Alignment.CENTER,
            shadow=ft.BoxShadow(blur_radius=28, spread_radius=2, color=T.soft("#000000", 0.6), offset=ft.Offset(0, 6)),
            tooltip=ft.Tooltip(message=quick_label, wait_duration=800), ink=True,
            on_click=lambda e: app.primary_action(pkg),
            scale=0.85, animate_scale=ft.Animation(160, ft.AnimationCurve.EASE_OUT)) if quick_label else None
        # one big round Play / Install button in the middle of the cover, shown on hover
        quick = ft.Container(circle, left=0, right=0, top=0, bottom=T.px(56), alignment=ft.Alignment.CENTER,
                             opacity=0, animate_opacity=ft.Animation(160, ft.AnimationCurve.EASE_OUT)) \
            if circle else None
        dim = state == "missing" and not on_pc
        tile = ft.Container(
            ft.Stack([
                C.art_fill(art, left=0, right=0, top=0, bottom=0, opacity=0.5 if dim else 1.0,
                           placeholder_icon=ft.Icons.COMPUTER_ROUNDED if rift else ft.Icons.VIEW_IN_AR_ROUNDED),
                ft.Container(C.bottom_fade(None, 0.92), left=0, right=0, bottom=0, top=T.px(90)),
                ft.Container(ft.Row([platform], spacing=T.px(4)), left=T.px(10), top=T.px(10)),
                ft.Container(ft.Column(badges, spacing=T.px(4), horizontal_alignment=ft.CrossAxisAlignment.END),
                             right=T.px(10), top=T.px(10)),
                *([quick] if quick else []),
                check,
                ft.Container(ft.Column([
                    ft.Text(display_title(g, tw), size=T.px(14), weight=ft.FontWeight.W_700, color=T.TEXT, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Container(ft.Row([C.dot(s_color, 7), C.meta(s_label, T.TEXT_2)], spacing=T.px(6), tight=True),
                                 tooltip=C.tip(HELP["status"])),
                ], spacing=T.px(4)), left=T.px(12), right=T.px(12), bottom=T.px(12)),
            ], expand=True),
            border_radius=T.RADIUS, bgcolor=T.SURFACE, border=ft.Border.all(1, T.BORDER), expand=True,
            scale=1.0, animate_scale=ft.Animation(140, ft.AnimationCurve.EASE_OUT),
            shadow=card_shadow(),
            tooltip=ft.Tooltip(message="Click to open · right-click for quick actions", wait_duration=1500),
            on_click=lambda e: self.toggle_selected(pkg) if self.select_mode else app.open_game(pkg))

        def hover(e):
            on = e.data in (True, "true")
            tile.scale = 1.03 if on else 1.0
            tile.border = ft.Border.all(1, T.ACCENT if on else T.BORDER)
            tile.shadow = card_shadow(on)
            if quick:
                quick.opacity = 1 if on and not self.select_mode else 0
                circle.scale = 1.0 if on else 0.85
            tile.update()
        tile.on_hover = hover
        return ft.GestureDetector(content=tile, expand=True,
                                  on_secondary_tap_down=lambda e: self.open_menu(pkg, e.global_position))

    def open_menu(self, pkg: str, position=None) -> None:
        """Right-click on a card: that game's quick actions (built now, so they match the game's current state)."""
        self.menu.items = C.menu_items(self.app.game_actions(pkg))
        C.update(self.menu)
        self.app.page.run_task(self.menu.open, global_position=position)
