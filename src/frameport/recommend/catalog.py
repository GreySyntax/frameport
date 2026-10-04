"""Known-good per-game recipes.

Sources, highest priority first:
  1. user entries   <user data>/catalog/games/*.yaml  (written by "Save as known-good" after a successful test)
  2. remote catalog FRAMEPORT_CATALOG_URL (a folder URL serving index.json + <package>.yaml), cached; lets the
                    catalog be updated without a new app release
  3. bundled        catalog/games/*.yaml in this repo
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..core import cache
from ..core.paths import catalog_dir, user_data_dir


@dataclass
class CatalogEntry:
    package: str
    title: str
    status: str = "unknown"  # works | issues | unsupported | unknown
    notes: str = ""
    details: str = ""
    tested_version: str = ""
    engine: str = ""
    xr: str = ""
    overport_extra: list[str] = field(default_factory=list)
    overport_remove: list[str] = field(default_factory=list)
    alt_overport: list[str] = field(default_factory=list)
    use_alt: bool = False
    frame: list[str] = field(default_factory=list)
    frame_remove: list[str] = field(default_factory=list)
    device: list[str] = field(default_factory=list)  # device.* toggles, e.g. device.text_input_window
    adapter: dict = field(default_factory=dict)
    device_files: dict = field(default_factory=dict)
    lepton_env: dict = field(default_factory=dict)
    pcvr_alternative: str | None = None
    # Rift (PC VR) recipes: package is "rift.<slug>"; kind "rift"
    kind: str = "quest"
    quest_package: str | None = None  # the Quest version of the same game (links the two in the UI)
    pcvr: list[str] = field(default_factory=list)  # pcvr.* patches to enable
    pcvr_remove: list[str] = field(default_factory=list)
    proton_env: dict = field(default_factory=dict)
    proton_tool: str = ""
    as_is: bool = False  # Rift: the dump runs unchanged (e.g. a SteamVR build: no Revive)
    verified: dict = field(default_factory=dict)
    updated: str = ""  # date of the last maintainer change without a new headset test (e.g. a fix from a report)
    source_hint: str = ""
    origin: str = "bundled"

    @classmethod
    def from_dict(cls, d: dict, origin: str) -> CatalogEntry:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known, origin=origin)

    def rev(self) -> str:
        """Fingerprint of the recipe content: library recipes derived from this entry follow it when it changes."""
        import hashlib
        import json

        d = {k: v for k, v in self.to_dict().items() if k != "origin"}
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        out = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            if k == "origin" or v in (None, "", [], {}, False) or (k == "kind" and v == "quest"):
                continue
            out[k] = v
        return out


def user_catalog_dir() -> Path:
    path = user_data_dir() / "catalog" / "games"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_dir(path: Path, origin: str) -> dict[str, CatalogEntry]:
    out = {}
    for f in sorted(path.glob("*.yaml")):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8"))
            out[d["package"]] = CatalogEntry.from_dict(d, origin)
        except Exception:
            continue
    return out


PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


def _load_remote() -> dict[str, CatalogEntry]:
    base = os.environ.get("FRAMEPORT_CATALOG_URL")
    if not base:
        return {}
    base = base.rstrip("/") + "/"
    index = cache.cached_json("catalog-index.json", base + "index.json", max_age=6 * 3600, fallback=[])
    out = {}
    for pkg in index or []:
        if not isinstance(pkg, str) or not PACKAGE_RE.match(pkg):
            continue  # names become cache file names and URLs: Android package / rift.<slug> ids only
        text = cache.cached_text(f"catalog-{pkg}.yaml", f"{base}{pkg}.yaml", max_age=6 * 3600)
        if text:
            try:
                out[pkg] = CatalogEntry.from_dict(yaml.safe_load(text), "remote")
            except Exception:
                pass
    return out


_cache: dict[str, CatalogEntry] | None = None


def load(refresh: bool = False) -> dict[str, CatalogEntry]:
    global _cache
    if _cache is None or refresh:
        entries = _load_dir(catalog_dir() / "games", "bundled")
        entries.update(_load_remote())
        for pkg, e in _load_dir(user_catalog_dir(), "user").items():
            if not _newer(entries.get(pkg), e):  # the user's own recipe, unless a maintained one was verified later
                entries[pkg] = e
        _cache = entries
    return _cache


def _newer(maintained: CatalogEntry | None, user: CatalogEntry) -> bool:
    """True when the bundled/remote entry was verified after the user's (e.g. a fix for a config the user shared)."""
    if maintained is None:
        return False
    when = max(str(maintained.updated or ""), str(maintained.verified.get("date") or ""))
    return when > str(user.verified.get("date") or "")


