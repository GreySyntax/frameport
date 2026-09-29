"""Known-good per-game recipes.

Sources, highest priority first:
  1. user entries   <user data>/catalog/games/*.yaml  (written by "Save as known-good" after a successful test)
  2. remote catalog FRAMEPORT_CATALOG_URL (a folder URL serving index.json + <package>.yaml), cached; lets the
                    catalog be updated without a new app release
  3. bundled        catalog/games/*.yaml in this repo
"""
from __future__ import annotations

import os
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
    adapter: dict = field(default_factory=dict)
    device_files: dict = field(default_factory=dict)
    lepton_env: dict = field(default_factory=dict)
    pcvr_alternative: str | None = None
    verified: dict = field(default_factory=dict)
    source_hint: str = ""
    origin: str = "bundled"

    @classmethod
    def from_dict(cls, d: dict, origin: str) -> "CatalogEntry":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known, origin=origin)

    def to_dict(self) -> dict:
        out = {}
        for k, f in self.__dataclass_fields__.items():
            v = getattr(self, k)
            if k == "origin" or v in (None, "", [], {}, False):
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


def _load_remote() -> dict[str, CatalogEntry]:
    base = os.environ.get("FRAMEPORT_CATALOG_URL")
    if not base:
        return {}
    base = base.rstrip("/") + "/"
    index = cache.cached_json("catalog-index.json", base + "index.json", max_age=6 * 3600, fallback=[])
    out = {}
    for pkg in index or []:
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
        entries.update(_load_dir(user_catalog_dir(), "user"))
        _cache = entries
    return _cache


def lookup(package: str) -> CatalogEntry | None:
    return load().get(package)


def save_user_entry(entry: CatalogEntry) -> Path:
    path = user_catalog_dir() / f"{entry.package}.yaml"
    path.write_text(yaml.safe_dump(entry.to_dict(), sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
    load(refresh=True)
    return path


def write_index(folder: Path) -> Path:
    """Write index.json for publishing a folder of recipes as a remote catalog."""
    import json

    pkgs = sorted(f.stem for f in folder.glob("*.yaml"))
    path = folder / "index.json"
    path.write_text(json.dumps(pkgs, indent=1))
    return path
