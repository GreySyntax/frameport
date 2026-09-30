"""Picking artwork in the GUI (artwork.sources.apply_choice): never leaves a game without art, keeps screenshots."""
import pytest

from frameport.artwork import fetch, sources

JPEG = b"\xff\xd8\xff" + b"x" * 400


@pytest.fixture
def art(tmp_path, monkeypatch):
    monkeypatch.setenv("FRAMEPORT_HOME", str(tmp_path))
    d = fetch.artwork_dir("com.x")
    for name in ("portrait.jpg", "t_portrait_400_aaaa.jpg", "shot_0.jpg", "t_shot_0_480_bbbb.jpg", "title.txt"):
        (d / name).write_bytes(b"old")
    return d


def test_failed_pick_keeps_the_current_art(art, monkeypatch):
    monkeypatch.setattr(sources, "apply_oculusdb", lambda pkg, app: False)  # dead store image
    monkeypatch.setattr(fetch, "fetch", lambda pkg, **kw: (fetch.artwork_dir(pkg), None))  # no art for the package
    assert sources.apply_choice("com.x", {"source": "Meta (Quest)", "package": "com.other", "app": {}}) is False
    assert (art / "portrait.jpg").read_bytes() == b"old"
    assert not any(p.name.startswith(".pick") for p in art.parent.iterdir())


def test_pick_replaces_art_but_keeps_screenshots_and_title(art, monkeypatch):
    monkeypatch.setattr(sources, "apply_oculusdb", lambda pkg, app: (sources._save(pkg, "square", JPEG), True)[1])
    assert sources.apply_choice("com.x", {"source": "Oculus Rift", "app": {"id": "1"}}) is True
    names = {p.name for p in art.iterdir() if p.is_file()}
    assert names == {"square.jpg", "shot_0.jpg", "t_shot_0_480_bbbb.jpg", "title.txt"}
    assert not any(p.name.startswith(".pick") for p in art.parent.iterdir())
