#!/usr/bin/env python3
"""Build a native FramePort app bundle for the current OS.

    python scripts/package.py            # flet build (Flutter; best result, needs the Flutter SDK - flet installs it)
    python scripts/package.py --pyinstaller   # flet pack (PyInstaller one-folder app; no Flutter SDK needed)

Bundled data (catalog, prebuilt artifacts, Frame agent, bootstrap script) is copied into src/frameport/_data first,
which is where the installed app looks for it (see core/paths.py). Java/overport/apksigner are NOT bundled: the app
downloads and manages them on first run (Tools page), so bundles stay small and tools stay current.
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/frameport/_data"
TARGET = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]


def stage_data():
    shutil.rmtree(DATA, ignore_errors=True)
    for name in ("catalog", "artifacts", "agent", "bootstrap"):
        shutil.copytree(ROOT / name, DATA / name)
    print(f"staged data in {DATA}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pyinstaller", action="store_true")
    ap.add_argument("--keep-data", action="store_true", help="leave src/frameport/_data after building")
    args = ap.parse_args()
    stage_data()
    try:
        if args.pyinstaller:
            cmd = ["flet", "pack", str(ROOT / "src/main.py"), "--name", "FramePort", "--product-name", "FramePort",
                   "--add-data", f"{DATA}{';' if TARGET == 'windows' else ':'}frameport/_data", "--distpath", str(ROOT / "dist")]
        else:
            # --yes: install the Flutter SDK etc. without prompting; --no-rich-output: plain logs (CI, Windows consoles)
            cmd = ["flet", "build", TARGET, str(ROOT), "--project", "FramePort", "--product", "FramePort",
                   "--module-name", "main", "--output", str(ROOT / "dist" / TARGET), "--yes", "--no-rich-output"]
        print(" ".join(cmd))
        return subprocess.call(cmd, cwd=ROOT)
    finally:
        if not args.keep_data:
            shutil.rmtree(DATA, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
