#!/usr/bin/env python3
"""Make a screenshot-safe copy of a FramePort data folder (for docs/images):

    python scripts/scrub_library.py <data dir> <new dir> [--sort size]

Copies library.json + artwork/ only (no Frame pairing, SSH key, tools, logs or builds). In the copy every game's
folder name becomes its store title, every local path becomes D:/Games/<Quest|PC VR>/<title>/..., settings other than
the Library view are dropped, and install/launch history is removed. Fails if any original path is left.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

PATHLIKE = re.compile(r"^(/|[A-Za-z]:[\\/]|\\\\)")
DEVICE = ("/data/", "/sdcard", "/storage/", "/system/", "/vendor/", "/apex/", "/proc/", "/dev/")  # inside the headset
DROP_KEYS = {"installs", "log_path", "last_played", "played"}


def safe(title: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "", title).strip() or "Game"


def scrub_game(g: dict) -> dict:
    title = g.get("title") or g.get("package")
    base = f"D:/Games/{'PC VR' if g.get('kind') == 'rift' else 'Quest'}/{safe(title)}"
    originals: set[str] = set()

    def collect(o):
        if isinstance(o, dict):
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)
        elif isinstance(o, str) and PATHLIKE.match(o) and not o.startswith(DEVICE):
            originals.add(o)
    collect(g)
    roots = sorted({str(Path(p).parent) for p in originals} | originals, key=len, reverse=True)

    def fix(o, key=""):
        if isinstance(o, dict):
            return {k: fix(v, k) for k, v in o.items() if k not in DROP_KEYS}
        if isinstance(o, list):
            return [fix(v, key) for v in o]
        if isinstance(o, str):
            if PATHLIKE.match(o) and not o.startswith(DEVICE):
                name = Path(o.replace("\\", "/")).name
                return base if key in ("origin", "folder", "game_dir", "data_dir", "base") else f"{base}/{name}"
            for r in roots:
                if len(r) > 3 and r in o:
                    o = o.replace(r, base)
            o = re.sub(r"/(?:mnt|home|Users)/[^)\"]*", "D:/Games", o)  # paths inside messages (e.g. a tool's folder)
        return o
    out = fix(g)
    out["name"] = title
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--sort", default="size", choices=["name", "recent", "played", "size", "status"])
    args = ap.parse_args()
    lib = json.loads((args.src / "library.json").read_text())
    games = {pkg: scrub_game(g) for pkg, g in lib.get("games", {}).items()}
    view = {**(lib.get("settings", {}).get("ui.library") or {}), "sort": args.sort, "platform": "all",
            "where": "all", "status": "all", "tags": []}
    out = {"games": games, "settings": {"ui.library": view, "migrations": lib.get("settings", {}).get("migrations", [])}}
    text = json.dumps(out, indent=1)
    leftovers = [o for o in re.findall(r'"((?:/|[A-Za-z]:\\\\)[^"]*)"', text) if not o.startswith(("D:/Games/",) + DEVICE)]
    if leftovers:
        print("not scrubbed:", leftovers[:10], file=sys.stderr)
        return 1
    args.dest.mkdir(parents=True, exist_ok=True)
    (args.dest / "library.json").write_text(text)
    if (args.src / "artwork").is_dir():
        shutil.copytree(args.src / "artwork", args.dest / "artwork", dirs_exist_ok=True)
    print(f"{len(games)} games -> {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
