"""Turn an Analysis into a suggested Recipe: catalog recipe when known, patch heuristics otherwise.

Every enabled patch carries a human-readable reason (shown next to its toggle in the UI).
"""
from __future__ import annotations

from ..core.models import Analysis, Recipe
from ..patches import base
from . import catalog


def suggest(analysis: Analysis, use_catalog: bool = True) -> Recipe:
    entry = catalog.lookup(analysis.package) if use_catalog else None
    recipe = Recipe(analysis.package, title=analysis.label)
    # 1. heuristics / defaults from every patch module
    rift = (analysis.extra or {}).get("kind") == "rift"
    for patch in base.all_patches():
        if not base.for_game(patch, analysis):
            continue  # Quest patches for Quest games, PC VR (Revive) options for Rift games
        if not patch.default_on and not patch.applies(analysis):
            continue  # irrelevant for this game (engine, XR API, graphics API, ...)
        s = patch.detect(analysis)
        if s and s.recommended:
            recipe.patches[patch.id] = dict(s.params)
            recipe.reasons[patch.id] = s.reason
    # 2. known-good catalog recipe overrides heuristics
    if rift:
        _rift_recipe(analysis, recipe, entry)
    elif entry:
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
    if not rift and not entry and (analysis.extra or {}).get("frame_patched"):
        # already has FramePort's adapter (e.g. a PATCHED/ build): installing it unchanged is the safe default
        recipe.as_is = True
        recipe.notes = (recipe.notes + " " if recipe.notes else "") + \
            "This APK is already patched for the Frame, so FramePort installs it as it is."
    # requirements
    for pid in list(recipe.patches):
        for req in base.get(pid).requires:
            if req not in recipe.patches:
                recipe.patches[req] = {}
                recipe.reasons[req] = f"Required by {pid}."
    return recipe


def _rift_recipe(analysis: Analysis, recipe: Recipe, entry) -> None:
    extra = analysis.extra
    if entry:
        recipe.source = f"catalog ({entry.origin})"
        recipe.status, recipe.notes, recipe.title = entry.status, entry.notes, entry.title or recipe.title
        why = f"Known-good recipe for {entry.title} (tested {entry.verified.get('date', '?')})."
        for pid in entry.pcvr:
            recipe.patches.setdefault(pid, {})
            recipe.reasons[pid] = why
        for pid in entry.pcvr_remove:
            recipe.patches.pop(pid, None)
        if entry.proton_env:
            recipe.patches["pcvr.proton_env"] = {"env": "\n".join(f"{k}={v}" for k, v in entry.proton_env.items())}
            recipe.reasons["pcvr.proton_env"] = why
        if entry.proton_tool:
            recipe.patches["pcvr.proton_tool"] = {"tool": entry.proton_tool}
            recipe.reasons["pcvr.proton_tool"] = why
        recipe.as_is = entry.as_is
        return
    recipe.source = "heuristics"
    recipe.as_is = True  # the repacks are pre-patched to run directly; Revive is an opt-in fallback (see pcvr.Revive)
    notes = []
    if extra.get("platform_sdk"):
        notes.append("Uses the Oculus Platform SDK (entitlement check): normally it needs the Oculus app running with a "
                     "license you own, so it may not start on the headset.")
    if extra.get("openxr_native"):
        notes.append("OpenXR-native: runs on SteamVR / the Frame without Revive.")
    if analysis.abis and analysis.abis[0] not in ("x86", "x86_64"):
        recipe.status = "unsupported"
        notes.append(f"Unexpected executable type {analysis.abis[0]}.")
    recipe.notes = " ".join(notes)


def warnings(recipe: Recipe) -> list[str]:
    out = []
    if recipe.package.startswith("rift."):
        for pid in recipe.patches:
            for r in base.get(pid).requires:
                if r not in recipe.patches:
                    out.append(f"{base.get(pid).title} needs {base.get(r).title}.")
        return out
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


def visible_patches(analysis: Analysis, recipe: Recipe | None = None) -> tuple[list, list]:
    """(relevant, hidden) patches for this game. Enabled patches are always shown, so a recipe never hides a choice."""
    shown, hidden = [], []
    for p in base.all_patches():
        if not base.for_game(p, analysis):
            continue  # other kind of game entirely (Quest vs Rift): not even offered under "show all"
        on = recipe is not None and p.id in recipe.patches
        (shown if on and not p.default_on or p.applies(analysis) else hidden).append(p)
    return shown, hidden
