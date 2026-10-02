"""Find Quest games on disk.

Accepted layouts:
  <folder>/<something>.apk [+ <folder>/<package>/ (OBB or raw asset data)]   e.g. common downloader layouts
  <folder>/<package>.apk + <folder>/obb/                                       FramePort/PATCHED output layout
  a single .apk file
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..core.models import SourceGame

# a release tag at the end of a download folder name: " -TAG" or " -TAG v76" (no space after the dash)
TAG = re.compile(r"\s+-(?=[A-Za-z])[A-Za-z0-9]{2,12}(?:\s+[A-Za-z]?\d{1,4})?\s*$")


def display_name(folder_name: str) -> str:
    """'PowerWash Simulator VR v3055+2.5.0 -TAG v76' -> 'PowerWash Simulator VR v3055+2.5.0'."""
    return re.sub(r'[<>:"/\\|?*]', "_", TAG.sub("", folder_name).strip())


def _package_of(apk: Path) -> str | None:
    try:
        from pyaxmlparser import APK

        return APK(str(apk)).package
    except Exception:
        return None


def _is_apk(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return "AndroidManifest.xml" in z.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def from_path(path: Path) -> SourceGame | None:
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".apk":
        pkg = _package_of(path)
        data = next((d for d in (path.parent / (pkg or ""), path.parent / "obb") if pkg and d.is_dir()), None)
        return SourceGame(display_name(path.stem), path, data, path.parent)
    if not path.is_dir():
        return None
    apks = sorted(p for p in path.glob("*.apk") if _is_apk(p))
    if not apks:
        return None
    primary = [a for a in apks if ".alt-" not in a.name]
    apk = primary[0] if primary else apks[0]
    pkg = _package_of(apk)
    data = None
    for cand in ((path / pkg) if pkg else None, path / "obb"):
        if cand and cand.is_dir() and any(cand.iterdir()):
            data = cand
            break
    return SourceGame(display_name(path.name), apk, data, path, [a for a in apks if a != apk])


def scan(root: Path, depth: int = 2) -> list[SourceGame]:
    """Find games under root (the root itself, or folders up to `depth` levels down)."""
    root = Path(root)
    found = []
    game = from_path(root)
    if game:
        return [game]
    frontier = [root]
    for _ in range(depth):
        nxt = []
        for d in frontier:
            try:
                children = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
            except OSError:
                continue
            for child in children:
                g = from_path(child)
                if g:
                    found.append(g)
                else:
                    nxt.append(child)
        frontier = nxt
    loose = [p for p in root.glob("*.apk") if _is_apk(p)]
    found += [g for g in (from_path(p) for p in loose) if g]
    return found
