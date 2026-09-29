"""FramePort command line. Same operations as the GUI (for automation and headless use).

    frameport tools install                      # portable Java + overport + apksigner
    frameport scan "<folder with game dumps>"    # analyze + suggest recipes
    frameport list | show <pkg> | patches
    frameport recipe <pkg> --enable frame.nodebug --set scale=1.2
    frameport build <pkg>|--all
    frameport frame discover | pair | info --frame steamos@frame.local
    frameport install <pkg> --frame steamos@frame.local [--apk-only]
    frameport test <pkg> --frame ...             # headless launch + triage
    frameport parity --known-good <PATCHED dir>  # rebuild everything and diff against known-good APKs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from . import pipeline
from .core import library
from .core.events import printing_reporter
from .patches import base

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Port Meta Quest games to the Steam Frame.")
tools_app = typer.Typer(help="Manage the portable toolchain.")
frame_app = typer.Typer(help="Find and connect to a Steam Frame.")
app.add_typer(tools_app, name="tools")
app.add_typer(frame_app, name="frame")


def _target(frame: Optional[str], password: Optional[str] = None):
    from .frame.connection import parse_target, saved_targets
    from .targets.frame_lepton import FrameLeptonTarget

    if frame:
        t = parse_target(frame)
    else:
        saved = saved_targets()
        if not saved:
            raise typer.BadParameter("no Frame given and none paired; use --frame steamos@<host>")
        t = saved[0]
    return FrameLeptonTarget(t, password).connect()


def _pkgs(package: Optional[str], all_: bool) -> list[str]:
    if all_:
        return [g["package"] for g in library.games()]
    if not package:
        raise typer.BadParameter("give a package or --all")
    matches = [g["package"] for g in library.games() if package.lower() in (g["package"] + " " + (g.get("title") or "")).lower()]
    if package in [g["package"] for g in library.games()]:
        return [package]
    if len(matches) != 1:
        raise typer.BadParameter(f"{package!r} matches {len(matches)} games: {matches[:5]}")
    return matches


# ------------------------------------------------------------------------------------------ tools
@tools_app.command("status")
def tools_status(check_latest: bool = typer.Option(False, "--latest", help="also look up the newest versions")):
    from .tools import toolchain

    for s in toolchain.status(check_latest):
        latest = f" (latest {s.latest})" if s.latest else ""
        typer.echo(f"{s.name:10} {'installed' if s.installed else 'missing':9} {s.version or '-'}{latest}  {s.path or ''}")


@tools_app.command("install")
def tools_install(update: bool = typer.Option(False, help="update to the newest versions")):
    from .tools import overport as ov
    from .tools import toolchain

    for s in toolchain.ensure_all(update=update):
        typer.echo(f"{s.name:10} {s.version}  {s.path}")
    from .patches import overport as op

    added = op.refresh(ov.list_patches)
    if added:
        typer.echo("new overport patches: " + ", ".join(added))


@tools_app.command("import-keys")
def tools_import_keys(dirs: list[Path]):
    """Import existing per-package signing keystores (keeps updates installable over existing installs)."""
    from .tools import overport as ov

    typer.echo(f"imported {ov.import_keystores(*dirs)} keystore(s) into {ov.workspace() / 'signatures'}")


# ------------------------------------------------------------------------------------------ library
@app.command()
def scan(path: Path):
    """Find games under PATH, analyze them and suggest patches."""
    rep = printing_reporter(verbose=False)
    for g in pipeline.add_path(path, rep):
        r = g["recipe"]
        typer.echo(f"{g['package']:40} {g.get('title', '')[:34]:34} {r['status']:11} {r['source']}")


@app.command("list")
def list_games():
    for g in library.games():
        a = g["analysis"]
        built = "built" if g.get("build", {}).get("ok") else ("build-failed" if g.get("build") else "")
        typer.echo(f"{g['package']:40} {(g.get('title') or '')[:30]:30} {a['engine']:6} {a['xr']:12} "
                   f"{g['recipe']['status']:11} {built}")


@app.command()
def show(package: str, as_json: bool = typer.Option(False, "--json"),
         all_: bool = typer.Option(False, "--all", help="also list patches that don't apply to this game")):
    [pkg] = _pkgs(package, False)
    g = library.game(pkg)
    if as_json:
        typer.echo(json.dumps(g, indent=1, default=str))
        return
    a, r = g["analysis"], g["recipe"]
    typer.echo(f"{g.get('title')}  ({pkg} {a['version']})\n  engine {a['engine']}, XR {a['xr']}, {a['graphics']}, "
               f"ABIs {', '.join(a['abis'])}, direct VrApi: {a['direct_vrapi']}")
    typer.echo(f"  status: {r['status']}  recipe source: {r['source']}  {r['notes']}")
    from .recommend.engine import visible_patches

    shown, hidden = visible_patches(library.analysis_from_dict(a), library.recipe_from_dict(r))
    for p in (base.all_patches() if all_ else shown):
        on = p.id in r["patches"]
        params = r["patches"].get(p.id) or {}
        typer.echo(f"  [{'x' if on else ' '}] {p.id:36} {p.title}{'  ' + json.dumps(params) if params else ''}"
                   f"{'  — ' + r['reasons'][p.id] if on and p.id in r.get('reasons', {}) else ''}")
    if hidden and not all_:
        typer.echo(f"  ({len(hidden)} patches hidden as not relevant for this game; --all to list them)")
    if r.get("alt_patches"):
        typer.echo(f"  alternate build adds: {', '.join(r['alt_patches'])}  (installed: {'alt' if r['use_alt'] else 'primary'})")


@app.command()
def patches():
    """List every available patch."""
    for p in base.all_patches():
        flag = " (experimental)" if p.experimental else ""
        typer.echo(f"{p.category:8} {p.id:36} {p.title}{flag}")


@app.command()
def recipe(package: str, enable: list[str] = typer.Option([], "--enable"), disable: list[str] = typer.Option([], "--disable"),
           set_: list[str] = typer.Option([], "--set", help="adapter setting key=value"),
           use_alt: Optional[bool] = typer.Option(None, "--use-alt/--no-alt"), reset: bool = False):
    """Change a game's patch selection."""
    [pkg] = _pkgs(package, False)
    r = pipeline.reset_recipe(pkg) if reset else library.recipe_from_dict(library.game(pkg)["recipe"])
    for pid in enable:
        base.get(pid)
        r.patches.setdefault(pid, {})
        r.reasons[pid] = "Enabled by user."
    for pid in disable:
        r.patches.pop(pid, None)
    for kv in set_:
        k, _, v = kv.partition("=")
        r.patches[f"adapter.{k}"] = {"value": float(v) if "." in v else int(v)}
    if use_alt is not None:
        r.use_alt = use_alt
    r.source = "user" if (enable or disable or set_ or use_alt is not None) else r.source
    pipeline.set_recipe(pkg, r)
    show(pkg)


