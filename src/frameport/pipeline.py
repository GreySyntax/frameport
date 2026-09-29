"""High-level operations used by both the CLI and the GUI:

    add (scan/import) → analyze → suggest recipe → [user confirms] → build → static checks
        → install on target → add to library → launch test (+ triage suggestions)
"""
from __future__ import annotations

from pathlib import Path

from . import build as builder
from .analysis.detect import analyze
from .artwork import fetch as artwork
from .core import library
from .core.events import Reporter
from .core.models import Recipe, SourceGame
from .core.paths import output_dir
from .recommend import engine
from .sources import quest_dump
from .targets.base import Target


def add_path(path: Path, reporter: Reporter | None = None) -> list[dict]:
    """Scan a file/folder, analyze every game found and store it with a suggested recipe."""
    added = []
    for src in quest_dump.scan(Path(path)):
        try:
            added.append(add_game(src, reporter))
        except Exception as exc:  # keep scanning; report the broken one
            if reporter:
                reporter.log(f"skipped {src.apk.name}: {exc}")
    return added


def add_game(src: SourceGame, reporter: Reporter | None = None) -> dict:
    if reporter:
        reporter.log(f"analyzing {src.apk.name}")
    a = analyze(src.apk, data_bytes=src.data_bytes())
    recipe = engine.suggest(a)
    return library.upsert_game(
        a.package, title=recipe.title or a.label, name=src.name, apk=str(src.apk),
        data_dir=str(src.data_dir) if src.data_dir else None, data_bytes=src.data_bytes(), origin=str(src.origin),
        analysis=a.to_dict(), recipe=library.recipe_to_dict(recipe), suggested=library.recipe_to_dict(recipe),
        status=recipe.status,
    )


def source_of(entry: dict) -> SourceGame:
    return SourceGame(entry.get("name") or entry["package"], Path(entry["apk"]),
                      Path(entry["data_dir"]) if entry.get("data_dir") else None,
                      Path(entry["origin"]) if entry.get("origin") else None)


def set_recipe(package: str, recipe: Recipe) -> None:
    library.upsert_game(package, recipe=library.recipe_to_dict(recipe))


def reset_recipe(package: str) -> Recipe:
    entry = library.game(package)
    recipe = engine.suggest(library.analysis_from_dict(entry["analysis"]))
    set_recipe(package, recipe)
    return recipe


def build_game(package: str, reporter: Reporter, outdir: Path | None = None) -> dict:
    entry = library.game(package)
    src = source_of(entry)
    a = library.analysis_from_dict(entry["analysis"])
    recipe = library.recipe_from_dict(entry["recipe"])
    out = outdir or (output_dir() / quest_dump.display_name(entry.get("name") or package))
    res = builder.build(src, a, recipe, out, reporter)
    art, store_title = artwork.fetch(package, res.apk)
    build_info = {"apk": str(res.apk), "alt_apk": str(res.alt_apk) if res.alt_apk else None, "sha256": res.sha256,
                  "alt_sha256": res.alt_sha256, "applied": res.applied, "checks": res.checks, "ok": res.ok,
                  "overport": res.meta.get("overport")}
    library.upsert_game(package, build=build_info, title=entry.get("title") or store_title)
    return build_info


def install_game(package: str, target: Target, reporter: Reporter, apk_only: bool = False,
                 add_to_library: bool = True) -> dict:
    entry = library.game(package)
    b = entry.get("build") or {}
    recipe = library.recipe_from_dict(entry["recipe"])
    apk = Path(b["alt_apk"] if recipe.use_alt and b.get("alt_apk") else b["apk"])
    data_dir = Path(entry["data_dir"]) if entry.get("data_dir") else None
    title = entry.get("title") or package
    result = target.install(package, title, apk, data_dir, recipe, reporter, apk_only)
    if add_to_library:
        target.add_to_library([package], reporter)
    installs = entry.get("installs", {})
    installs[target.label] = {"apk": str(apk), "result": result}
    library.upsert_game(package, installs=installs)
    return result


def test_game(package: str, target: Target, reporter: Reporter, seconds: int = 45) -> dict:
    result, _log = target.launch_test(package, reporter, seconds)
    summary = {"state": result.state, "verdict": result.verdict, "milestone": result.milestone, "fps": result.fps,
               "findings": [f.__dict__ for f in result.findings], "suggestions": result.suggestions()}
    library.upsert_game(package, last_test=summary)
    return summary


def apply_suggestions(package: str, suggestions: list[str]) -> Recipe:
    """Add triage-suggested patches to the game's recipe (the user confirms in the UI before rebuilding)."""
    entry = library.game(package)
    recipe = library.recipe_from_dict(entry["recipe"])
    for pid in suggestions:
        if pid.startswith("adapter."):
            from .patches.base import get

            recipe.patches[pid] = {"value": 1 if get(pid).params[0].kind == "int" else get(pid).params[0].default}
        else:
            recipe.patches.setdefault(pid, {})
        recipe.reasons[pid] = "Suggested by log triage."
        if pid == "patch_remove_unreal_force_quit":
            recipe.use_alt = True
            if pid not in recipe.alt_patches:
                recipe.alt_patches.append(pid)
            recipe.patches.pop(pid, None)
    set_recipe(package, recipe)
    return recipe
