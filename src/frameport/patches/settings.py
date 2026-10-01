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
     "Reports Frame controllers as Oculus Touch and hides synthetic hand tracking. Set 0 for games that require hand "
     "tracking (e.g. Silhouette)."),
    ("swapchain_fix", "int", 1, "Swapchain format fallback", "Retry rejected GLES formats/MSAA with sRGB, samples=1."),
    ("layer_fix", "int", 1, "Drop invalid layers", "Drop layers whose swapchain failed or whose extension isn't enabled."),
    ("passthrough_emul", "int", 1, "Emulate passthrough", "XR_FB_passthrough via ALPHA_BLEND (Frame greyscale cameras)."),
    ("flip_emul", "int", 1, "Emulate flipped quads",
     "Blit quads flagged XrCompositionLayerImageLayoutFB VERTICAL_FLIP upside down (Vulkan). Fixes UI panels and "
     "text that show upside down (e.g. Assassin's Creed Nexus)."),
    ("cylinder_strips", "int", 1, "Show curved panels",
     "Cylinder layers (curved menus and movie screens, e.g. 4XVR), which the Frame's runtime lacks, are shown as a "
     "few flat strips along the curve. 0 = drop them. 360° (equirect) layers can't be shown on the Frame."),
    ("scene_emul", "int", 0, "Emulate Meta scene (room)",
     "Fake XR_FB_scene/spatial entities: a guardian-sized room with floor, ceiling and four walls, for mixed-reality "
     "games that build their level from the room (e.g. Demeter)."),
    ("controller_models", "int", 0, "Steam Frame controller models",
     "Games that ask the headset for its controller models (Meta's runtime controller models, XR_FB_render_model) get "
     "the Steam Frame controllers instead of Quest Touch controllers. The models come from the Frame's own SteamVR and "
     "are converted on the Frame at install time. Games that ship their own controller meshes aren't affected. Turning "
     "it on needs a rebuild (it adds a small library in front of overport's loader)."),
    ("scene_height", "float", 2.5, "Emulated room height (m)", "Ceiling height for scene_emul."),
    ("scene_width", "float", 0.0, "Emulated room width (m)", "Override the guardian width (0 = use guardian, min 1.5 m)."),
    ("scene_depth", "float", 0.0, "Emulated room depth (m)", "Override the guardian depth (0 = use guardian, min 1.5 m)."),
    ("swap_eyes", "int", 0, "Swap eyes", "Swap left/right views (diagnostic)."),
    ("strip_depth", "int", 0, "Strip depth layers", "Remove XR_KHR_composition_layer_depth chains (diagnostic)."),
    ("mutable_fix", "int", 0, "Mutable swapchain fix", "Experimental Vulkan mutable-format workaround."),
    ("respace_kick", "int", 0, "Re-create reference space", "Recreate spaces after the first frames (diagnostic)."),
    ("flip_quads", "int", 0, "Rotate quads 180°", "Older workaround for upside-down quads: rotates them 180° (quads are single-sided; prefer "
     "flip_emul)."),
    ("gl_hide_multiview", "int", 1, "GL shim: hide multiview",
     "GL shim only: hide GL_OVR_multiview so all passes use single-view shaders. For GLES games whose multiview "
     "shaders fail on single-view render targets (e.g. Path of the Warrior)."),
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
        from .applicability import needs_scene, uses_render_models

        if self.key == "controller_fix" and a.extra.get("hand_tracking_only"):
            return Suggestion(True, "Hand tracking is required by the game: pass hands through instead of reporting "
                                    "Touch controllers (e.g. Silhouette).", {"value": 0})
        if self.key == "controller_models" and uses_render_models(a):
            return Suggestion(True, "The game asks the headset for its controller models: show Steam Frame controllers "
                                    "instead of Quest Touch controllers.", {"value": 1})
        if self.key == "scene_emul" and needs_scene(a):
            return Suggestion(True, "Mixed-reality game that builds its level from the room model: emulate a "
                                    "guardian-sized room (e.g. Demeter).", {"value": 1})
        return None

    def applies(self, a):
        from . import applicability as ap

        rules = {
            "scene_emul": lambda a: ap.uses_scene(a) or a.extra.get("mr_only"),
            "scene_height": lambda a: ap.uses_scene(a) or a.extra.get("mr_only"),
            "scene_width": lambda a: ap.uses_scene(a) or a.extra.get("mr_only"),
            "scene_depth": lambda a: ap.uses_scene(a) or a.extra.get("mr_only"),
            "passthrough_emul": lambda a: a.extra.get("mr_only") or "com.oculus.feature.PASSTHROUGH" in (a.extra.get("features") or {}),
            "flip_emul": ap.is_vulkan,
            "flip_quads": ap.is_vulkan,
            "mutable_fix": ap.is_vulkan,
            "swapchain_fix": ap.is_gles,
            "gl_hide_multiview": lambda a: a.direct_vrapi and ap.is_gles(a),
            "controller_models": ap.may_use_render_models,
        }
        rule = rules.get(self.key)
        return bool(rule(a)) if rule else True

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
    description = ("Writes config files into the game's Android/data/<package>/files/ on the Frame, to turn off engine "
                   "features the Frame doesn't support (e.g. a CryEngine user.cfg with r_variable_rate_shading = 0, "
                   "as The Climb 2 needs).")
    category = "device"
    stage = "install"
    params = [Param("files", "text", {}, "path relative to files/ -> content")]

    def detect(self, a):
        if a.engine == "CryEngine" and a.graphics.startswith("Vulkan") or (a.engine == "CryEngine" and "libCryRenderVulkan.so" in a.libs):
            return Suggestion(True, "CryEngine: variable-rate shading isn't supported on the Frame, so user.cfg turns "
                                    "it off (r_variable_rate_shading = 0; e.g. The Climb 2).",
                              {"files": {"user.cfg": "r_variable_rate_shading = 0\n"}})
        return None

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
