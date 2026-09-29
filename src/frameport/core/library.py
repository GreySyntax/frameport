"""Persistent app state: the games the user added, their confirmed recipes, builds and install results."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

from .models import Analysis, Recipe
from .paths import user_data_dir

_lock = threading.Lock()


def _path() -> Path:
    return user_data_dir() / "library.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"games": {}, "settings": {}}


def save(data: dict) -> None:
    with _lock:
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(_path())


def upsert_game(package: str, **fields) -> dict:
    data = load()
    entry = data["games"].setdefault(package, {"package": package, "added": time.time()})
    entry.update(fields)
    save(data)
    return entry


def game(package: str) -> dict | None:
    return load()["games"].get(package)


def games() -> list[dict]:
    return sorted(load()["games"].values(), key=lambda g: (g.get("title") or g["package"]).lower())


def remove_game(package: str) -> None:
    data = load()
    data["games"].pop(package, None)
    save(data)


def setting(key: str, default=None):
    return load().get("settings", {}).get(key, default)


def set_setting(key: str, value) -> None:
    data = load()
    data.setdefault("settings", {})[key] = value
    save(data)


def recipe_to_dict(r: Recipe) -> dict:
    return asdict(r)


def recipe_from_dict(d: dict) -> Recipe:
    return Recipe(**{k: v for k, v in d.items() if k in Recipe.__dataclass_fields__})


def analysis_from_dict(d: dict) -> Analysis:
    return Analysis(**{k: v for k, v in d.items() if k in Analysis.__dataclass_fields__})
