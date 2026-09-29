"""FrameBridge adapter settings and device-side files, exposed as patches so the UI can toggle them.

Adapter settings end up in lib/<abi>/libframe_settings.so (build time) and in <install>/settings.conf +
Android/data/<pkg>/files/framebridge.conf on the Frame (can be changed later without re-patching).
The GL shim reads framebridge.conf too (gl_hide_multiview).
"""
from __future__ import annotations

from .base import InstallContext, Param, Patch, Suggestion, register

# Always written (in this order) so every build carries explicit values.
BASE_SETTINGS = {"scale": 1.0, "foveation_fix": 1, "controller_fix": 1}

# key, kind, default, title, description
SETTINGS = [
    ("scale", "float", 1.0, "Resolution scale",
     "Multiplies the recommended eye-buffer width and height (0.5–2.0). 1.5 ≈ 2.25× pixels."),
    ("foveation_fix", "int", 1, "Hide Quest foveation", "Hides Quest foveation extensions the Frame runtime lacks."),
    ("controller_fix", "int", 1, "Report Touch controllers",
     "Reports Frame controllers as Oculus Touch and hides synthetic hand tracking. 0 for hand-tracking games (Silhouette)."),
    ("swapchain_fix", "int", 1, "Swapchain format fallback", "Retry rejected GLES formats/MSAA with sRGB, samples=1."),
    ("layer_fix", "int", 1, "Drop invalid layers", "Drop layers whose swapchain failed or whose extension isn't enabled."),
    ("passthrough_emul", "int", 1, "Emulate passthrough", "XR_FB_passthrough via ALPHA_BLEND (Frame greyscale cameras)."),
    ("flip_emul", "int", 1, "Emulate flipped quads",
     "Blit quads flagged XrCompositionLayerImageLayoutFB VERTICAL_FLIP upside down (Vulkan; AC Nexus UI)."),
    ("scene_emul", "int", 0, "Emulate Meta scene (room)",
     "Fake XR_FB_scene/spatial entities: a guardian-sized room with floor, ceiling and four walls (Demeter)."),
    ("scene_height", "float", 2.5, "Emulated room height (m)", "Ceiling height for scene_emul."),
    ("scene_width", "float", 0.0, "Emulated room width (m)", "Override the guardian width (0 = use guardian, min 1.5 m)."),
    ("scene_depth", "float", 0.0, "Emulated room depth (m)", "Override the guardian depth (0 = use guardian, min 1.5 m)."),
    ("swap_eyes", "int", 0, "Swap eyes", "Swap left/right views (diagnostic)."),
    ("strip_depth", "int", 0, "Strip depth layers", "Remove XR_KHR_composition_layer_depth chains (diagnostic)."),
    ("mutable_fix", "int", 0, "Mutable swapchain fix", "Experimental Vulkan mutable-format workaround."),
    ("respace_kick", "int", 0, "Re-create reference space", "Recreate spaces after the first frames (diagnostic)."),
    ("flip_quads", "int", 0, "Rotate quads 180°", "Old AC Nexus workaround (quads are single-sided; prefer flip_emul)."),
    ("gl_hide_multiview", "int", 1, "GL shim: hide multiview",
     "GL shim only: hide GL_OVR_multiview so all passes use single-view shaders (Path of the Warrior)."),
]


class AdapterSetting(Patch):
    category = "adapter"
    stage = "install"

    def __init__(self, key, kind, default, title, description):
        self.id = f"adapter.{key}"
        self.key = key
        self.title = title
        self.description = description
        self.params = [Param("value", kind, default, description)]
        self.default = default

    def detect(self, a):
        if self.key == "controller_fix" and a.extra.get("hand_tracking_only"):
            return Suggestion(True, "Hand-tracking game: pass hands through.", {"value": 0})
        return None

    def install(self, ctx: InstallContext) -> None:
        pass  # collected by adapter_settings()


def adapter_settings(recipe_patches: dict) -> dict:
    """Ordered settings for libframe_settings.so / settings.conf: base keys first, then enabled overrides in
    selection order."""
    out = dict(BASE_SETTINGS)
    for pid, params in recipe_patches.items():
        if pid.startswith("adapter."):
            key = pid.split(".", 1)[1]
            spec = next((s for s in SETTINGS if s[0] == key), None)
            value = (params or {}).get("value", spec[2] if spec else 1)
            if spec and spec[1] == "float":
                value = float(value)
            elif spec and spec[1] == "int":
                value = int(value)
            out[key] = value
    return out


class DeviceFiles(Patch):
    id = "device.files"
    title = "Game config files"
    description = ("Writes files into the game's Android/data/<package>/files/ on the Frame, e.g. The Climb 2's "
                   "user.cfg with r_variable_rate_shading = 0 (CryEngine VRS is unsupported on the Frame).")
    category = "device"
    stage = "install"
    params = [Param("files", "text", {}, "path relative to files/ -> content")]

    def install(self, ctx: InstallContext) -> None:
        for rel, content in (ctx.params.get("files") or {}).items():
            ctx.files[rel] = content.encode() if isinstance(content, str) else content


class LeptonEnv(Patch):
    id = "device.lepton_env"
    title = "Extra Lepton environment"
    description = "Environment variables exported by the launcher (e.g. VK_INSTANCE_LAYERS=\"\" to test without Valve's layers)."
    category = "device"
    stage = "install"
    params = [Param("env", "text", {}, "NAME -> value")]

    def install(self, ctx: InstallContext) -> None:
        ctx.env.update({k: str(v) for k, v in (ctx.params.get("env") or {}).items()})


for _spec in SETTINGS:
    register(AdapterSetting(*_spec))
register(DeviceFiles)
register(LeptonEnv)
