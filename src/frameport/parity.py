"""Parity test: rebuild every catalog game from its original dump and compare with known-good APKs.

Each differing zip entry is classified:
  identical     byte-identical
  expected      a FramePort-owned binary was updated (adapter / VrApi bridge / GL shim) — lists old vs new
  equivalent    same meaning, different bytes: generated stub library with identical exports; ELF files whose only
                change is the same DT_NEEDED additions (our writer vs patchelf); settings with identical values
  UNEXPLAINED   anything else (fails the run)
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

from .analysis import elf
from .analysis.detect import analyze
from .apk import sign
from .build import build
from .core.events import Reporter
from .core.paths import artifacts_dir
from .recommend import catalog, engine
from .sources import quest_dump

OWNED = {"libopenxr_loader_generic.so": "FrameBridge adapter", "libvrapi.so": "VrApi bridge", "libglshim.so": "GL shim",
         "libovrplatformcompat.so": "platform compat", "libfp_vk.so": "Vulkan shim"}


def _default_rewrites(base: str, old: bytes) -> list[tuple[bytes, str]]:
    """In-place rewrites that default-on Frame patches make to a game's own libraries (newer than the known-good
    builds): the result of applying them to the known-good bytes."""
    from .patches.frame import swapchain_limit, vk_sanitize

    out = []
    if base == swapchain_limit.DISPATCHER:
        fixed, n = swapchain_limit.raise_swapchain_limit(old)
        if fixed:
            out.append((fixed, f"frame.swapchain_limit: swapchain size guard raised ({n} checks)"))
    if base in vk_sanitize.ENGINE_LIBS:
        fixed, n = elf.replace_rodata_string(old, vk_sanitize.VULKAN, vk_sanitize.SHIM)
        if n:
            out.append((fixed, f"frame.vk_sanitize: Vulkan loaded through {vk_sanitize.SHIM}"))
    return out


def _entries(apk: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(apk) as z:
        return {i.filename: hashlib.sha256(z.read(i)).digest() for i in z.infolist()
                if not i.filename.startswith("META-INF/") and not i.is_dir()}


def _read(apk: Path, name: str) -> bytes:
    with zipfile.ZipFile(apk) as z:
        return z.read(name)


def _text_bytes(data: bytes) -> bytes:
    from elftools.elf.elffile import ELFFile

    e = ELFFile(io.BytesIO(data))
    return b"".join(s.data() for s in e.iter_sections() if s["sh_flags"] & 0x4 and s["sh_type"] == "SHT_PROGBITS")


def classify(name: str, new: bytes, old: bytes) -> tuple[str, str]:
    base = name.rsplit("/", 1)[-1]
    if base in OWNED:
        current = (artifacts_dir() / name.split("/")[1] / base) if name.startswith("lib/") else None
        is_current = current is not None and current.exists() and current.read_bytes() == new
        return ("expected", f"{OWNED[base]} updated to the current build") if is_current else \
               ("UNEXPLAINED", f"{OWNED[base]} differs but is not the current artifact")
    for fixed, why in _default_rewrites(base, old):
        if fixed == new:
            return "expected", why
    if base == "libframe_settings.so":
        def kv(b):
            return dict(l.split("=", 1) for l in b.decode().split("\n") if "=" in l)
        a, b = kv(new), kv(old)
        if a == b:
            return "equivalent", "same settings, different order"
        return "expected", f"settings now {a} (known-good lib had {b}; installs also write settings.conf)"
    if base == "libovrstubs.so" and elf.is_elf(new) and elf.is_elf(old):
        if elf.dyn_symbols(new, True) == elf.dyn_symbols(old, True):
            return "equivalent", f"generated stub library, same {len(elf.dyn_symbols(new, True))} exports"
        return "UNEXPLAINED", "stub exports differ"
    if elf.is_elf(new) and elf.is_elf(old):
        if (elf.needed(new) == elf.needed(old) and elf.dyn_symbols(new, True) == elf.dyn_symbols(old, True)
                and elf.dyn_symbols(new, False) == elf.dyn_symbols(old, False) and _text_bytes(new) == _text_bytes(old)):
            return "equivalent", f"same code, symbols and NEEDED ({', '.join(elf.needed(new)[:2])}, …); different layout"
        return "UNEXPLAINED", f"ELF differs (NEEDED new {elf.needed(new)[:3]} vs old {elf.needed(old)[:3]})"
    return "UNEXPLAINED", "content differs"


def _redundant_stubs(new_apk: Path, old_apk: Path, name: str) -> bool:
    """Known-good builds made before libovrplatformcompat existed stubbed ovrMessageType_ToString, which then
    shadowed compat's real implementation. New builds only add stubs for symbols nothing else provides."""
    lib = name.rsplit("/", 1)[0] + "/libovrplatformcompat.so"
    try:
        old_stubs = elf.dyn_symbols(_read(old_apk, name), True)
        compat = elf.dyn_symbols(_read(new_apk, lib), True)
    except KeyError:
        return False
    return bool(old_stubs) and old_stubs <= compat


