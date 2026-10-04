"""Unity XR settings the game's code chooses at runtime, fixed in libil2cpp.so (found per game with Cpp2IL, like
frame.unity_text_input).

- MSAA: Meta's OVRManager (useRecommendedMSAALevel) raises Unity's antiAliasing to the headset's recommended level
  (4x) at runtime ("The current MSAA level is 0, but the recommended MSAA level is 4. Switching to the recommended
  level."), whatever QualitySettings say, so frame.unity_no_msaa can't keep it off. Multisampled render-to-texture on
  GLES through Zink hangs the Frame's GPU (Lucky's Tale: the whole headset restarted in a menu, 2026-10-03).
  OVRDisplay.recommendedMSAALevel -> 0 keeps it off.
- Multiview: Oculus XR Plugin games render both eyes in one pass (Multiview) on Android. I Am Cat (Unity 2022.3, GLES
  through Zink) drew only effects into the right eye's array slice; OculusSettings.GetStereoRenderingMode -> 0
  (MultiPass) renders each eye in its own pass."""
from __future__ import annotations

from ..base import Suggestion, register
from .unity_text_input import RET_FALSE, Il2cppReturnPatch

RET_ZERO = RET_FALSE  # mov w0, #0 ; ret


class UnityRuntimeMsaa(Il2cppReturnPatch):
    id = "frame.unity_runtime_msaa_off"
    title = "Unity: keep MSAA off at runtime (OVRManager)"
    description = ("Meta's OVRManager switches Unity to the headset's recommended MSAA level (4x) while the game runs, "
                   "whatever its quality settings say (log: \"Switching to the recommended level\"). Multisampled "
                   "render-to-texture on GLES can hang the Frame's GPU, up to a restart of the whole headset (e.g. "
                   "Lucky's Tale in a menu). Rewrites OVRDisplay.recommendedMSAALevel -> 0 in libil2cpp.so (found with "
                   "Cpp2IL), so MSAA stays off.")
    order = 47
    targets = {"Oculus.VR/OVRDisplay.cs": {"get_recommendedMSAALevel": RET_ZERO},
               "Assembly-CSharp/OVRDisplay.cs": {"get_recommendedMSAALevel": RET_ZERO}}  # older Oculus Integration
    check_name = "Unity MSAA"

    def applies(self, a):
        return a.engine == "Unity" and "libil2cpp.so" in a.libs and not a.only_32bit

    def detect(self, a):
        if self.applies(a) and (a.extra or {}).get("ovr_runtime_msaa") and "GLES" in a.graphics:
            return Suggestion(True, "GLES Unity game whose OVRManager turns 4x MSAA on at runtime: multisampled "
                                    "render-to-texture can hang the Frame's GPU.")
        return None


class UnityMultiPass(Il2cppReturnPatch):
    id = "frame.unity_multipass"
    title = "Unity: render each eye separately (no multiview)"
    description = ("Oculus XR Plugin games render both eyes in one pass (multiview) on Android. If one eye shows only "
                   "effects or grey (e.g. I Am Cat), rendering each eye in its own pass can fix it, at some GPU cost. "
                   "Rewrites OculusSettings.GetStereoRenderingMode -> MultiPass in libil2cpp.so (found with Cpp2IL). "
                   "Try it when one eye is wrong.")
    order = 48
    targets = {"Unity.XR.Oculus/Unity/XR/Oculus/OculusSettings.cs": {"GetStereoRenderingMode": RET_ZERO}}
    check_name = "Unity stereo mode"

    def applies(self, a):
        return a.engine == "Unity" and "libil2cpp.so" in a.libs and not a.only_32bit \
            and (a.extra or {}).get("oculus_xr_plugin", True) is not False

    def detect(self, a):
        return None  # only for a game that shows the symptom (catalog recipe or the user's choice)


register(UnityRuntimeMsaa)
register(UnityMultiPass)
