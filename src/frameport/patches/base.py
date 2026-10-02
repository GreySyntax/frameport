"""The patch plugin interface.

A patch is one self-contained, optional change. Stages run in this order:
  overport  -> passed to the overport CLI as `--patches=` (see patches/overport.py)
  apk       -> edits the overport output (FrameBridge adapter, manifest fixes, library fixes)
  install   -> files/env written on the Frame at install time (adapter settings, config files)

Each patch can suggest itself from an Analysis (`detect`), applies itself (`apply`) and can report checks
(`validate`). New patches only need a module that calls `register(...)`; nothing else changes.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..apk.workspace import ApkWorkspace
    from ..core.events import Reporter
    from ..core.models import Analysis

STAGES = ("overport", "apk", "install")
CATEGORIES = ("overport", "frame", "adapter", "device", "pcvr")


@dataclass
class Suggestion:
    recommended: bool
    reason: str
    params: dict = field(default_factory=dict)


@dataclass
class Param:
    key: str
    kind: str  # "float" | "int" | "bool" | "str" | "text"
    default: Any
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None


@dataclass
class ApkContext:
    ws: ApkWorkspace
    analysis: Analysis
    params: dict
    reporter: Reporter
    recipe_patches: dict  # full selection, for patches that depend on others
    notes: list[str] = field(default_factory=list)


@dataclass
class InstallContext:
    package: str
    params: dict
    files: dict[str, bytes]  # path relative to Android/data/<pkg>/files -> content
    env: dict[str, str]  # extra Lepton env exports
    adapter_settings: dict[str, Any]


class Patch:
    id: str = ""
    title: str = ""
    description: str = ""
    category: str = "frame"
    stage: str = "apk"
    order: int = 50  # apk-stage patches run in ascending order
    params: list[Param] = []
    conflicts: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    default_on: bool = False  # part of the always-recommended baseline
    experimental: bool = False

    def detect(self, analysis: Analysis) -> Suggestion | None:
        return Suggestion(True, "Recommended for every game.") if self.default_on else None

    def applies(self, analysis: Analysis) -> bool:
        """False when the patch can't matter for this game (wrong engine, no VrApi, ...). The UI hides such patches
        (they stay in the recipe if they are defaults, where they are no-ops)."""
        return True

    not_applicable_reason: str = ""

    def apply(self, ctx: ApkContext) -> bool:  # apk stage; returns True when something changed
        raise NotImplementedError

    def install(self, ctx: InstallContext) -> None:  # install stage
        raise NotImplementedError

    def validate(self, ctx: ApkContext) -> list[tuple[str, bool | None, str]]:
        return []

    def describe(self) -> dict:
        return {"id": self.id, "title": self.title, "description": self.description, "category": self.category,
                "stage": self.stage, "experimental": self.experimental,
                "params": [p.__dict__ for p in self.params], "conflicts": list(self.conflicts)}


REGISTRY: dict[str, Patch] = {}


def register(patch: Patch | type[Patch]) -> Patch:
    instance = patch() if isinstance(patch, type) else patch
    if not instance.id or instance.id in REGISTRY:
        raise ValueError(f"bad or duplicate patch id {instance.id!r}")
    if instance.stage not in STAGES or instance.category not in CATEGORIES:
        raise ValueError(f"{instance.id}: bad stage/category")
    REGISTRY[instance.id] = instance
    return instance


def get(patch_id: str) -> Patch:
    load_all()
    try:
        return REGISTRY[patch_id]
    except KeyError:
        raise KeyError(f"unknown patch {patch_id!r}") from None


def for_game(patch: Patch, analysis: Analysis) -> bool:
    """Quest patches only for Quest games, PC VR (Revive) patches only for Rift games."""
    rift = (analysis.extra or {}).get("kind") == "rift"
    return (patch.category == "pcvr") == rift


def all_patches() -> list[Patch]:
    load_all()
    return sorted(REGISTRY.values(), key=lambda p: (CATEGORIES.index(p.category), p.order, p.id))


_loaded = False


def load_all() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    import pkgutil

    from . import frame, overport, pcvr, settings  # noqa: F401  (registration side effects)

    for mod in pkgutil.iter_modules(frame.__path__):
        importlib.import_module(f"{frame.__name__}.{mod.name}")


def simple(patch_id: str, **attrs) -> Callable[[type], type]:
    """Class decorator: set attributes and register."""

    def wrap(cls):
        cls.id = patch_id
        for k, v in attrs.items():
            setattr(cls, k, v)
        register(cls)
        return cls

    return wrap
