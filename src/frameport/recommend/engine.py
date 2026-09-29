"""Turn an Analysis into a suggested Recipe: catalog recipe when known, patch heuristics otherwise.

Every enabled patch carries a human-readable reason (shown next to its toggle in the UI).
"""
from __future__ import annotations

from ..core.models import Analysis, Recipe
from ..patches import base
from . import catalog


def suggest(analysis: Analysis) -> Recipe:
    entry = catalog.lookup(analysis.package)
    recipe = Recipe(analysis.package, title=analysis.label)
    # 1. heuristics / defaults from every patch module
    for patch in base.all_patches():
        s = patch.detect(analysis)
        if s and s.recommended:
            recipe.patches[patch.id] = dict(s.params)
            recipe.reasons[patch.id] = s.reason
    # 2. known-good catalog recipe overrides heuristics
    if entry:
        recipe.source = f"catalog ({entry.origin})"
        recipe.status, recipe.notes, recipe.title = entry.status, entry.notes, entry.title or recipe.title
        why = f"Known-good recipe for {entry.title} (tested {entry.verified.get('date', '?')})."
        for pid in entry.overport_remove:
            recipe.patches.pop(pid, None)
        for pid in entry.overport_extra + entry.frame:
            recipe.patches.setdefault(pid, {})
            recipe.reasons[pid] = why
        # heuristic-only suggestions the catalog didn't choose are dropped for exact reproducibility
        chosen = set(entry.overport_extra) | set(entry.frame)
        for pid in list(recipe.patches):
            p = base.get(pid)
            if pid not in chosen and not p.default_on and p.category in ("overport", "frame"):
                recipe.patches.pop(pid)
                recipe.reasons.pop(pid, None)
        for pid in entry.frame_remove:
            recipe.patches.pop(pid, None)
        for key, value in entry.adapter.items():
            recipe.patches[f"adapter.{key}"] = {"value": value}
            recipe.reasons[f"adapter.{key}"] = why
        if entry.device_files:
            recipe.patches["device.files"] = {"files": dict(entry.device_files)}
            recipe.reasons["device.files"] = why
        if entry.lepton_env:
            recipe.patches["device.lepton_env"] = {"env": dict(entry.lepton_env)}
        recipe.alt_patches = list(entry.alt_overport)
        recipe.use_alt = entry.use_alt
    else:
        recipe.source = "heuristics"
        if analysis.engine == "Unreal":
            recipe.alt_patches = ["patch_remove_unreal_force_quit"]  # build a fallback in case it quits itself
        if analysis.only_32bit:
            recipe.status = "unsupported"
            recipe.notes = "32-bit only: the Steam Frame has no AArch32 support. Consider the PC (Rift) version via Revive."
    # requirements
    for pid in list(recipe.patches):
        for req in base.get(pid).requires:
            if req not in recipe.patches:
                recipe.patches[req] = {}
                recipe.reasons[req] = f"Required by {pid}."
    return recipe


def warnings(recipe: Recipe) -> list[str]:
    out = []
    for pid in recipe.patches:
        p = base.get(pid)
        for c in p.conflicts:
            if c in recipe.patches:
                out.append(f"{p.title} conflicts with {base.get(c).title}.")
        for r in p.requires:
            if r not in recipe.patches:
                out.append(f"{p.title} needs {base.get(r).title}.")
    if "patch_copy_libraries" not in recipe.patches:
        out.append("Without 'Copy overport libraries' nothing is translated to OpenXR.")
    if "frame.adapter" not in recipe.patches:
        out.append("Without the FrameBridge adapter most games fail on the Frame runtime.")
    return out