def lookup(package: str) -> CatalogEntry | None:
    return load().get(package)


def save_user_entry(entry: CatalogEntry) -> Path:
    path = user_catalog_dir() / f"{entry.package}.yaml"
    path.write_text(to_yaml(entry), encoding="utf-8")
    load(refresh=True)
    return path


# device.* patches a recipe switches on by id (device.files / device.lepton_env carry their own catalog fields)
TOGGLED_DEVICE = ("device.text_input_window",)


def entry_from_library(g: dict, status: str | None = None, notes: str | None = None,
                       verified: dict | None = None) -> CatalogEntry:
    """A catalog recipe from a library entry (what "Save as known-good" and "Share working config" publish)."""
    import time

    from ..core import library
    from ..patches import base
    from ..patches.overport import DEFAULT_OVERPORT

    package = g["package"]
    r = library.recipe_from_dict(g["recipe"])
    a = g.get("analysis") or {}
    if status is None:  # the game's own status ("works with issues" stays that; untested = works, it's being saved)
        status = r.status if r.status in ("works", "issues") else "works"
    common = dict(package=package, title=g.get("title") or package, status=status,
                  notes=r.notes if notes is None else notes,
                  tested_version=a.get("version") or "", engine=a.get("engine") or "", xr=a.get("xr") or "",
                  verified={k: v for k, v in {"date": time.strftime("%Y-%m-%d"),
                                              "known_good_sha256": (g.get("build") or {}).get("sha256"),
                                              **(verified or {})}.items() if v},
                  source_hint=generic_source_hint(g.get("name", "")))
    if g.get("kind") == "rift":
        env = r.params("pcvr.proton_env").get("env") or ""
        return CatalogEntry(
            **common, kind="rift", quest_package=g.get("quest_package"), as_is=r.as_is,
            pcvr=[p for p in r.patches if p not in ("pcvr.proton_env", "pcvr.proton_tool")],
            pcvr_remove=[p for p in ("pcvr.revive",) if p not in r.patches],
            proton_env=dict(line.split("=", 1) for line in env.splitlines() if "=" in line),
            proton_tool=r.params("pcvr.proton_tool").get("tool") or "")

    def cat(p):
        try:
            return base.get(p)
        except KeyError:
            return None
    return CatalogEntry(
        **common, as_is=r.as_is,
        overport_extra=[p for p in r.patches
                        if (c := cat(p)) and c.category == "overport" and p not in DEFAULT_OVERPORT],
        overport_remove=[p for p in DEFAULT_OVERPORT if p not in r.patches],
        alt_overport=r.alt_patches, use_alt=r.use_alt,
        frame=[p for p in r.patches if (c := cat(p)) and c.category == "frame" and not c.default_on],
        device=[p for p in r.patches if p in TOGGLED_DEVICE],
        adapter={p.split(".", 1)[1]: v.get("value") for p, v in r.patches.items() if p.startswith("adapter.")},
        device_files=r.params("device.files").get("files", {}))


def generic_source_hint(name: str) -> str:
    """The game's title from a download folder name, without what varies between releases: version ("v20022+2.0.22"),
    bracketed notes ("(Pro)", "(English Only)") and release-tag suffixes ("-TAG")."""
    import re

    t = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", name or "")
    t = re.split(r"\s+v\d", t)[0]
    t = re.sub(r"\s+-\s*[A-Za-z0-9]+\s*$", "", t)
    return " ".join(t.split()).strip(" -")


def source_hint_matches(hint: str, folder_name: str) -> bool:
    """Loose match of a recipe's source hint against a download folder name (case, punctuation, version and release
    tags don't matter)."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower())
    want = norm(generic_source_hint(hint))
    return bool(want) and want in norm(folder_name)


def to_yaml(entry: CatalogEntry) -> str:
    return yaml.safe_dump(entry.to_dict(), sort_keys=False, allow_unicode=True, width=110)