# ------------------------------------------------------------------------------------------ build / install / test
@app.command()
def build(package: Optional[str] = typer.Argument(None), all_: bool = typer.Option(False, "--all"),
          outdir: Optional[Path] = None, verbose: bool = False):
    """Patch and sign (primary + alternate build when the recipe has one)."""
    failed = []
    for pkg in _pkgs(package, all_):
        rep = printing_reporter(verbose)
        try:
            info = pipeline.build_game(pkg, rep, outdir / pkg if outdir and all_ else outdir)
            typer.echo(f"{pkg}: {'OK' if info['ok'] else 'CHECKS FAILED'} -> {info['apk']}")
            if not info["ok"]:
                failed.append(pkg)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"{pkg}: FAILED {exc}")
            failed.append(pkg)
    raise typer.Exit(1 if failed else 0)


@app.command()
def install(package: Optional[str] = typer.Argument(None), all_: bool = typer.Option(False, "--all"),
            frame: Optional[str] = typer.Option(None, help="steamos@host"), password: Optional[str] = None,
            apk_only: bool = typer.Option(False, help="reuse game data already on the Frame"),
            no_library: bool = typer.Option(False, help="don't add to the Steam library now")):
    target = _target(frame, password)
    pkgs = _pkgs(package, all_)
    for pkg in pkgs:
        pipeline.install_game(pkg, target, printing_reporter(False), apk_only, add_to_library=False)
    if not no_library:
        target.add_to_library(pkgs, printing_reporter(False))


@app.command()
def test(package: Optional[str] = typer.Argument(None), all_: bool = typer.Option(False, "--all"),
         frame: Optional[str] = None, seconds: int = 45):
    """Headless launch on the Frame + log triage."""
    target = _target(frame)
    installed = {g["package"] for g in target.installed()}
    for pkg in _pkgs(package, all_):
        if pkg not in installed:
            typer.echo(f"{pkg}: not installed on {target.label}")
            continue
        s = pipeline.test_game(pkg, target, printing_reporter(False), seconds)
        typer.echo(f"{pkg}: {s['state']} ({s['verdict']}) furthest: {s['milestone']}"
                   + (f"; suggestions: {', '.join(s['suggestions'])}" if s["suggestions"] else ""))


