"""Unity 2017-era built-in Oculus support starts its VR device only on a Quest/Go: libunity.so asks Android's package
manager for com.oculus.systemactivities (Meta's system UI) and, when the lookup fails, quietly falls back to its "None"
VR device. Lepton's Android has no such package, so the game ran as a plain 2D app that Lepton never shows (only the
Android home screen, e.g. Accounting+, Unity 2017.4). The package name in libunity.so is pointed at "android" (always
installed): a same-length, in-place string edit; everything else about the check stays as it is."""
from __future__ import annotations

from ...analysis import elf
from ..base import ApkContext, Patch, Suggestion, register
from . import artifact

PACKAGE = "com.oculus.systemactivities"
ALWAYS_THERE = "android"
# Unity's legacy frame loop never calls ovrp_WaitToBeginFrame: its ovrp_Update2 lookup goes to native/ovrpshim
SHIM = "libfp_ovrp.so"
UPDATE, SHIM_UPDATE = "ovrp_Update2", "fpov_Update2"


class UnityOculusCheck(Patch):
    id = "frame.unity_oculus_check"
    title = "Unity 2017: start VR without Meta's system apps"
    description = ("Unity's older built-in Oculus support (Unity 2017–2018) only starts VR when Android has Meta's "
                   "com.oculus.systemactivities package; without it the game runs as a hidden 2D app (you see the "
                   "Android home screen, e.g. Accounting+). Points that package name in libunity.so at \"android\", "
                   "which always exists, and adds the frame wait its legacy frame loop never makes (libfp_ovrp.so "
                   "calls ovrp_WaitToBeginFrame before ovrp_Update2; without it no frame starts and the dashboard "
                   "freezes).")
    order = 45

    def applies(self, a):
        x = a.extra or {}
        major = int(str(x.get("unity_version") or "0").split(".")[0] or 0)
        # Unity 2019+ games run without it (e.g. Lucky's Tale 2019.4): their builds stay as they are
        return a.engine == "Unity" and "libOVRPlugin.so" in a.libs and bool(x.get("unity_oculus_check")) \
            and 0 < major < 2019

    def detect(self, a):
        if self.applies(a):
            return Suggestion(True, "Older Unity with built-in Oculus support: it checks for Meta's system apps "
                                    "before starting VR, and its frame loop never waits for the next frame "
                                    "(e.g. Accounting+).")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        name = ws.lib("libunity.so")
        if not ws.has(name):
            return False
        data, count = elf.replace_rodata_string(ws.read(name), PACKAGE, ALWAYS_THERE)
        if not count:
            return False
        data, loops = elf.replace_rodata_string(data, UPDATE, SHIM_UPDATE)
        plugin = ws.lib("libOVRPlugin.so")
        if loops and ws.abi == "arm64-v8a" and ws.has(plugin):
            ovrp = ws.read(plugin)
            if SHIM.encode() not in ovrp:
                ws.put(plugin, elf.add_needed(ovrp, SHIM))
            ws.put(ws.lib(SHIM), artifact(ws.abi, SHIM))
            ctx.notes.append(f"libunity.so: {UPDATE} -> {SHIM_UPDATE} ({SHIM} waits for each frame)")
        ws.put(name, data)
        ctx.notes.append(f"libunity.so: {PACKAGE} -> {ALWAYS_THERE} ({count}x)")
        return True


register(UnityOculusCheck)
