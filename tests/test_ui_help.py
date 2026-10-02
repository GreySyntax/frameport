"""Help hints and the game action menus (library right-click, game page "…")."""
import re
from pathlib import Path
from types import SimpleNamespace

from frameport.ui import components as C
from frameport.ui.help import HELP

UI = Path(__file__).resolve().parents[1] / "src" / "frameport" / "ui"


def test_every_help_key_used_in_the_ui_exists():
    used = set()
    for f in UI.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        used |= set(re.findall(r'help="(\w+)"', src))
        used |= set(re.findall(r'HELP\["(\w+)"\]', src))
        used |= set(re.findall(r'help_icon\("(\w+)"\)', src))
        used |= set(re.findall(r'with_help\([^\n]*?, "(\w+)"\)', src))
    from frameport.ui.views.game import CATEGORY_TITLES

    used |= {f"cat_{c}" for c in CATEGORY_TITLES}
    assert used, "the pattern scan found nothing"
    assert used <= set(HELP), sorted(used - set(HELP))


def test_help_icon_accepts_a_key_or_text():
    assert C.help_icon("lepton").tooltip.message == HELP["lepton"]
    assert C.help_icon("Some text").tooltip.message == "Some text"
    plain = C.meta("x")
    assert C.with_help(plain, None) is plain


def test_menu_items_dividers():
    act = ("A", None, None)
    items = C.menu_items([None, act, None, None, act, None])
    assert [i.content is not None for i in items] == [True, False, True]


def _app(monkeypatch, game, frame_info=None, busy=None, pc=()):
    from frameport.core import library
    from frameport.ui.app import FramePortApp

    monkeypatch.setattr(library, "game", lambda p: game if p == game["package"] else None)
    app = object.__new__(FramePortApp)
    app.frame_state = "connected" if frame_info is not None else "none"
    app.frame_info = frame_info
    app.jobs = SimpleNamespace(busy_with=lambda p: busy)
    app.library_view = None
    app._pc_cache = (float("inf"), {p: {} for p in pc})
    return app


def _labels(actions):
    return [a[0] if a else "—" for a in actions]


def test_right_click_menu_follows_install_state(monkeypatch):
    g = {"package": "com.q", "title": "Q", "recipe": {"status": "works"}, "build": {"sha256": "new"}}
    app = _app(monkeypatch, g, {"installed": [{"package": "com.q", "sha256": "old"}]})
    installed = _labels(app.game_actions("com.q"))
    assert installed[:3] == ["Open", "Play on Frame", "Update on Frame"]
    expected = {"Launch test on Frame", "Game settings…", "Uninstall from Frame", "Remove from library"}
    assert expected <= set(installed)

    missing = _labels(_app(monkeypatch, g, {"installed": []}).game_actions("com.q"))
    assert "Install on Frame" in missing and "Uninstall from Frame" not in missing and "Play on Frame" not in missing
    assert "Game settings…" in missing  # saved to the recipe, used when it's installed

    flat = {**g, "analysis": {"extra": {"vr_kind": "none"}}}  # a 2D Android app has no adapter
    assert "Game settings…" not in _labels(_app(monkeypatch, flat, {"installed": []}).game_actions("com.q"))

    offline = _labels(_app(monkeypatch, g).game_actions("com.q"))
    assert "Connect your Frame" in offline and "Launch test on Frame" not in offline


def test_right_click_menu_while_busy_and_on_game_page(monkeypatch):
    g = {"package": "rift.r", "kind": "rift", "title": "R", "recipe": {"status": "unknown"}}
    busy = _labels(_app(monkeypatch, g, {"installed": []}, busy=object()).game_actions("rift.r"))
    assert "Cancel" in busy and "Install on Frame" not in busy and "Check game files" not in busy
    page = _labels(_app(monkeypatch, g, {"installed": []}).game_actions("rift.r", quick=False))
    assert page[0] == "Change executable…" and "Open" not in page and "Install on Frame" not in page


def test_play_is_the_quick_action_when_installed(monkeypatch):
    g = {"package": "com.q", "title": "Q", "recipe": {"status": "works"}, "build": {"sha256": "x"}}
    app = _app(monkeypatch, g, {"installed": [{"package": "com.q", "sha256": "x"}]})
    assert app.quick_action(g) == ("Play on Frame", "play")
    assert _app(monkeypatch, g, {"installed": []}).quick_action(g) == ("Install on Frame", "install")
    old = _app(monkeypatch, g, {"installed": [{"package": "com.q", "sha256": "older"}]})
    old.pc_installs = lambda: {}
    assert old.quick_action(g) == ("Play on Frame", "play")  # installed (even outdated): playing comes first
    assert _app(monkeypatch, g).quick_action(g) == (None, None)  # no Frame: "Connect" isn't a quick action
    r = {"package": "rift.r", "kind": "rift", "title": "R", "recipe": {}}
    app = _app(monkeypatch, r, {"installed": []}, pc=("rift.r",))
    assert [o[0] for o in app.play_options(r)] == ["Play on this PC"]


def test_menus_offer_sharing_and_diagnostics(monkeypatch):
    g = {"package": "com.q", "title": "Q", "recipe": {"status": "works"}, "build": {"sha256": "x"}}
    for quick in (True, False):
        labels = _labels(_app(monkeypatch, g, {"installed": []}).game_actions("com.q", quick=quick))
        assert {"Share working config…", "Collect logs", "Report a problem…"} <= set(labels)
        assert labels[-1] == "Remove from library"


def test_install_browser_explains_known_folders():
    from frameport.ui.views.files_dialog import folder_note

    assert "shader cache" in folder_note("lepton-shaders") and folder_note("lepton-data")
    assert folder_note("lepton-app/obb") == ""  # only top-level entries


def test_untested_games_invite_sharing_their_recipe():
    from frameport.ui.views.game import should_ask_to_share

    g = {"package": "com.x", "recipe": {"status": "unknown", "source": "heuristics"}}
    assert not should_ask_to_share(g, False)  # never installed, tested or played
    assert should_ask_to_share(g, True)
    assert should_ask_to_share({**g, "last_test": {"verdict": "pass"}}, False)
    assert not should_ask_to_share({**g, "recipe": {"status": "unknown", "source": "catalog (bundled)"}}, True)
    assert not should_ask_to_share({**g, "recipe": {"status": "works", "source": "user"}}, True)
    assert not should_ask_to_share({**g, "shared_config": 1.0}, True)
    assert not should_ask_to_share({**g, "share_dismissed": True}, True)
    assert not should_ask_to_share({**g, "kind": "rift"}, True)


def test_patch_reasons_in_plain_words():
    from frameport.ui.views.game import plain_reason

    assert plain_reason("overport default.") == "Standard for every game"  # an older recipe's wording
    assert plain_reason("Known-good recipe for Batman (tested 2026-09-28).") == "From the tested recipe for this game"
    assert plain_reason("Enabled by you.") == "Turned on by you"
    assert plain_reason("Applied automatically if the manifest needs it.") == "Added automatically when needed"
    assert plain_reason("Hand tracking is required by the game: …") == "Suggested for this game"
