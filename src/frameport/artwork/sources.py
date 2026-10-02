"""Find artwork for games that the Meta art service (by Android package) doesn't cover — Oculus Rift games.

Order (all lookups cached for 30 days; only exact title matches are used, so no wrong art is picked):
  1. the Quest version's package (catalog, library, or OculusDB's packageName) → the full Meta art set (fetch.fetch)
  2. OculusDB (community database of the Oculus store): the Rift app's square cover
  3. the Steam store (games also sold on Steam): portrait, hero, landscape and logo
  4. the game's own program icon
"""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from ..core import cache
from . import fetch

OCULUSDB = "https://oculusdb.rui2015.me"
STEAM_SEARCH = "https://store.steampowered.com/api/storesearch/?term={term}&cc=us&l=en"
STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps/{id}/{file}"
STEAM_FILES = {"portrait": "library_600x900.jpg", "hero": "library_hero.jpg", "landscape": "header.jpg",
               "logo": "logo.png"}
MAX_AGE = 30 * 86400


ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
QUEST_SUFFIXES = {"", "unplugged", "quest", "questedition", "forquest", "vr"}


def norm(s: str) -> str:
    """Comparable title: lower-case letters and digits only, roman numerals as digits ('Lone Echo II' = 'Lone Echo 2',
    "Asgard's Wrath" = 'Asgards Wrath')."""
    words = re.findall(r"[a-z0-9]+", (s or "").lower().replace("'", ""))
    return "".join(ROMAN.get(w, w) if i and w in ROMAN else w for i, w in enumerate(words))


def search_terms(title: str) -> list[str]:
    """The title, then shorter forms (OculusDB's search doesn't match "Asgards" to "Asgard's")."""
    words = re.sub(r"[^\w\s]", " ", title).split()
    terms = [title]
    for n in (3, 2, 1):
        if len(words) > n and len(" ".join(words[:n])) >= 4:
            terms.append(" ".join(words[:n]))
    if words and len(words[0]) >= 5 and words[0].lower().endswith("s"):
        terms.append(words[0][:-1])  # "Asgards" -> "Asgard", "Wilsons" -> "Wilson"
    return list(dict.fromkeys(terms))


