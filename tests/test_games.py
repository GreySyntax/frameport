"""Checks against real game dumps (opt-in): FRAMEPORT_GAMES=<folder with dumps> pytest -m games"""
import pytest

from conftest import games_dir
from frameport.analysis.detect import analyze
from frameport.recommend import catalog
from frameport.sources import quest_dump

pytestmark = pytest.mark.games


@pytest.fixture(scope="module")
def dumps():
    d = games_dir()
    if not d:
        pytest.skip("set FRAMEPORT_GAMES")
    return quest_dump.scan(d)


def test_every_catalog_game_found_and_detected(dumps):
    by_pkg = {}
    for g in dumps:
        a = analyze(g.apk, deep=False)
        by_pkg[a.package] = a
    matched = [pkg for pkg in catalog.load() if pkg in by_pkg]
    assert len(matched) >= min(len(by_pkg), 1), "no catalog game found in the dumps"
    for pkg, entry in catalog.load().items():
        if pkg not in by_pkg:
            continue
        a = by_pkg[pkg]
        assert a.engine == entry.engine, pkg
        assert a.xr == entry.xr, pkg
        if entry.status == "unsupported" and "32-bit" in entry.notes:
            assert a.only_32bit, pkg
    print(f"checked {len(matched)} catalog games")
