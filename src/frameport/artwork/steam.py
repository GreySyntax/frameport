"""A complete Steam library art set for a shortcut, composed from whatever artwork a game has.

Steam wants: portrait 600×900 (library grid), landscape 920×430 (recent/horizontal), hero 1920×620 (game page banner),
logo (transparent, drawn on the hero) and an icon. Store art rarely covers all of them — Rift games from OculusDB only
have a square cover — so missing shapes are composed: the best source image, fitted and centred over a blurred,
darkened copy of itself (or a screenshot, for the hero). Results are cached in artwork/<pkg>/steam/.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import fetch

SIZES = {"portrait": (600, 900), "landscape": (920, 430), "hero": (1920, 620)}
# preferred sources per Steam shape, best first ("shot" = first screenshot)
PREFER = {"portrait": ("portrait", "square", "icon", "landscape"),
          "landscape": ("landscape", "hero", "shot", "square", "portrait"),
          "hero": ("hero", "shot", "landscape", "square", "portrait"),
          "icon": ("icon", "square", "portrait")}


def _sources(package: str) -> dict[str, Path]:
    files = {p.stem: p for p in fetch.files(package)}
    shots = sorted(fetch.artwork_dir(package).glob("shot_*.jpg"))
    if shots:
        files["shot"] = shots[0]
    return files


def _fill(im, size):
    """Scale to cover `size` and centre-crop."""
    from PIL import Image

    w, h = size
    s = max(w / im.width, h / im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def _compose(src: Path, size, exact: bool):
    """exact: the source already has (about) the right shape → cover-fit; otherwise fit over a blurred backdrop."""
    from PIL import Image, ImageEnhance, ImageFilter

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = size
        if exact:
            return _fill(im, size)
        bg = _fill(im, size).filter(ImageFilter.GaussianBlur(radius=max(w, h) // 30))
        bg = ImageEnhance.Brightness(bg).enhance(0.45)
        s = min(w / im.width, h / im.height) * 0.94
        fg = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
        return bg


def _ratio_ok(src: Path, size) -> bool:
    from PIL import Image

    with Image.open(src) as im:
        r = im.width / im.height
    target = size[0] / size[1]
    return abs(r - target) / target < 0.18


def steam_set(package: str) -> dict[str, Path]:
    """{portrait, landscape, hero, logo?, icon?} files ready for Steam (created once per source change)."""
    src = _sources(package)
    if not src:
        return {}
    out_dir = fetch.artwork_dir(package) / "steam"
    out_dir.mkdir(exist_ok=True)
    key = hashlib.sha1("|".join(f"{k}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for k, p in sorted(src.items()))
                       .encode()).hexdigest()[:8]
    stamp = out_dir / f".{key}"
    result: dict[str, Path] = {}
    if stamp.exists():
        for p in out_dir.iterdir():
            if not p.name.startswith("."):
                result[p.stem] = p
        return result
    for p in out_dir.iterdir():
        p.unlink()
    for kind, size in SIZES.items():
        choice = next((k for k in PREFER[kind] if k in src), None)
        if not choice:
            continue
        try:
            # a screenshot always fills the wide banner; other sources only when their shape is close enough
            exact = _ratio_ok(src[choice], size) or (kind == "hero" and choice == "shot")
            img = _compose(src[choice], size, exact)
            path = out_dir / f"{kind}.jpg"
            img.save(path, "JPEG", quality=90, optimize=True)
            result[kind] = path
        except Exception:  # noqa: BLE001 - unreadable source: skip that shape
            continue
    if "logo" in src:
        result["logo"] = out_dir / f"logo{src['logo'].suffix}"
        result["logo"].write_bytes(src["logo"].read_bytes())
    icon = next((src[k] for k in PREFER["icon"] if k in src), None)
    if icon:
        try:
            from PIL import Image

            with Image.open(icon) as im:
                im = _fill(im.convert("RGBA"), (256, 256))
                path = out_dir / "icon.png"
                im.save(path, "PNG")
                result["icon"] = path
        except Exception:  # noqa: BLE001
            pass
    stamp.write_text("")
    return result


def original_platform(entry: dict) -> str:
    return "Oculus Rift" if entry.get("kind") == "rift" else "Meta Quest"


def steam_tags(entry: dict, where: str = "frame") -> list[str]:
    """Tags for the Steam shortcut (Steam can group them into collections): how it runs, the game's original platform,
    genres and the user's own tags."""
    runs = ("PC VR on Frame" if entry.get("kind") == "rift" else "Quest on Frame") if where == "frame" else \
        "Rift via Revive"
    base = [runs, original_platform(entry)]
    genres = ((entry.get("details") or {}).get("genres") or [])[:4]
    return list(dict.fromkeys(base + genres + list(entry.get("tags") or [])))
