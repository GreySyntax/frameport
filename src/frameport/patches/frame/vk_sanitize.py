"""Vulkan shim that drops invalid pNext pointers from render-pass create infos (native/vkshim)."""
from __future__ import annotations

from ...analysis import elf
from ..base import ApkContext, Patch, Suggestion, register
from . import artifact

SHIM = "libfp_vk.so"  # not longer than "libvulkan.so": the engine's dlopen string is rewritten in place
VULKAN = "libvulkan.so"
ENGINE_LIBS = ("libUE4.so", "libUnreal.so")


class VulkanSanitize(Patch):
    id = "frame.vk_sanitize"
    title = "Vulkan: drop invalid render-pass pointers"
    description = ("Some engines leave the pNext of unused Vulkan attachment references uninitialized. The Frame's "
                   "driver never reads it, but Lepton always loads Steam's Fossilize shader-cache layer, which follows "
                   "it and crashes on the first frame (SIGSEGV in libVkLayer_fossilize.so from FVulkanRenderPass, "
                   "e.g. Deadpool VR). Loads Vulkan through a small shim that keeps valid pointers and drops only "
                   "unreadable ones or ones pointing at the wrong structure type.")
    order = 72
    default_on = True

    def detect(self, a):
        if a.engine == "Unreal":
            return Suggestion(True, "Unreal game: applied automatically when the engine loads Vulkan by name; keeps "
                                    "Lepton's Fossilize layer from crashing on uninitialized pointers (e.g. Deadpool VR).")
        return None

    def applies(self, a):
        return a.engine == "Unreal"

    def apply(self, ctx: ApkContext) -> bool:
        ws = ctx.ws
        if ws.abi != "arm64-v8a":
            return False
        changed = False
        for lib in ENGINE_LIBS:
            if not ws.has(ws.lib(lib)):
                continue
            data, count = elf.replace_rodata_string(ws.read_lib(lib), VULKAN, SHIM)
            if count:
                ws.put(ws.lib(lib), data)
                ctx.notes.append(f"{lib} loads Vulkan through {SHIM}")
                changed = True
        if changed:
            shim = artifact(ws.abi, SHIM)
            if not ws.has(ws.lib(SHIM)) or ws.read_lib(SHIM) != shim:
                ws.put(ws.lib(SHIM), shim)
        return changed

    def validate(self, ctx: ApkContext):
        ws = ctx.ws
        return [("Vulkan shim present", ws.has(ws.lib(SHIM)), SHIM)]


register(VulkanSanitize)
