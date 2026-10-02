#!/usr/bin/env python3
"""CI smoke test of FramePort's self-update on this OS, with the archive this job just built:

    python scripts/update_smoke.py FramePort-<os>.<zip|tar.gz>

It unpacks the archive twice (an "installed" copy, plus an older-looking copy of it with a stale marker file), then
runs the real update path: frameport.updates._extract (ditto on macOS) + the real swap script (PowerShell on Windows,
/bin/sh elsewhere), without relaunching. It checks the installed copy now has the new files, and on Windows that the
signer check sees the same certificate on both copies. Exits non-zero on any failure.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    import logging

    logging.basicConfig(level=logging.INFO)
    archive = Path(sys.argv[1]).resolve()
    home = Path(tempfile.mkdtemp(prefix="fp-update-smoke-"))
    os.environ["FRAMEPORT_HOME"] = str(home / "data")
    from frameport import updates

    platform = sys.platform
    new = updates._extract(archive, home / "staged", platform)
    installed_parent = home / "Programs"
    installed_parent.mkdir()
    installed = installed_parent / new.name
    old = updates._extract(archive, home / "old", platform)
    shutil.move(str(old), str(installed))
    stale = installed / ("Contents/stale.txt" if platform == "darwin" else "stale.txt")
    stale.write_text("from the old version")
    marker = new / ("Contents/new-version.txt" if platform == "darwin" else "new-version.txt")
    marker.write_text("new")
    sub = next(p for p in sorted(marker.parent.iterdir()) if p.is_dir() and not p.is_symlink())
    nested = sub / "new-version.txt"  # files in existing subfolders must be replaced too (Windows merges folders)
    nested.write_text("new")
    if platform == "win32" and os.environ.get("HAS_CERT") == "true":
        a, b = updates._signer_thumbprint(installed / "FramePort.exe"), updates._signer_thumbprint(new / "FramePort.exe")
        print(f"signer thumbprints: installed {a} new {b}")
        assert a and a == b, "signature check failed"
    script = updates.apply(new, installed, relaunch=False, pid=999999, platform=platform, wait=True)
    log = (home / "data/logs/update.log").read_text()
    print(script.read_text()[:400], "...\n--- update.log ---\n" + log)
    installed_marker = installed / marker.relative_to(new)
    assert installed_marker.read_text() == "new", "the new files weren't installed"
    assert (installed / nested.relative_to(new)).read_text() == "new", "a subfolder wasn't updated"
    assert "installed" in log and "FAILED" not in log, "the update script reported a failure"
    exe = {"win32": installed / "FramePort.exe", "darwin": installed / "Contents/MacOS"}.get(platform,
                                                                                         installed / "FramePort")
    assert exe.exists(), f"{exe} missing after the update"
    if platform != "win32":  # Windows copies over the old files (the zip has no folder of its own); others swap
        assert not stale.exists(), "the old version's files are still there"
    print("update smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
