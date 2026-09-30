"""VrApi→OpenXR bridge and the GL shim, for engines that call libvrapi.so directly."""
from __future__ import annotations

from ...analysis import elf
from ..base import ApkContext, Patch, Suggestion, register
from . import artifact

SHIM = "libglshim.so"


class VrApiBridge(Patch):
    id = "frame.vrapi_bridge"
    title = "VrApi → OpenXR bridge"
    description = (
        "Replaces libvrapi.so with a VrApi→OpenXR bridge (Android-XR-Bridge/OVRPort fork, GPL-3.0, patched for the "
        "Frame: GLES sessions, cylinder→quad layers, sRGB format fallback, 30 s VR-mode deadline, emulated time "
        "conversion). Needed when the engine calls VrApi itself instead of through OVRPlugin (e.g. The Climb 2, Path of "
        "the Warrior); overport cannot translate those."
    )
    order = 60
    experimental = True

    def detect(self, a):
        if a.direct_vrapi and "arm64-v8a" in a.abis:
            return Suggestion(True, "The engine calls libvrapi.so directly; overport can't translate it.")
        return None

    def applies(self, a):
        return "libvrapi.so" in a.libs and "arm64-v8a" in a.abis

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        if ws.abi != "arm64-v8a" or not ws.has(ws.lib("libvrapi.so")):
            return False
        bridge = artifact(ws.abi, "libvrapi.so")
        if ws.read_lib("libvrapi.so") == bridge:
            return False
        ws.put(ws.lib("libvrapi.so"), bridge)
        return True


class GlShim(Patch):
    id = "frame.gl_shim"
    title = "GL shim (Mesa GLSL compatibility)"
    description = (
        "Loads libglshim.so ahead of libEGL in every engine library that fetches GL through eglGetProcAddress. Fixes "
        "Quest-only leniency that Mesa/Zink rejects: #pragma before #extension, implicit int/float conversions "
        "(enables GL_EXT_shader_implicit_conversions), and multiview shaders used on single-view framebuffers "
        "(hides GL_OVR_multiview; gl_hide_multiview=0 turns that off). Logs every failed shader with its source "
        "lines (logcat tag GLShim). Symptom: black screen with working audio in a GLES game."
    )
    order = 61
    requires = ("frame.vrapi_bridge",)
    experimental = True

    def detect(self, a):
        if a.direct_vrapi and a.uses_glad_gl and "GLES" in a.graphics and "arm64-v8a" in a.abis:
            return Suggestion(True, "GLES engine resolving GL through eglGetProcAddress (GLAD) on the VrApi bridge.")
        return None

    def applies(self, a):
        return a.direct_vrapi and "GLES" in a.graphics and "arm64-v8a" in a.abis

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        if ws.abi != "arm64-v8a":
            return False
        shim = artifact(ws.abi, SHIM)
        if ws.has(ws.lib(SHIM)):
            if ws.read_lib(SHIM) == shim:
                return False
            ws.put(ws.lib(SHIM), shim)
            return True
        skip = {"libvrapi.so", "libOVRPlugin.so", SHIM}
        for lib in ws.libs():
            if lib in skip or lib.startswith("libopenxr_loader"):
                continue
            data = ws.read_lib(lib)
            if not elf.is_elf(data) or "eglGetProcAddress" not in elf.dyn_symbols(data, False):
                continue
            ws.put(ws.lib(lib), elf.add_needed(data, SHIM))
            ctx.notes.append(f"GL shim loaded by {lib}")
        ws.put(ws.lib(SHIM), shim)
        return True


register(VrApiBridge)
register(GlShim)
