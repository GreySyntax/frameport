"""Store artwork for the Steam library (portrait/landscape/hero/logo/icon).

Fetched live from OVRPort's image service (Meta store art by package name), cached per package. Falls back to
the APK's launcher icon when the store has nothing (e.g. sideload-only titles).
"""
from __future__ import annotations

import base64
import zipfile
from pathlib import Path

from ..core import cache
from ..core.paths import user_data_dir

API = "https://ovrp.crx.moe/images/by_package?package={package}"
KINDS = {
    "APP_IMG_COVER_PORTRAIT": "portrait",
    "APP_IMG_COVER_LANDSCAPE": "landscape",
    "APP_IMG_HERO": "hero",
    "APP_IMG_LOGO_TRANSPARENT": "logo",
    "APP_IMG_ICON": "icon",
}
EXTRA_KINDS = ("square",)  # OculusDB's square cover (Rift games)
PICKED = ".picked"  # marker: the art in this folder was chosen by the user (Find artwork); automatic fetches skip it


def artwork_dir(package: str) -> Path:
    path = user_data_dir() / "artwork" / package
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ext(data: bytes) -> str:
    return ".png" if data[:4] == b"\x89PNG" else ".jpg"


def fetch(package: str, apk: Path | None = None, refresh: bool = False,
          lookup: str | None = None) -> tuple[Path, str | None]:
    """Returns (folder with <kind>.<ext> files, store display name or None). `lookup` is the package whose store art
    to use (a Rift game borrows its Quest version's art); Rift ids without one have no store art.
    Without `refresh` it only fills in missing kinds and never touches art the user picked (PICKED marker): a pick
    often lacks a kind (Steam has no icon, OculusDB only a square cover), and re-fetching the store art for that
    replaced the pick at the next install."""
    out = artwork_dir(package)
    if refresh:
        (out / PICKED).unlink(missing_ok=True)
    have = {p.stem for p in out.iterdir()}
    title = None
    lookup = lookup or (None if package.startswith("rift.") else package)
    picked = (out / PICKED).exists()
    if lookup and not picked and (refresh or not {"portrait", "landscape", "hero", "icon"} <= have):
        try:
            data = cache.http_get(API.format(package=lookup), timeout=30).json()
        except Exception:
            data = {}
        if data.get("status") == "ok":
            title = data.get("displayName")
            for img in data.get("images", []):
                kind = KINDS.get(img.get("image_type"))
                if not kind:
                    continue
                if not refresh and kind in have:
                    continue  # keep what's there; only fill gaps
                raw = base64.b64decode(img["uri"])
                for old in out.glob(f"{kind}.*"):
                    old.unlink()
                (out / f"{kind}{_ext(raw)}").write_bytes(raw)
            if title:
                (out / "title.txt").write_text(title, encoding="utf-8")
    if not any(out.glob("icon.*")) and apk:
        icon = apk_icon(apk)
        if icon:
            (out / f"icon{_ext(icon)}").write_bytes(icon)
    if title is None and (out / "title.txt").exists():
        title = (out / "title.txt").read_text(encoding="utf-8")
    return out, title


def apk_icon(apk: Path) -> bytes | None:
    """Largest PNG launcher icon inside the APK (mipmap/drawable)."""
    try:
        with zipfile.ZipFile(apk) as z:
            cands = [i for i in z.infolist() if i.filename.startswith("res/") and "ic_launcher" in i.filename
                     and i.filename.endswith((".png", ".webp"))]
            if not cands:
                cands = [i for i in z.infolist() if i.filename.startswith("res/mipmap") and i.filename.endswith(".png")]
            if not cands:
                return None
            best = max(cands, key=lambda i: i.file_size)
            data = z.read(best)
            return data if data[:4] == b"\x89PNG" else None
    except (zipfile.BadZipFile, OSError):
        return None


def files(package: str) -> list[Path]:
    return sorted(p for p in artwork_dir(package).iterdir() if p.stem in KINDS.values() or p.stem in EXTRA_KINDS)
