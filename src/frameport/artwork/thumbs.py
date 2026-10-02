"""Small cached thumbnails of the artwork, served to the GUI by URL (the GUI's assets folder is the user data dir).

Full-size store art is 0.3–2 MB per image; sending it as bytes on every render froze the app. Thumbnails are JPEGs named
after their source's content hash (t_<kind>_<width>_<sha8>.jpg), so the client cache stays correct when art changes.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..core.paths import user_data_dir
from . import fetch

WIDTHS = {"portrait": 400, "square": 400, "landscape": 1280, "hero": 1280, "icon": 96, "logo": 600}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def _digest(path: Path) -> str:
    st = path.stat()
    return hashlib.sha1(f"{path.name}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:8]


def thumb(path: Path, width: int, kind: str | None = None) -> Path | None:
    """A JPEG (PNG for logos, which need transparency) at most `width` px wide; created once."""
    kind = kind or path.stem
    ext = ".png" if kind == "logo" else ".jpg"
    out = path.parent / f"t_{kind}_{width}_{_digest(path)}{ext}"
    if out.exists():
        return out
    with _lock(str(out)):
        if out.exists():
            return out
        try:
            from PIL import Image

            with Image.open(path) as im:
                im.load()
                if im.width > width:
                    im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
                for old in path.parent.glob(f"t_{kind}_{width}_*{ext}"):
                    old.unlink(missing_ok=True)
                tmp = out.with_suffix(out.suffix + ".tmp")
                if ext == ".png":
                    im.save(tmp, "PNG", optimize=True)
                else:
                    im.convert("RGB").save(tmp, "JPEG", quality=86, optimize=True, progressive=True)
                tmp.replace(out)
        except Exception:  # unreadable/odd image: use the original
            return path
    return out


def pick(package: str, kinds: tuple[str, ...]) -> Path | None:
    files = {p.stem: p for p in fetch.files(package)}
    return next((files[k] for k in kinds if k in files), None)


def url(package: str, kinds: tuple[str, ...], width: int | None = None, wait: bool = True) -> str | None:
    """Asset URL (for ft.Image / DecorationImage src) of the first available kind, as a thumbnail. wait=False (render
    paths): if the thumbnail doesn't exist yet it is made in the background and the original is used meanwhile."""
    src = pick(package, kinds)
    if not src:
        return None
    width = width or WIDTHS.get(src.stem, 600)
    if not wait:
        ready = ready_thumb(src, width, src.stem)
        if ready is None:
            threading.Thread(target=thumb, args=(src, width, src.stem), daemon=True).start()
            return asset_url(src)
        return asset_url(ready)
    return asset_url(thumb(src, width, src.stem))


def ready_thumb(path: Path, width: int, kind: str | None = None) -> Path | None:
    """The thumbnail if it already exists (no image work)."""
    kind = kind or path.stem
    out = path.parent / f"t_{kind}_{width}_{_digest(path)}{'.png' if kind == 'logo' else '.jpg'}"
    return out if out.exists() else None


def asset_url(path: Path) -> str:
    return "/" + path.relative_to(user_data_dir()).as_posix()


def prewarm(package: str) -> None:
    """Create the thumbnails the GUI uses (cards, hero, icons) ahead of time (called after fetching art)."""
    for kinds, width in ((("portrait", "square", "icon"), 400), (("hero", "landscape", "portrait", "square"), 1280),
                         (("icon", "square", "portrait"), 96)):
        src = pick(package, kinds)
        if src:
            thumb(src, width, src.stem)