def compare(new_apk: Path, old_apk: Path) -> dict:
    a, b = _entries(new_apk), _entries(old_apk)
    rows = []
    stub_names = [n for n in b if n.endswith("/libovrstubs.so") and n not in a and _redundant_stubs(new_apk, old_apk, n)]
    for n in stub_names:
        rows.append((n, "expected", "known-good stub only shadowed libovrplatformcompat's real ovrMessageType_ToString; not needed"))
        loader = n.rsplit("/", 1)[0] + "/libovrplatformloader.so"
        if loader in a and a[loader] != b.get(loader):
            new_l, old_l = _read(new_apk, loader), _read(old_apk, loader)
            if [x for x in elf.needed(old_l) if x != "libovrstubs.so"] == elf.needed(new_l) and _text_bytes(new_l) == _text_bytes(old_l):
                rows.append((loader, "expected", "same, minus the redundant libovrstubs.so dependency"))
                a[loader] = b[loader]
        b.pop(n)
    for n in sorted(set(a) | set(b)):
        if n not in b:
            base = n.rsplit("/", 1)[-1]
            current = artifacts_dir() / n.split("/")[1] / base if n.startswith("lib/") and base in OWNED else None
            if current is not None and current.exists() and current.read_bytes() == _read(new_apk, n):
                rows.append((n, "expected", f"{OWNED[base]} added (current build)"))
            else:
                rows.append((n, "UNEXPLAINED", "only in the new build"))
        elif n not in a:
            rows.append((n, "UNEXPLAINED", "missing from the new build"))
        elif a[n] != b[n]:
            kind, why = classify(n, _read(new_apk, n), _read(old_apk, n))
            rows.append((n, kind, why))
    same_cert = sign.cert_digest(new_apk) == sign.cert_digest(old_apk)
    if not same_cert:
        rows.append(("signing certificate", "UNEXPLAINED", "different signing key"))
    worst = "UNEXPLAINED" if any(r[1] == "UNEXPLAINED" for r in rows) else \
        "expected" if any(r[1] == "expected" for r in rows) else "equivalent" if rows else "identical"
    return {"entries": len(a), "diffs": rows, "verdict": worst, "same_cert": same_cert}


def find_source(sources: Path, hint: str, package: str) -> quest_dump.SourceGame | None:
    from .recommend.catalog import source_hint_matches

    for d in sorted(sources.iterdir()):  # by name (loosely: releases name their folders differently)
        if d.is_dir() and hint and source_hint_matches(hint, d.name):
            g = quest_dump.from_path(d)
            if g and quest_dump._package_of(g.apk) == package:
                return g
    for d in sorted(sources.iterdir()):  # slow path: look inside
        g = quest_dump.from_path(d) if d.is_dir() else None
        if g and quest_dump._package_of(g.apk) == package:
            return g
    return None


