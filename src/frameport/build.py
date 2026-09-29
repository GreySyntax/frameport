"""Build stage: overport → Frame fixes (apk-stage patches) → sign → static validation."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .apk import sign
from .apk.workspace import ApkWorkspace
from .core.events import Reporter
from .core.models import Analysis, BuildResult, Recipe, SourceGame
from .core.paths import work_dir
from .patches import base
from .patches.overport import OVERPORT_PATCHES
from .tools import overport as overport_tool
from .validate.static import check_apk


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def overport_ids(recipe: Recipe, alt: bool = False) -> list[str]:
    """overport defaults (overport's order) first, then extras in recipe order, then the alt-build extras."""
    chosen = [pid for pid in recipe.patches if base.get(pid).category == "overport"]
    order = [pid for pid, *_ in OVERPORT_PATCHES]
    defaults = [pid for pid in order if pid in chosen and base.get(pid).default_on]
    extras = [pid for pid in chosen if pid not in defaults]
    ids = defaults + extras
    if alt:
        ids += [pid for pid in recipe.alt_patches if pid not in ids]
    return ids


def apk_patches(recipe: Recipe) -> list[base.Patch]:
    patches = [base.get(pid) for pid in recipe.patches if base.get(pid).stage == "apk"]
    return sorted(patches, key=lambda p: p.order)


def apply_frame_fixes(apk_in: Path, apk_out_unsigned: Path, analysis: Analysis, recipe: Recipe,
                      reporter: Reporter) -> tuple[list[str], list[dict]]:
    applied, checks = [], []
    with ApkWorkspace(apk_in) as ws:
        for patch in apk_patches(recipe):
            reporter.check_cancel()
            ctx = base.ApkContext(ws, analysis, recipe.params(patch.id), reporter, recipe.patches)
            if patch.apply(ctx):
                applied.append(patch.id)
                reporter.log(f"applied {patch.id}" + (f" ({'; '.join(ctx.notes)})" if ctx.notes else ""))
            for name, ok, detail in patch.validate(ctx):
                checks.append({"name": name, "ok": ok, "detail": detail})
        ws.write(apk_out_unsigned)
    return applied, checks


def build(source: SourceGame, analysis: Analysis, recipe: Recipe, outdir: Path, reporter: Reporter,
          keep_work: bool = False) -> BuildResult:
    pkg = analysis.package
    work = work_dir() / pkg
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    outdir.mkdir(parents=True, exist_ok=True)
    variants = [("primary", False)] + ([("alt", True)] if recipe.alt_patches else [])
    results = {}
    all_checks, applied = [], []
    try:
        for variant, alt in variants:
            reporter.stage(f"overport ({variant})")
            ids = overport_ids(recipe, alt)
            patched = overport_tool.patch(source.apk, work, f"{pkg}.{variant}.overport.apk", ids, reporter)
            reporter.stage(f"Frame fixes ({variant})")
            unsigned = work / f"{pkg}.{variant}.unsigned.apk"
            applied, checks = apply_frame_fixes(patched, unsigned, analysis, recipe, reporter)
            patched.unlink(missing_ok=True)
            reporter.stage(f"sign ({variant})")
            final = outdir / (f"{pkg}.apk" if not alt else f"{pkg}.alt-noforcequit.apk")
            sign.sign(unsigned, final, pkg)
            unsigned.unlink(missing_ok=True)
            reporter.stage(f"validate ({variant})")
            checks += check_apk(final, pkg)
            for c in checks:
                reporter.check(c["name"], c["ok"], c["detail"])
            results[variant] = final
            all_checks += [{**c, "variant": variant} for c in checks]
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    primary, alt_apk = results["primary"], results.get("alt")
    return BuildResult(pkg, primary, alt_apk, sha256(primary), sha256(alt_apk) if alt_apk else None, applied,
                       all_checks, {"overport": overport_ids(recipe), "alt_overport": overport_ids(recipe, True)
                                    if alt_apk else None})
