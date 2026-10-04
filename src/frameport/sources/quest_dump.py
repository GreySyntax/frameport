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


def _no_quest_games_below(d: Path) -> bool:
    """Folders not worth searching for APKs: a PC program (exe/dll files), a git checkout, a Python environment."""
    try:
        names = [p.name.lower() for p in d.iterdir()]
    except OSError:
        return True
    return any(n.endswith((".exe", ".dll")) for n in names) or ".git" in names or "pyvenv.cfg" in names


def _other_folders(d: Path, game: SourceGame) -> list[Path]:
    """Subfolders of a folder with an APK that aren't that game's own data (OBB) folder."""
    try:
        subs = [p for p in d.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))]
    except OSError:
        return []
    own = {game.data_dir.resolve()} if game.data_dir else set()
    return [p for p in subs if p.resolve() not in own and p.name.lower() not in ("obb", "android")]


def scan(root: Path, depth: int = 5) -> list[SourceGame]:
    """Find games under root, up to `depth` folder levels down (e.g. a download manager's
    "<library>/data/downloads/<game>/game.apk"). A folder with an APK and nothing but that game's data folder is one
    game; a folder that also has other folders is a collection: its loose APKs are games and its folders are searched
    (a "VR" folder with a stray APK next to the game folders used to count as one game). PC program folders and code
    checkouts aren't searched."""
    found: list[SourceGame] = []

    def walk(d: Path, level: int) -> None:
        game = from_path(d)
        if game and (level >= depth or not _other_folders(d, game)):
            found.append(game)
            return
        if game:  # a collection with loose APKs: each is a game of its own
            found.extend(g for g in (from_path(p) for p in sorted(d.glob("*.apk")) if _is_apk(p)) if g)
        if level >= depth:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
        except OSError:
            return
        for child in children:
            if game and game.data_dir and child.resolve() == game.data_dir.resolve():
                continue
            if from_path(child) or not _no_quest_games_below(child):
                walk(child, level + 1)

    root = Path(root)
    if root.is_file():
        g = from_path(root)
        return [g] if g else []
    walk(root, 0)
    return found