@app.command()
def triage(logfile: Path, package: Optional[str] = None):
    """Classify a launch.log offline."""
    from .validate.triage import triage as run_triage

    r = run_triage(logfile.read_text(errors="replace"), "UNKNOWN", package)
    typer.echo(f"furthest milestone: {r.milestone}; fps {r.fps}")
    for f in r.findings:
        typer.echo(f"  {f.severity:7} {f.id}: {f.diagnosis}\n          {f.evidence[:200]}\n          suggest: {f.suggest}")


@app.command()
def settings(package: str, values: list[str], frame: Optional[str] = None):
    """Change FrameBridge settings of an installed game (no re-patching), e.g. scale=1.2."""
    target = _target(frame)
    kv = dict(v.split("=", 1) for v in values)
    typer.echo(json.dumps(target.set_settings(package, kv), indent=1))


# ------------------------------------------------------------------------------------------ frame
@frame_app.command("discover")
def frame_discover(seconds: float = 4.0):
    from .frame.discovery import browse

    for f in browse(seconds):
        typer.echo(f"{f.name:30} {f.host:16} {'FramePort' if f.is_frameport else 'ssh'}")


@frame_app.command("pair")
def frame_pair(timeout: float = 600):
    """Serve the one-line bootstrap for the Frame and wait until it reports back."""
    import time

    from .frame.connection import FrameTarget, save_target
    from .frame.pairing import PairingServer

    srv = PairingServer().start()
    typer.echo("On the Frame, open Desktop Mode → Konsole and run:\n\n    " + srv.one_liner + "\n")
    end = time.time() + timeout
    while time.time() < end and not srv.paired:
        time.sleep(1)
    srv.stop()
    if not srv.paired:
        typer.echo("timed out")
        raise typer.Exit(1)
    info = srv.paired[0]
    save_target(FrameTarget(info["host"], info["user"], 22, info["name"]))
    typer.echo(f"paired with {info['name']} at {info['host']}")


@frame_app.command("connect")
def frame_connect(address: str, password: Optional[str] = typer.Option(None, prompt=False)):
    """Connect with SSH (password once), install FramePort's key and remember the Frame."""
    from .frame.connection import Frame, parse_target, save_target

    t = parse_target(address)
    f = Frame(t, password).connect()
    f.install_key()
    info = f.agent("info")
    t.name = info["hostname"]
    save_target(t)
    typer.echo(json.dumps({k: info[k] for k in ("hostname", "os", "os_version", "lepton", "steam_users", "free_bytes")}, indent=1))


@frame_app.command("cleanup")
def frame_cleanup(frame: Optional[str] = None, keep_rollback: bool = typer.Option(False, help="keep previous-game.apk copies"),
                  path: list[str] = typer.Option([], help="extra folder under the Frame's home to delete, e.g. ~/PATCHED")):
    """Free space on the Frame: rollback APKs from reinstalls, leftover uploads, optional extra folders."""
    r = _target(frame).frame.agent("cleanup", rollback=not keep_rollback, paths=path)
    typer.echo(f"removed {len(r['removed'])} item(s), freed {r['freed_bytes'] / 2**30:.1f} GiB")


@frame_app.command("info")
def frame_info(frame: Optional[str] = None):
    typer.echo(json.dumps(_target(frame).describe(), indent=1, default=str))


# ------------------------------------------------------------------------------------------ parity
@app.command()
def parity(known_good: Path = typer.Option(..., help="folder of known-good game folders (PATCHED layout)"),
           sources: Path = typer.Option(..., help="folder with the original game dumps"),
           outdir: Path = typer.Option(Path("parity-out")), only: list[str] = typer.Option([]),
           report: Path = typer.Option(Path("parity-report.md")), keep: bool = False):
    """Rebuild every catalog game from its source dump and compare with the known-good APKs."""
    from .parity import run_parity

    ok = run_parity(known_good, sources, outdir, report, only, keep, printing_reporter(False))
    raise typer.Exit(0 if ok else 1)


@app.command()
def report(out: Path = typer.Option(Path("REPORT.md"))):
    """Write a status report of every catalog game (markdown)."""
    from .report import write

    typer.echo(f"wrote {write(out)}")


@app.command("parity-device")
def parity_device(results: Path = typer.Option(Path("parity-out/parity.json")), frame: Optional[str] = None,
                  baseline: Optional[Path] = typer.Option(None, help="baseline launch.txt from before the change"),
                  report: Path = typer.Option(Path("parity-device-report.md")), only: list[str] = typer.Option([]),
                  seconds: int = 45, test_only: bool = typer.Option(False, help="only re-run the launch tests")):
    """Install the APKs rebuilt by `parity` on the Frame (APK only) and compare headless launches with a baseline."""
    from .parity import install_and_test

    ok = install_and_test(results, _target(frame), baseline, report, printing_reporter(False), only, seconds, test_only)
    raise typer.Exit(0 if ok else 1)


def main():  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