def run_parity(known_good: Path, sources: Path, outdir: Path, report: Path, only: list[str], keep: bool,
               reporter: Reporter) -> bool:
    results = []
    previous = outdir / "parity.json"
    if only and previous.exists():  # partial re-run: keep the other games' earlier results
        results = [r for r in json.loads(previous.read_text())
                   if not any(o.lower() in r["game"].lower() for o in only)]
    folders = sorted(p for p in known_good.iterdir() if (p / "game.json").exists())
    if only:
        folders = [f for f in folders if any(o.lower() in f.name.lower() for o in only)]
    for folder in folders:
        g = json.loads((folder / "game.json").read_text())
        pkg = g["package"]
        entry = catalog.lookup(pkg)
        row = {"game": folder.name, "package": pkg}
        t = time.time()
        try:
            src = find_source(sources, entry.source_hint if entry else folder.name, pkg)
            if not src:
                raise RuntimeError("source dump not found")
            reporter.stage(f"{folder.name}")
            a = analyze(src.apk)
            recipe = engine.suggest(a)
            res = build(src, a, recipe, outdir / pkg, reporter)
            row["primary"] = compare(res.apk, folder / f"{pkg}.apk")
            if res.alt_apk:
                known_alt = folder / f"{pkg}.alt-noforcequit.apk"
                row["alt"] = compare(res.alt_apk, known_alt) if known_alt.exists() else {"verdict": "UNEXPLAINED",
                                                                                           "diffs": [("alt", "UNEXPLAINED", "no known-good alt APK")]}
            elif (folder / f"{pkg}.alt-noforcequit.apk").exists():
                row["alt"] = {"verdict": "UNEXPLAINED", "diffs": [("alt", "UNEXPLAINED", "recipe has no alternate build")]}
            row["new_apk"] = str(res.apk)
            row["new_alt_apk"] = str(res.alt_apk) if res.alt_apk else None
            # unsupported games (32-bit) fail the 64-bit check by design
            row["checks_failed"] = [c for c in res.checks if c["ok"] is False
                                    and not (entry and entry.status == "unsupported")]
            row["use_alt"] = recipe.use_alt
        except Exception as exc:  # noqa: BLE001
            row["error"] = str(exc)
        row["seconds"] = round(time.time() - t)
        results.append(row)
        results.sort(key=lambda r: r["game"].lower())
        verdicts = [row.get(k, {}).get("verdict") for k in ("primary", "alt") if k in row]
        reporter.log(f"{folder.name}: {row.get('error') or ', '.join(verdicts)}")
        if not keep and "error" not in row:
            pass  # APKs are kept for the device reinstall step; delete outdir afterwards
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "parity.json").write_text(json.dumps(results, indent=1, default=str))
    for r in results:  # rows from earlier runs: apply the same "unsupported games" rule
        e = catalog.lookup(r["package"])
        if e and e.status == "unsupported":
            r["checks_failed"] = []
    write_report(results, report)
    return all("error" not in r and all(r.get(k, {}).get("verdict") != "UNEXPLAINED" for k in ("primary", "alt"))
               and not r.get("checks_failed") for r in results)


