"""FramePort command line. Same operations as the GUI (for automation and headless use).

    frameport tools install                      # portable Java + overport + apksigner
    frameport scan "<folder with game dumps>"    # analyze + suggest recipes
    frameport list | show <pkg> | patches
    frameport recipe <pkg> --enable frame.nodebug --set scale=1.2
    frameport build <pkg>|--all
    frameport frame discover | pair | info --frame steamos@frame.local
    frameport install <pkg> --frame steamos@frame.local [--apk-only]
    frameport test <pkg> --frame ...             # headless launch + triage
    frameport frame proton [--install]           # Proton on the Frame, for Oculus Rift (PC VR) games
    frameport install rift.<game> --to pc|frame  # Rift games: this PC (Revive + Steam) or the Frame (Proton)
    frameport pc info                            # Windows Steam / SteamVR / Revive on this PC
    frameport parity --known-good <PATCHED dir>  # rebuild everything and diff against known-good APKs
    frameport diag collect <pkg>|--all [--no-frame]  # redacted diagnostics zip (attach it to a GitHub issue)
    frameport diag inspect <zip>                 # re-triage a diagnostics zip (no game files or Frame needed)
    frameport diag report <pkg>                  # zip + prefilled GitHub problem report
    frameport share-recipe <pkg> --status works  # prefilled GitHub issue submitting a working recipe
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
pc_app = typer.Typer(help="This PC as a target for Oculus Rift (PC VR) games via Revive.")
diag_app = typer.Typer(help="Diagnostics bundles and problem reports.")
app.add_typer(tools_app, name="tools")
app.add_typer(frame_app, name="frame")
app.add_typer(pc_app, name="pc")
app.add_typer(diag_app, name="diag")


@app.callback()
def _setup():
    from .core import applog

    applog.setup("cli")


def _target(frame: Optional[str], password: Optional[str] = None, to: str = "frame"):
    if to == "pc":
        from .targets.pc_revive import PcReviveTarget

        return PcReviveTarget()
    if to != "frame":
        raise typer.BadParameter("--to must be 'frame' or 'pc'")
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
def tools_install(update: bool = typer.Option(False, help="update to the newest versions"),
                  revive: bool = typer.Option(False, help="also install Revive (for Oculus Rift games)")):
    from .tools import overport as ov
    from .tools import toolchain

    for s in toolchain.ensure_all(update=update, optional=revive):
        if s.optional and not s.installed:
            continue
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
    for p in (shown + hidden if all_ else shown):
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
           use_alt: Optional[bool] = typer.Option(None, "--use-alt/--no-alt"), reset: bool = False,
           as_is: Optional[bool] = typer.Option(None, "--as-is/--patch", help="install unchanged (already patched)"),
           exe: Optional[str] = typer.Option(None, help="Rift games: the program that starts the game (relative)")):
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
    if as_is is not None:
        r.as_is = as_is
        if library.game(pkg).get("kind") == "rift":
            r.patches.pop("pcvr.revive", None) if as_is else r.patches.setdefault("pcvr.revive", {})
    if exe:
        pipeline.set_exe(pkg, exe)
        r = library.recipe_from_dict(library.game(pkg)["recipe"])
    r.source = "user" if (enable or disable or set_ or use_alt is not None or as_is is not None) else r.source
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
            typer.echo(f"{pkg}: {'OK' if info['ok'] else 'CHECKS FAILED'} -> {info.get('apk') or 'ready (Rift game)'}")
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
            no_library: bool = typer.Option(False, help="don't add to the Steam library now"),
            to: str = typer.Option("frame", help="frame, or pc (Oculus Rift games only: run on this PC via Revive)")):
    target = _target(frame, password, to)
    pkgs = _pkgs(package, all_)
    for pkg in pkgs:
        pipeline.install_game(pkg, target, printing_reporter(False), apk_only, add_to_library=False)
    if not no_library:
        target.add_to_library(pkgs, printing_reporter(False))


@app.command()
def test(package: Optional[str] = typer.Argument(None), all_: bool = typer.Option(False, "--all"),
         frame: Optional[str] = None, seconds: int = 45,
         to: str = typer.Option("frame", help="frame, or pc (Rift games installed on this PC)")):
    """Headless launch on the Frame (or this PC) + log triage."""
    target = _target(frame, to=to)
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


@frame_app.command("send")
def frame_send(paths: list[Path] = typer.Argument(..., help="files or folders to send"),
               to: str = typer.Option("videos", help="destination: videos, downloads, documents, app, app-files"),
               game: Optional[str] = typer.Option(None, help="package of the game, for --to app / app-files"),
               folder: str = typer.Option("", help="sub-folder inside the destination"),
               app: Optional[str] = typer.Option(None, help="package of a player: also add the files to its own folder "
                                                            "(e.g. 4XVR lists /sdcard/4XPlayer, not Movies)"),
               frame: Optional[str] = None):
    """Send files to apps on the Frame. videos/downloads/documents are shared by every Quest game (they appear as
    /sdcard/Movies, /sdcard/Download, /sdcard/Documents); app/app-files are one game's own storage. Apps find the
    files by browsing folders (Android's media index doesn't work in Lepton)."""
    from .install import files

    r = files.send_files(_target(frame).frame, paths, to, game, folder, printing_reporter(), link_app=app)
    typer.echo(f"sent {r['files']} file(s) ({r['bytes'] / 2**20:.0f} MiB, {r['skipped']} already there); "
               f"in the app: {r['android']}")


@frame_app.command("storage")
def frame_storage(game: Optional[str] = None, frame: Optional[str] = None):
    """Where files for Lepton apps go on the Frame (and where the apps see them)."""
    from .install import files

    for t in files.storage_targets(_target(frame).frame, game):
        typer.echo(f"{t['id']:10} {t['android']:45} {t['path']}" + ("  (every app)" if t["shared"] else ""))


@frame_app.command("info")
def frame_info(frame: Optional[str] = None):
    typer.echo(json.dumps(_target(frame).describe(), indent=1, default=str))


@frame_app.command("controller-models")
def frame_controller_models(frame: Optional[str] = None,
                            convert: bool = typer.Option(False, help="also convert them (cached on the Frame)")):
    """SteamVR render models on the Frame and which ones serve as Steam Frame controller models (controller_models)."""
    typer.echo(json.dumps(_target(frame).frame.agent("controller_models", convert=convert), indent=1))


@frame_app.command("proton")
def frame_proton(frame: Optional[str] = None,
                 install_: bool = typer.Option(False, "--install", help="install it (Steam on the Frame restarts once "
                                               "and downloads it; waits until done)"),
                 in_headset: bool = typer.Option(False, help="with --install: only ask Steam (confirm in the headset)"),
                 test: bool = typer.Option(False, "--test", help="run a Windows program under Proton on the Frame"),
                 tool: Optional[str] = typer.Option(None, help="compat tool name, e.g. proton-experimental-arm64")):
    """Proton (ARM64) on the Frame, needed for Oculus Rift (PC VR) games."""
    t = _target(frame)
    if test:
        r = t.proton_selftest(tool)
        r.pop("log_tail", None)
        typer.echo(json.dumps(r, indent=1))
        raise typer.Exit(0 if r.get("ran") else 1)
    if install_ and in_headset:
        typer.echo(json.dumps(t.install_proton(False, tool), indent=1))
        return
    if install_:
        from .install.installer import ensure_proton

        ready = ensure_proton(t.frame, printing_reporter(False), tool)
        typer.echo(f"ready: {ready['display_name']}")
        return
    st = t.proton_status(tool)
    for p in st["tools"]:
        state = "installed" if p["installed"] and p["require_installed"] else \
            "needs runtime" if p["installed"] else "not installed"
        typer.echo(f"{p['name']:28} {p['display_name']:32} {state}")
    typer.echo(f"ready: {st['ready']['name'] if st.get('ready') else 'no (frameport frame proton --install)'}")
    typer.echo(f"OpenXR runtime: {(st.get('openxr') or {}).get('name') or 'none'}")


# ------------------------------------------------------------------------------------------ pc (Revive)
@pc_app.command("info")
def pc_info():
    typer.echo(json.dumps(_target(None, to="pc").describe(), indent=1, default=str))


@pc_app.command("install-revive")
def pc_install_revive():
    """Download Revive's latest release and unpack it into FramePort's tools (no installer, no admin rights)."""
    from .tools import revive

    path = revive.install(lambda f: None)
    typer.echo(f"Revive {revive.installed_version()} at {path}")


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


@app.command("uninstall-app")
def uninstall_app(frame: bool = typer.Option(False, help="also remove FramePort's games and files from the Frame"),
                  keep_frame_saves: bool = typer.Option(True, help="with --frame: keep game saves on the Frame"),
                  backup_keys: Optional[Path] = typer.Option(None, help="folder for a zip of the signing keys "
                                                                          "(default: your Documents folder)"),
                  backup: bool = typer.Option(True, "--backup/--no-backup", help="back up the signing keys first"),
                  yes: bool = typer.Option(False, "--yes", "-y", help="don't ask for confirmation")):
    """Remove everything FramePort created on this PC (and optionally on the Frame). Then delete the app itself."""
    from . import uninstall as un

    target = _target(None) if frame else None
    info = target.describe() if target else None
    pl = un.plan(info)
    typer.echo("This removes:")
    for it in pl.items:
        typer.echo(f"  - {it.what}" + (f"  ({it.path})" if it.path else "") +
                   (f"  {it.size / 2**30:.1f} GiB" if it.size > 2**28 else ""))
    dest = (backup_keys or un.default_backup_dir()) if backup else None
    typer.echo(f"Signing keys: {len(pl.keys)} " + ("(not backed up!)" if dest is None else f"→ backup zip in {dest}"))
    if not yes and not typer.confirm("Uninstall FramePort?", default=False):
        raise typer.Exit(1)
    out = un.run(printing_reporter(False), target.frame if target else None, keep_frame_saves, dest, frame)
    typer.echo("Done." + (f" Keys backup: {out['backup']}" if out.get("backup") else "") +
               " Delete the FramePort program folder to finish (or `uv tool uninstall frameport`).")


# ------------------------------------------------------------------------------------------ diagnostics / sharing
def _diag_target(no_frame: bool, frame: Optional[str], to: str):
    if no_frame:
        return None
    try:
        return _target(frame, to=to)
    except Exception as exc:  # noqa: BLE001  (offline Frame: PC-side data only)
        typer.echo(f"({to} not reachable: {exc}; collecting the PC side only)")
        return None


def _open(url: str, browser: bool) -> None:
    from .core import winhost

    typer.echo(url)
    if browser and not winhost.open_url(url):
        typer.echo("(couldn't open a browser: open the link above)")


@diag_app.command("collect")
def diag_collect(package: Optional[str] = typer.Argument(None, help="a game (omit for app-wide logs only)"),
                 all_: bool = typer.Option(False, "--all"), frame: Optional[str] = None,
                 no_frame: bool = typer.Option(False, "--no-frame", help="don't contact the Frame"),
                 to: str = typer.Option("frame", help="where the game is installed: frame or pc"),
                 out: Optional[Path] = typer.Option(None, help="folder or .zip path (default: Documents)")):
    """Write a redacted diagnostics zip: logs, recipe, analysis, device info (no game files, no personal data)."""
    pkgs = _pkgs(package, all_) if (package or all_) else []
    path = pipeline.collect_diagnostics(pkgs, _diag_target(no_frame, frame, to), printing_reporter(False), out)
    typer.echo(f"wrote {path}")


@diag_app.command("inspect")
def diag_inspect(bundle: Path, as_json: bool = typer.Option(False, "--json")):
    """Summarize a diagnostics zip and re-triage its launch logs with this version's signatures."""
    from .diag import bundle as b

    res = b.read(bundle)
    if as_json:
        typer.echo(json.dumps(res, indent=1, default=str))
        return
    m = res["manifest"]
    env = m.get("env", {})
    typer.echo(f"created {m.get('created')} by FramePort {env.get('app')}; {env.get('os')}; "
               f"Frame: {(env.get('frame') or {}).get('build_id') or '-'}")
    for w in m.get("warnings", []):
        typer.echo(f"  warning: {w}")
    for pkg, g in res["games"].items():
        typer.echo(f"\n{pkg} — {g.get('title')} [{g.get('status')}]")
        last = g.get("last_test") or {}
        if last:
            typer.echo(f"  last test: {last.get('state')} ({last.get('verdict')}) furthest: {last.get('milestone')}")
        t = g.get("triage")
        if t:
            typer.echo(f"  re-triage of {g['log']}: {t['verdict']}, furthest: {t['milestone']}")
            for f in t["findings"]:
                typer.echo(f"    {f['severity']:7} {f['id']}: {f['diagnosis']}\n            {f['evidence'][:200]}")
            if t["suggestions"]:
                typer.echo(f"  suggested patches: {', '.join(t['suggestions'])}")