def _key(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", term.lower())[:80]


def oculusdb_search(term: str, rift_only: bool = True) -> list[dict]:
    q = urllib.parse.quote(term)
    url = f"{OCULUSDB}/api/v1/search/{q}" + ("?headsets=RIFT" if rift_only else "")
    res = cache.cached_json(f"oculusdb-{'rift' if rift_only else 'all'}-{_key(term)}.json", url, max_age=MAX_AGE,
                            fallback=[])
    return [r for r in res or [] if isinstance(r, dict) and r.get("__OculusDBType", "Application") == "Application"]


def steam_search(term: str) -> list[dict]:
    res = cache.cached_json(f"steamsearch-{_key(term)}.json", STEAM_SEARCH.format(term=urllib.parse.quote(term)),
                            max_age=MAX_AGE, fallback={})
    return (res or {}).get("items") or []


def match_rift(title: str, canonical: str | None = None) -> dict | None:
    """The OculusDB Rift app for this game (exact normalized name or canonicalName match)."""
    want = norm(title)
    terms = ([canonical.replace("-", " ")] if canonical else []) + search_terms(title)
    for term in terms:
        for r in oculusdb_search(term):
            names = {norm(r.get("appName") or ""), norm(r.get("displayName") or "")}
            if (canonical and r.get("canonicalName") == canonical) or want in names:
                return r
    return None


def match_quest(title: str) -> str | None:
    """Package of the game's Quest version on the Meta store (same name, or the name plus a short suffix such as
    'Unplugged'), from OculusDB."""
    want = norm(title)
    if len(want) < 4:
        return None
    for term in search_terms(title)[:2]:
        for r in oculusdb_search(term, rift_only=False):
            pkg = r.get("packageName")
            n = norm(r.get("appName") or r.get("displayName") or "")
            # same name, or the name plus a platform word ("Robo Recall: Unplugged") — never a sequel/other game
            if pkg and n.startswith(want) and n[len(want):] in QUEST_SUFFIXES:
                return pkg
    return None


def match_steam(title: str) -> dict | None:
    want = norm(title)
    return next((i for i in steam_search(title) if norm(i.get("name") or "") == want), None)


def _save(package: str, kind: str, data: bytes) -> None:
    if not data or len(data) < 200:
        return
    ext = ".png" if data[:4] == b"\x89PNG" else ".webp" if data[8:12] == b"WEBP" else ".jpg"
    d = fetch.artwork_dir(package)
    for old in d.glob(f"{kind}.*"):
        old.unlink()
    (d / f"{kind}{ext}").write_bytes(data)


def _get(url: str) -> bytes | None:
    try:
        r = cache.http_get(url, timeout=30)
        return r.content if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def apply_oculusdb(package: str, app: dict) -> bool:
    link = app.get("imageLink") or (f"/cdn/images/{app['id']}" if app.get("id") else None)
    data = _get(OCULUSDB + link) if link else None
    if data:
        _save(package, "square", data)
    return bool(data)


def apply_steam(package: str, appid: int | str) -> bool:
    got = False
    for kind, file in STEAM_FILES.items():
        data = _get(STEAM_CDN.format(id=appid, file=file))
        if data:
            _save(package, kind, data)
            got = True
    return got


def has_art(package: str) -> bool:
    stems = {p.stem for p in fetch.files(package)}
    return bool(stems & {"portrait", "square", "landscape", "hero"})


def fetch_rift(package: str, title: str, canonical: str | None = None, quest_package: str | None = None,
               exe: Path | None = None, oculus: bool = True) -> dict:
    """Find and store artwork for a PC VR game. Returns what was found: {source, quest_package, oculus_app_id,
    canonical_name, steam_appid} (only the keys that apply). oculus=False (no Oculus code: a SteamVR/OpenXR game):
    Steam is asked first, the Oculus store only if Steam has nothing."""
    found: dict = {}
    if not oculus:
        try:
            st = match_steam(title)
        except Exception:  # noqa: BLE001 - offline
            st = None
        if st and apply_steam(package, st["id"]):
            return {"steam_appid": st["id"], "source": "steam"}
    app = None
    try:
        app = match_rift(title, canonical)
    except Exception:  # noqa: BLE001 - offline: fall through to local sources
        app = None
    if app:
        found.update(oculus_app_id=app.get("id"), canonical_name=app.get("canonicalName"),
                     title=app.get("displayName") or app.get("appName"))
    if not quest_package:
        try:
            quest_package = match_quest(found.get("title") or title)
        except Exception:  # noqa: BLE001
            quest_package = None
    if quest_package:
        found["quest_package"] = quest_package
        fetch.fetch(package, lookup=quest_package)
        if has_art(package):
            found["source"] = "meta"
    if app and not found.get("source") and apply_oculusdb(package, app):
        found["source"] = "oculusdb"
    if found.get("source") != "meta":
        try:
            st = match_steam(found.get("title") or title)
        except Exception:  # noqa: BLE001
            st = None
        if st and apply_steam(package, st["id"]):
            found["steam_appid"] = st["id"]
            found["source"] = found.get("source") or "steam"
    if not has_art(package) and exe is not None:
        from ..analysis.rift import exe_icon

        icon = exe_icon(exe)
        if icon:
            _save(package, "icon", icon)
            found["source"] = "icon"
    return found


def search(term: str) -> list[dict]:
    """Candidates for the artwork picker: [{source, name, preview (URL), id|package}]."""
    out = []
    try:
        for r in oculusdb_search(term, rift_only=False)[:12]:
            link = r.get("imageLink") or (f"/cdn/images/{r['id']}" if r.get("id") else None)
            if link:
                out.append({"source": "Meta (Quest)" if r.get("packageName") else "Oculus Rift",
                            "name": r.get("displayName") or r.get("appName"), "preview": OCULUSDB + link,
                            "id": r.get("id"), "package": r.get("packageName"), "app": r})
    except Exception:  # noqa: BLE001
        pass
    try:
        for i in steam_search(term)[:8]:
            out.append({"source": "Steam", "name": i.get("name"), "preview": STEAM_CDN.format(id=i["id"],
                        file="header.jpg"), "id": i["id"]})
    except Exception:  # noqa: BLE001
        pass
    return out


ART_STEMS = frozenset(fetch.KINDS.values()) | frozenset(fetch.EXTRA_KINDS)


def apply_choice(package: str, choice: dict) -> bool:
    """Store the artwork of a picker result, replacing the current art. It's downloaded into a staging folder first:
    when the result yields no cover (dead store image, no art for that package) the current art stays and this
    returns False. Screenshots and the stored title are kept either way."""
    import shutil

    tmp = f".pick.{package}"
    stage = fetch.artwork_dir(tmp)
    shutil.rmtree(stage, ignore_errors=True)
    stage = fetch.artwork_dir(tmp)
    try:
        if choice["source"] == "Steam":
            apply_steam(tmp, choice["id"])
        elif choice.get("package"):
            fetch.fetch(tmp, lookup=choice["package"], refresh=True)
            if not has_art(tmp):
                apply_oculusdb(tmp, choice["app"])
        else:
            apply_oculusdb(tmp, choice["app"])
        if not has_art(tmp):
            return False
        d = fetch.artwork_dir(package)
        for f in d.iterdir():  # the old art and its thumbnails (screenshot thumbnails are t_shot_*)
            if f.is_file() and (f.stem in ART_STEMS or f.name.startswith("t_") and not f.name.startswith("t_shot_")):
                f.unlink()
        for f in stage.iterdir():
            if f.is_file() and f.stem in ART_STEMS:
                f.replace(d / f.name)
        return True
    finally:
        shutil.rmtree(stage, ignore_errors=True)
