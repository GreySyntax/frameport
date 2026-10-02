"""overport CLI wrapper. overport's workspace (runtime versions, per-package signing keys) lives in our data dir."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..core.events import Reporter
from ..core.paths import user_data_dir
from . import toolchain


def workspace() -> Path:
    path = user_data_dir() / "overport-workspace"
    (path / "signatures").mkdir(parents=True, exist_ok=True)
    return path


def keystore(package: str) -> Path:
    return workspace() / "signatures" / f"{package}.keystore"


def import_keystores(*dirs: Path) -> int:
    """Copy existing per-package keystores (e.g. ~/overport/cache/signatures) into our workspace. Never overwrites:
    a game must keep being signed with the same key, or updates can't install over it."""
    count = 0
    for d in dirs:
        for ks in Path(d).glob("*.keystore"):
            target = keystore(ks.stem)
            if not target.exists():
                shutil.copy2(ks, target)
                count += 1
    return count


def _jar() -> str:
    jar = toolchain.overport_jar()
    if not jar:
        raise RuntimeError("overport is not installed; run `frameport tools install`")
    return str(jar)


def list_patches() -> list[str]:
    p = toolchain.run_java(["-jar", _jar(), "patches"], timeout=120)
    return re.findall(r"^- (patch_[a-z0-9_]+)", p.stdout, re.M)


def cli_version() -> str | None:
    p = toolchain.run_java(["-jar", _jar(), "help"], timeout=120)
    m = re.search(r"version ([\w.\-]+)", p.stdout)
    return m[1] if m else None


def patch(apk: Path, outdir: Path, name: str, patches: list[str], reporter: Reporter,
          version: str = "latest") -> Path:
    """Run `overport patch`; returns the output APK path. version='latest' always uses the newest runtime."""
    if not patches:  # overport 1.2.5+ refuses an empty --patches= (and without patches it only re-signs)
        raise ValueError("no overport patches selected")
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / name
    target.unlink(missing_ok=True)
    args = ["-jar", _jar(), "patch", f"--input={apk}", f"--output={outdir}", f"--output-name={name}",
            f"--workspace={workspace()}", f"--version={version}", "--patches=" + ";".join(patches)]
    reporter.log("overport " + " ".join(a for a in args[3:] if not a.startswith("--patches")))
    p = toolchain.run_java(args)
    # overport draws spinners with \r; keep only final line states
    for line in (p.stdout + p.stderr).splitlines():
        line = line.split("\r")[-1].strip()
        if line:
            reporter.log("  " + line)
    if p.returncode or not target.exists():
        raise RuntimeError(f"overport failed (exit {p.returncode}); see log")
    return target