@diag_app.command("report")
def diag_report(package: Optional[str] = typer.Argument(None), description: str = typer.Option("", "--text"),
                frame: Optional[str] = None, no_frame: bool = typer.Option(False, "--no-frame"),
                to: str = typer.Option("frame"), out: Optional[Path] = None,
                browser: bool = typer.Option(True, "--browser/--no-browser")):
    """Collect a diagnostics zip and open a prefilled GitHub problem report (attach the zip there)."""
    from .core import winhost

    pkgs = _pkgs(package, False) if package else []
    target = _diag_target(no_frame, frame, to)
    info = None
    if target is not None:
        try:
            info = target.describe()
        except Exception:  # noqa: BLE001
            target = None
    path = pipeline.collect_diagnostics(pkgs, target, printing_reporter(False), out)
    typer.echo(f"wrote {path} — attach it to the issue")
    if browser:
        winhost.open_folder(path, select=True)
    _open(pipeline.problem_report(pkgs[0] if pkgs else None, description, path, info), browser)


@app.command("share-recipe")
def share_recipe(package: str, status: str = typer.Option("works", help="works or issues"),
                 notes: str = typer.Option("", help="what you checked in the headset, known issues"),
                 frame: Optional[str] = typer.Option(None, help="include the Frame's SteamOS build"),
                 browser: bool = typer.Option(True, "--browser/--no-browser")):
    """Submit a working configuration: saves it as a known-good recipe and opens a prefilled GitHub issue."""
    if status not in ("works", "issues"):
        raise typer.BadParameter("--status must be works or issues")
    pkg = _pkgs(package, False)[0]
    info = None
    if frame:
        info = _target(frame).describe()
    _open(pipeline.share_working_config(pkg, status, notes, info), browser)


def main():  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
