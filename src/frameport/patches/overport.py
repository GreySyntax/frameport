"""overport CLI patches (https://github.com/ovrport/app). These run inside overport; we only choose which ones.

The patch list is discovered dynamically (`overport patches`), and titles are fetched from the overport app's
strings.xml on GitHub (both cached). The table below is the offline fallback and adds what we learned on the
Steam Frame; `default` mirrors overport's recommended set (Patch(..., true) in its sources).
"""
from __future__ import annotations

from ..core.models import Analysis
from .base import Patch, Suggestion, register

# id, title, default, detail
OVERPORT_PATCHES = [
    ("patch_copy_libraries", "Copy overport libraries", True,
     "Adds overport's OpenXR loader dispatcher and platform loader. Required: without it nothing is translated."),
    ("patch_copy_ovrplugin_vrapi", "Copy OVRPlugin if VrApi is present", True,
     "Swaps in an OpenXR OVRPlugin for games that use VrApi through OVRPlugin (older Unity/Unreal)."),
    ("patch_replace_icon_label", "Replace application label and icon", True,
     "Uses the store title/icon (overport image service) for the app label."),
    ("patch_fix_min_android_sdk", "Fix minimal Android SDK", True, "Raises minSdk where the loader needs it."),
    ("patch_generate_config", "Generate overport config", True, "Writes liboverport.config.so with runtime options."),
    ("patch_remove_localized_names", "Remove localized app names", True, "Keeps one label so the title is stable."),
    ("patch_clean_up_frida", "Clean up Frida leftovers in smali", True, "Removes leftovers from dumped/modded APKs."),
    ("patch_oculus_unity", "Patch Oculus detection for Unity", True, "Makes Unity's Oculus checks pass on other runtimes."),
    ("patch_oculus_unreal", "Patch Oculus detection for Unreal", True, "Makes Unreal's Oculus checks pass on other runtimes."),
    ("patch_vr_metadata", "Pico/YVR/Quest metadata", True, "Adds the VR app metadata other launchers expect."),
    ("patch_launcher_entry", "Fix launcher icon entry", True, "Adds a launcher entry point (Lepton additionally needs "
     "category LAUNCHER, see the Frame 'launcher' fix)."),
    ("patch_remove_uses_library", "Remove uses-library", True, "Drops Quest-only shared library requirements."),
    ("patch_fix_unreal_crash", "Fix UE4 crash with Unity stub", True, "Works around a UE4 startup crash."),
    ("patch_meta_xr_audio", "Patch Meta XR Audio", True, "Neutralises Meta XR Audio's Quest-only calls (Unity/Wwise)."),
    ("patch_mark_as_debuggable", "Mark application as debuggable", True,
     "Lets you read logs/attach. Some Unreal games abort under CheckJNI when debuggable; the Frame 'nodebug' fix undoes it."),
    ("patch_mark_allow_backup", "Mark application to allow backup", True, "Allows data backup."),
    ("patch_remove_unreal_force_quit", "Remove Unreal's ForceQuit", False,
     "For Unreal games that close themselves right after starting (e.g. Phantom: Covert Ops). Also disables the in-game Quit."),
    ("patch_force_passthrough", "Force enable passthrough", False,
     "For mixed-reality-only games. On the Frame, passthrough is emulated by the FrameBridge adapter (greyscale cameras)."),
    ("patch_disable_space_warp", "Disable application space warp if used", False,
     "For heavy games; space warp causes artifacts/hangs on non-Quest runtimes (Asgard's Wrath 2, Batman)."),
    ("patch_disable_controller_offset", "Disable controller tracking offset", False,
     "Removes overport's controller pose offset if controllers look misplaced."),
    ("patch_remove_vrapi", "Remove VrApi library", False,
     "Not recommended: breaks games that load VrApi through OVRPlugin."),
]
DEFAULT_OVERPORT = [pid for pid, _, default, _ in OVERPORT_PATCHES if default]


class OverportPatch(Patch):
    category = "overport"
    stage = "overport"

    def __init__(self, pid: str, title: str, default: bool, detail: str):
        self.id, self.title, self.default_on, self.description = pid, title, default, detail

    def detect(self, analysis: Analysis) -> Suggestion | None:
        if self.default_on:
            return Suggestion(True, "overport default.")
        if self.id == "patch_disable_space_warp" and analysis.extra.get("size", 0) > 3 * 2**30:
            return Suggestion(False, "Consider for very heavy games.")
        return None


STRINGS_URL = "https://raw.githubusercontent.com/ovrport/app/HEAD/composeApp/src/commonMain/composeResources/values/strings.xml"


for _pid, _title, _default, _detail in OVERPORT_PATCHES:
    register(OverportPatch(_pid, _title, _default, _detail))


def refresh(list_patches=None, fetch_titles: bool = True) -> list[str]:
    """Register patches the installed overport CLI offers that this table doesn't know yet, and update titles.
    `list_patches` is a callable returning patch ids (tools.overport.list_patches). Returns newly added ids."""
    import re

    from ..core.cache import cached_text
    from .base import REGISTRY

    added = []
    if list_patches is not None:
        try:
            ids = list_patches()
        except Exception:
            ids = []
        for pid in ids:
            if pid not in REGISTRY:
                register(OverportPatch(pid, pid.removeprefix("patch_").replace("_", " ").capitalize(), False,
                                       "New overport patch (not yet described by FramePort)."))
                added.append(pid)
    if fetch_titles:
        text = cached_text("overport-strings.xml", STRINGS_URL, max_age=7 * 86400)
        for pid, title in re.findall(r'<string name="(patch_[a-z0-9_]+)">([^<]+)</string>', text or ""):
            if pid in REGISTRY and REGISTRY[pid].category == "overport":
                REGISTRY[pid].title = title.replace("\\'", "'")
    return added
