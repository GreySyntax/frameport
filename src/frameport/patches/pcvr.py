"""PC VR (Oculus Rift) options, shown in the UI as patches like the overport ones.

They don't edit the game: they decide how FramePort launches it. On this PC the Steam shortcut runs
ReviveInjector.exe (Revive's OpenXR backend by default); on the Frame the launcher runs the same injector under
Proton (ARM64, x86 emulated by FEX inside Proton), which bridges OpenXR to the Frame runtime via wineopenxr.
"""
from __future__ import annotations

from .base import Param, Patch, Suggestion, register


def _rift(analysis) -> bool:
    return (analysis.extra or {}).get("kind") == "rift"


class _PcvrPatch(Patch):
    category = "pcvr"
    stage = "install"

    def applies(self, analysis) -> bool:
        return _rift(analysis)


class Revive(_PcvrPatch):
    id = "pcvr.revive"
    title = "Revive (Oculus LibOVR → OpenXR)"
    description = ("Launch the game through Revive's injector, which translates the Oculus PC SDK (LibOVR) to OpenXR. "
                   "Needed by every Oculus Rift game that isn't OpenXR-native. FramePort downloads Revive itself.")
    order = 10

    def detect(self, analysis):
        # Oculus/LibOVR games need Revive to get VR (the bare exe runs flat). Off for games with no Oculus code, which
        # SteamVR (PC) and the Frame's wineopenxr run directly (e.g. Rick and Morty, handled by its catalog recipe).
        if not _rift(analysis):
            return None
        if analysis.extra.get("frame_native"):
            return Suggestion(False, "No Oculus code: SteamVR / the Frame run it directly, without Revive.")
        return Suggestion(True, "Oculus/LibOVR game: Revive translates it to SteamVR/OpenXR (without it it runs flat).")


class ReviveOpenVR(_PcvrPatch):
    id = "pcvr.revive_openvr"
    title = "Revive: SteamVR (OpenVR) backend on PC"
    description = ("On this PC, launch the game through Revive's OpenVR backend, which talks straight to SteamVR — the "
                   "original, most reliable Revive path. On by default for PC installs. Turn it off to use Revive's "
                   "newer OpenXR backend instead. Ignored on the Frame (which always uses OpenXR).")
    order = 20
    requires = ("pcvr.revive",)

    def detect(self, analysis):
        if not _rift(analysis):
            return None
        if analysis.extra.get("frame_native"):
            return Suggestion(False, "Runs on SteamVR directly; no Revive needed.")
        return Suggestion(True, "PC: Revive's SteamVR/OpenVR backend is the most reliable path.")


class XrTimefix(_PcvrPatch):
    id = "pcvr.xr_timefix"
    title = "Frame OpenXR compatibility layer"
    description = ("Loads FramePort's OpenXR layer under Proton on the Frame. The Frame's SteamVR runtime only accepts "
                   "OpenXR 1.0 apps, but Proton's VR helper asks for 1.1, so without the layer VR never starts (the "
                   "game shows as a flat window, or Revive fails with 'Unable to load LibOVRRT DLL'); the layer "
                   "retries as 1.0. It also emulates xrConvertTimespecTimeToTimeKHR if a runtime refuses it. "
                   "Ignored on this PC.")
    order = 25

    def detect(self, analysis):
        if _rift(analysis):
            return Suggestion(True, "The Frame's OpenXR runtime rejects Proton's OpenXR 1.1 request without it.")
        return None


class NoCrashReporter(_PcvrPatch):
    id = "pcvr.no_crash_reporter"
    title = "No Unreal crash reporter"
    description = ("Unreal games start CrashReportClient when they crash, which leaves a crash dialog instead of simply "
                   "closing. This passes -nocrashreports to the game and, on the Frame, renames the game's copy of "
                   "CrashReportClient.exe so it can't start (your game files on this PC aren't changed).")
    order = 15

    def detect(self, analysis):
        if _rift(analysis) and analysis.engine == "Unreal":
            return Suggestion(True, "Unreal game: close on a crash instead of showing the crash reporter.")
        return None

    def applies(self, analysis) -> bool:
        return _rift(analysis) and analysis.engine == "Unreal"


class OculusUnreal(_PcvrPatch):
    id = "pcvr.oculus_unreal"
    title = "Patch Oculus detection for Unreal (Frame)"
    description = ("The PC VR counterpart of overport's 'Patch Oculus detection for Unreal'. Unreal's Oculus plugin only "
                   "starts when the Oculus service announces a headset (the Windows event 'OculusHMDConnected'); "
                   "without it the game runs as a flat window. On the Frame the launcher runs the game through "
                   "FramePort's small helper (fp_oculushmd.exe) that provides that event while the game runs, instead "
                   "of relying only on Revive's hook of the check. Ignored on this PC (the Oculus app or Revive "
                   "handle it there).")
    order = 18

    def detect(self, analysis):
        if not (_rift(analysis) and analysis.engine == "Unreal"):
            return None
        if analysis.extra.get("frame_native"):
            return Suggestion(False, "No Oculus plugin in use: runs directly.")
        return Suggestion(True, "Unreal game: its Oculus plugin checks for the Oculus service before it starts VR.")

    def applies(self, analysis) -> bool:
        return _rift(analysis) and analysis.engine == "Unreal"


class ProtonLog(_PcvrPatch):
    id = "pcvr.proton_log"
    title = "Proton debug log (Frame)"
    description = ("Write Proton's log (PROTON_LOG=1) to steam-<appid>.log in the game folder on the Frame. Slower; "
                   "use while debugging a game that doesn't start.")
    order = 30


class ProtonTool(_PcvrPatch):
    id = "pcvr.proton_tool"
    title = "Proton version (Frame)"
    description = ("Which Proton build runs the game on the Frame (a Steam compat tool name from the Frame's ARM64 "
                   "compat list, e.g. proton_11-arm64 or proton-experimental-arm64). Empty = newest installed.")
    order = 40
    params = [Param("tool", "str", "", "compat tool name")]


class ProtonEnv(_PcvrPatch):
    id = "pcvr.proton_env"
    title = "Extra launch environment (Frame)"
    description = "Environment variables for the Proton launcher on the Frame (e.g. DXVK_HUD=fps), one KEY=value per line."
    order = 50
    params = [Param("env", "text", "", "KEY=value lines")]


for _cls in (Revive, ReviveOpenVR, NoCrashReporter, OculusUnreal, XrTimefix, ProtonLog, ProtonTool, ProtonEnv):
    register(_cls)


def game_args(recipe) -> list[str]:
    """Extra command-line arguments for the game itself."""
    return ["-nocrashreports"] if "pcvr.no_crash_reporter" in recipe.patches else []


def launch_env(recipe) -> dict[str, str]:
    """Environment for the Frame launcher from the recipe's pcvr patches."""
    env = {}
    if "pcvr.proton_log" in recipe.patches:
        env["PROTON_LOG"] = "1"
    raw = recipe.params("pcvr.proton_env").get("env") or ""
    items = raw.items() if isinstance(raw, dict) else (line.partition("=")[::2] for line in str(raw).splitlines())
    for k, v in items:
        k = k.strip()
        if k:
            env[k] = str(v).strip()
    return env
