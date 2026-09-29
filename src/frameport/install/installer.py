"""Install a built game on a Steam Frame over SSH (via the FramePort agent)."""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

from ..artwork import fetch as artwork
from ..build import sha256
from ..core.events import Reporter
from ..core.models import Recipe
from ..frame.connection import Frame
from ..patches import base
from ..patches.settings import adapter_settings


@dataclass
class InstallPlan:
    package: str
    title: str
    apk: Path
    data_dir: Path | None
    recipe: Recipe
    apk_only: bool = False  # reuse the data already on the Frame
    dest: str | None = None


def install_context(recipe: Recipe) -> base.InstallContext:
    ctx = base.InstallContext(recipe.package, {}, {}, {}, adapter_settings(recipe.patches))
    for pid, params in recipe.patches.items():
        patch = base.get(pid)
        if patch.stage == "install" and patch.category == "device":
            ctx.params = params or {}
            patch.install(ctx)
    return ctx


def local_data_manifest(data_dir: Path | None) -> dict[str, int]:
    if not data_dir or not data_dir.is_dir():
        return {}
    return {p.relative_to(data_dir).as_posix(): p.stat().st_size for p in sorted(data_dir.rglob("*")) if p.is_file()}


def install(frame: Frame, plan: InstallPlan, reporter: Reporter) -> dict:
    reporter.stage("Prepare Frame")
    apk_sha = sha256(plan.apk)
    prep = frame.agent("prepare", package=plan.package, title=plan.title, dest=plan.dest,
                       apk_sha256=apk_sha, apk_size=plan.apk.stat().st_size)
    if not prep.get("lepton"):
        raise RuntimeError("Lepton is not installed on the Frame (Setup → Install Lepton)")
    incoming = prep["incoming"]
    manifest = {} if plan.apk_only else local_data_manifest(plan.data_dir)
    existing = prep["existing_obb"]
    to_send = [rel for rel, size in manifest.items() if existing.get(rel) != size]
    need = sum(manifest[r] for r in to_send) + (0 if prep["same_apk"] else plan.apk.stat().st_size)
    if need > prep["free_bytes"] - (1 << 30):
        raise RuntimeError(f"not enough space on the Frame: need {need / 2**30:.1f} GiB + 1 GiB headroom, "
                           f"have {prep['free_bytes'] / 2**30:.1f} GiB")
    total = max(need, 1)
    sent = 0

    def progress_for(label):
        def cb(done, size):
            reporter.progress((sent + done) / total, f"{label} {done / 2**20:.0f}/{size / 2**20:.0f} MiB")
        return cb

    reporter.stage("Upload APK")
    if prep["same_apk"]:
        reporter.log("identical APK already installed; not re-sending")
    else:
        frame.put(plan.apk, posixpath.join(incoming, "game.apk"), progress_for("APK"))
        sent += plan.apk.stat().st_size
    if to_send:
        reporter.stage(f"Upload data ({len(to_send)} files)")
        for rel in to_send:
            reporter.check_cancel()
            frame.put(plan.data_dir / rel, posixpath.join(incoming, "obb", rel), progress_for(rel))
            sent += manifest[rel]
    elif manifest:
        reporter.log("game data already on the Frame; not re-sending")

    reporter.stage("Artwork")
    art_dir, _ = artwork.fetch(plan.package, plan.apk)
    remote_art = posixpath.join(prep["base"], "incoming-artwork")
    frame.run(f"rm -rf '{remote_art}' && mkdir -p '{remote_art}'")
    for f in artwork.files(plan.package):
        frame.put(f, posixpath.join(remote_art, f.name), resume=False)

    reporter.stage("Finalize install")
    ctx = install_context(plan.recipe)
    result = frame.agent(
        "finalize", package=plan.package, title=plan.title, dest=plan.dest, apk_sha256=apk_sha,
        apk_name=plan.apk.name, settings=ctx.adapter_settings,
        files={k: v.decode() if isinstance(v, bytes) else v for k, v in ctx.files.items()}, env=ctx.env,
        obb_manifest=manifest or None,
        recipe={"patches": sorted(plan.recipe.patches), "source": plan.recipe.source, "alt": plan.recipe.use_alt},
    )
    reporter.log(f"installed at {result['base']} (Steam shortcut id {result['appid']})")
    return result


def add_to_steam(frame: Frame, packages: list[str], reporter: Reporter, wait: float = 120) -> dict:
    """Add library entries + artwork for installed games; Steam restarts once (a few seconds)."""
    import time

    reporter.stage("Add to Steam library")
    frame.agent("shortcuts", packages=packages, restart=True)
    end = time.time() + wait
    status = {}
    while time.time() < end:
        time.sleep(3)
        try:
            status = frame.agent("shortcut_status", timeout=30)
        except Exception:  # the SSH session can hiccup while Steam restarts
            continue
        if status.get("state") in ("done", "failed"):
            break
    for a in status.get("added", []):
        reporter.check(f"Steam library: {a['package']}", True, f"shortcut {a['appid']}")
    for e in status.get("errors", []):
        reporter.check("Steam library", False, e)
    return status
