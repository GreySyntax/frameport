"""Predicates shared by patch `applies()` / `detect()` rules."""
from __future__ import annotations

from ..core.models import Analysis


def is_unreal(a: Analysis) -> bool:
    return a.engine == "Unreal"


def is_unity(a: Analysis) -> bool:
    return a.engine == "Unity"


def has_vrapi(a: Analysis) -> bool:
    return "libvrapi.so" in a.libs


def is_gles(a: Analysis) -> bool:
    return "GLES" in a.graphics


def is_vulkan(a: Analysis) -> bool:
    return a.graphics.startswith("Vulkan")


def arm64(a: Analysis) -> bool:
    return "arm64-v8a" in a.abis


def meta_audio_libs(a: Analysis) -> list[str]:
    return [l for l in a.libs if l.lower().startswith(("libmetaxraudio", "libovraudio", "libaksoundengine", "libovravatar"))]


def uses_scene(a: Analysis) -> bool:
    perms = a.extra.get("meta_permissions_used") or a.meta_permissions
    return any(p.endswith(("USE_SCENE", "USE_ANCHOR_API")) for p in perms)


def needs_scene(a: Analysis) -> bool:
    """Mixed-reality-only game that builds its world from the room model."""
    perms = a.extra.get("meta_permissions_used") or a.meta_permissions
    return bool(a.extra.get("mr_only")) and any(p.endswith("USE_SCENE") for p in perms)


def unreal_version(a: Analysis) -> tuple[int, int] | None:
    v = a.extra.get("unreal_version")
    try:
        major, minor = v.split(".")[:2]
        return int(major), int(minor)
    except (AttributeError, ValueError):
        return None