def write_report(results: list[dict], path: Path) -> None:
    lines = ["# Parity report", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')}. Rebuilt each game from its original dump "
             "with its catalog recipe and compared every APK entry with the known-good build (META-INF excluded).", "",
             "| Game | Primary | Alternate | Failed checks | Notes |", "|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['game']} | ERROR | | | {r['error']} |")
            continue
        notes = []
        for k in ("primary", "alt"):
            for n, kind, why in r.get(k, {}).get("diffs", []):
                notes.append(f"{k}: `{n.rsplit('/', 1)[-1]}` {kind} — {why}")
        failed = ", ".join(c["name"] for c in r.get("checks_failed", [])) or "none"
        lines.append(f"| {r['game']} | {r['primary']['verdict']} | {r.get('alt', {}).get('verdict', '—')} | {failed} | "
                     + "<br>".join(notes) + " |")
    ok = sum(1 for r in results if "error" not in r and all(r.get(k, {}).get("verdict") != "UNEXPLAINED" for k in ("primary", "alt")))
    lines += ["", f"**{ok}/{len(results)} games at parity** (identical, expected or equivalent)."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_and_test(results_json: Path, target, baseline: Path | None, report: Path, reporter: Reporter,
                     only: list[str] | None = None, seconds: int = 45, test_only: bool = False) -> bool:
    """Device half of the parity test: install every rebuilt APK through the normal installer (APK only; the game
    data already on the Frame is reused), add the Steam shortcuts in one batch, launch-test each game headless and
    compare with a baseline run (lines like '######## <pkg>: RUNNING (pid N)')."""
    from .install import installer
    from .validate import device

    rows = json.loads(results_json.read_text())
    base_state = {}
    if baseline and baseline.exists():
        for line in baseline.read_text().splitlines():
            m = __import__("re").match(r"#+ (\S+): (\w+)", line)
            if m:
                base_state[m[1]] = "RUNNING" if m[2] == "RUNNING" else "EXITED"
    installed_pkgs, out = [], []
    previous = {}
    if report.with_suffix(".json").exists():
        previous = {r["package"]: r for r in json.loads(report.with_suffix(".json").read_text())}
    if test_only:  # re-run launch tests only; keep install/shortcut results from the previous run
        out = [dict(previous[r["package"]]) for r in rows if r["package"] in previous
               and (not only or any(o.lower() in r["game"].lower() for o in only))]
        rows = []
    for row in rows:
        pkg = row["package"]
        if only and not any(o.lower() in row["game"].lower() for o in only):
            continue
        if "error" in row:
            out.append({**row, "install": "skipped (build error)"})
            continue
        entry = catalog.lookup(pkg)
        apk = Path(row["new_alt_apk"] if row.get("use_alt") and row.get("new_alt_apk") else row["new_apk"])
        a = analyze(apk, deep=False)
        recipe = engine.suggest(a)
        title = entry.title if entry else a.label
        res = {"game": row["game"], "package": pkg, "apk": apk.name}
        try:
            reporter.stage(f"install {title}")
            installer.install(target.frame, installer.InstallPlan(pkg, title, apk, None, recipe, apk_only=True), reporter)
            installed_pkgs.append(pkg)
            res["install"] = "ok"
        except Exception as exc:  # noqa: BLE001
            res["install"] = f"FAILED: {exc}"
        out.append(res)
    if installed_pkgs and not test_only:
        status = installer.add_to_steam(target.frame, installed_pkgs, reporter, wait=180)
        added = {a["package"]: a for a in status.get("added", [])}
        for r in out:
            a = added.get(r["package"])
            if a:
                r["shortcut"] = "same id" if a["appid"] == a["expected"] else f"id changed {a['expected']}→{a['appid']}"
    for r in out:
        if r.get("install") != "ok":
            continue
        try:
            t, _ = device.launch_test(target.frame, r["package"], reporter, seconds)
            r["state"], r["milestone"], r["fps"] = t.state, t.milestone, t.fps
            r["findings"] = [f.id for f in t.findings if f.severity == "fatal"]
        except Exception as exc:  # noqa: BLE001
            r["state"] = f"ERROR {exc}"
        r["baseline"] = base_state.get(r["package"], "?")
        r["regression"] = r["baseline"] == "RUNNING" and r["state"] != "RUNNING"
        previous[r["package"]] = r
        (report.with_suffix(".json")).write_text(json.dumps(sorted(previous.values(), key=lambda x: x["game"].lower()),
                                                            indent=1, default=str))
    out = sorted(previous.values(), key=lambda x: x["game"].lower()) if previous else out
    lines = ["# Device parity (install + headless launch)", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')}. APK-only reinstall through the FramePort agent, Steam "
             "shortcuts re-added in one batch, then a 45 s headless launch per game compared with the pre-change baseline.",
             "", "| Game | Install | Shortcut | Baseline | Now | Furthest milestone | fps | Fatal findings |",
             "|---|---|---|---|---|---|---|---|"]
    for r in out:
        lines.append(f"| {r['game']} | {r.get('install')} | {r.get('shortcut', '')} | {r.get('baseline', '')} | "
                     f"{r.get('state', '')}{' **REGRESSION**' if r.get('regression') else ''} | {r.get('milestone') or ''} | "
                     f"{r.get('fps') or ''} | {', '.join(r.get('findings') or [])} |")
    regressions = [r for r in out if r.get("regression") or (r.get("install") not in ("ok", None) and "skipped" not in r["install"])]
    lines += ["", f"**{len(out) - len(regressions)}/{len(out)} OK, {len(regressions)} regression(s).**"]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return not regressions
