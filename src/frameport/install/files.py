"""Send files (videos, documents, mods, saves, ...) to Lepton apps on the Frame.

Lepton gives every app the Frame's ~/Videos, ~/Downloads and ~/Documents as /sdcard/Movies, /sdcard/Download and
/sdcard/Documents (shared by all apps), and each app its own /sdcard (<install>/lepton-data/external). The agent reports
these destinations (`storage_targets`, read from Lepton itself). Android's media index doesn't work inside Lepton, so
media apps find these files by browsing folders, not in "all videos" lists. Some players only list their own folder
in /sdcard (4XVR: "Internal Storage" = /sdcard/4XPlayer): `link_app` hard-links what was sent to the shared folders into
that app's own folder too (agent `link_media`; no copy, no extra space). Uploads are resumable and use the fast link
(USB / the Frame's hotspot) like game installs.
"""
from __future__ import annotations

import posixpath
from pathlib import Path

from ..core.events import Reporter
from ..frame.connection import Frame
from . import installer

TARGETS_HELP = ("videos (/sdcard/Movies in every app), downloads (/sdcard/Download), documents (/sdcard/Documents), "
                "app (the game's own /sdcard; needs a game), app-files (/sdcard/Android/data/<package>/files)")


def storage_targets(frame: Frame, package: str | None = None) -> list[dict]:
    args = {"package": package} if package else {}
    return frame.agent("storage_targets", **args)["targets"]


def _items(paths: list[Path], subdir: str) -> list[tuple[Path, str, int]]:
    """(local file, relative remote path, size); folders keep their structure under their own name."""
    items = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                items.append((f, posixpath.join(subdir, p.name, f.relative_to(p).as_posix()), f.stat().st_size))
        elif p.is_file():
            items.append((p, posixpath.join(subdir, p.name), p.stat().st_size))
        else:
            raise FileNotFoundError(f"not found: {p}")
    return items


def _remote_size(frame: Frame, path: str) -> int | None:
    try:
        return frame.sftp.stat(path).st_size
    except OSError:
        return None


def _safe_subdir(subdir: str) -> str:
    parts = [x for x in (subdir or "").replace("\\", "/").split("/") if x and x != "."]
    if any(x == ".." for x in parts):
        raise ValueError("the folder must stay inside the destination (no '..')")
    return "/".join(parts)


def send_files(frame: Frame, paths: list[Path], target: str = "videos", package: str | None = None,
               subdir: str = "", reporter: Reporter | None = None, link_app: str | None = None) -> dict:
    """Upload to `target`. With link_app (a shared target only): the files also appear in that app's own folder."""
    reporter = reporter or Reporter()
    reporter.stage("Prepare the Frame")
    dests = {t["id"]: t for t in storage_targets(frame, package if target.startswith("app") else None)}
    if target not in dests:
        raise ValueError(f"unknown destination {target!r}; choose one of: {', '.join(dests) or TARGETS_HELP}")
    dest = dests[target]
    sub = _safe_subdir(subdir)
    items = _items(paths, sub)
    have = [i for i in items if _remote_size(frame, posixpath.join(dest["path"], i[1])) == i[2]]
    if have:
        reporter.log(f"{len(have)} file(s) already on the Frame; not re-sending")
        items = [i for i in items if i not in have]
    total = sum(i[2] for i in items)
    reporter.log(f"{len(items)} file(s), {total / 2**20:.0f} MiB → {dest['path']} (apps see {dest['android']})")
    with installer.transfer_link(frame, reporter, total) as xfer:
        reporter.stage(f"Upload {len(items)} file(s)")
        installer.upload_files(xfer, items, dest["path"], reporter, max(total, 1))
    where = posixpath.join(dest["android"], sub) if sub else dest["android"]
    linked = None
    if link_app and dest["shared"]:
        remote = [posixpath.join(dest["path"], i[1]) for i in items + have]
        linked = frame.agent("link_media", package=link_app, files=remote)
        if linked.get("folder"):
            reporter.log(f"added {len(linked['linked']) + len(linked['existing'])} file(s) to the game's own folder "
                         f"{linked['android']}")
            where = linked["android"]
        else:
            reporter.log("the game has no folder of its own yet (start it once); it finds the files in " + where)
    reporter.log(f"done: in the app, open {where}")
    return {"files": len(items), "skipped": len(have), "bytes": total, "path": posixpath.join(dest["path"], sub), "android": where,
            "shared": dest["shared"], "linked": linked}
