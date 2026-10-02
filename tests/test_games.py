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


def test_heuristics_reproduce_catalog(dumps):
    """Without the catalog, heuristics must reproduce the verified recipes (Phantom's use-the-no-ForceQuit-build is a
    runtime finding the triage makes, not a static one)."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("ev", Path(__file__).resolve().parents[1] / "scripts/eval_heuristics.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    exact = total = 0
    for g in dumps:
        a = analyze(g.apk, data_bytes=g.data_bytes())
        entry = catalog.lookup(a.package)
        if not entry:
            continue
        from frameport.recommend import engine

        want, got = ev.signature(engine.suggest(a)), ev.signature(engine.suggest(a, use_catalog=False))
        want["status_unsupported"] = got["status_unsupported"] = False
        total += 1
        exact += want == got
    assert exact >= total - 1, f"only {exact}/{total} catalog recipes reproduced by heuristics"
