"""Unity text fields that close at once on the Frame: make them edit in place, like on a PC.

Unity's text fields (TextMeshPro TMP_InputField, and uGUI's older InputField) open the system on-screen keyboard on
Android and close themselves a frame later when none is visible. Lepton's Android has no on-screen keyboard (Meta's
system keyboard does this job on a Quest), so the field shows a caret for a moment and loses focus: nothing can be
typed (found with Stremio VR, 2026-10-03). Two return values are rewritten so the field behaves as on a PC: it doesn't
wait for a system keyboard and takes key presses as events, from Steam's keyboard (with device.text_input_window) or
the PC keyboard (Type on Frame). The methods are found per game with Cpp2IL (analysis/il2cpp.py)."""
from __future__ import annotations

from ...analysis import elf
from ..base import ApkContext, Patch, Suggestion, register

METADATA = "assets/bin/Data/Managed/Metadata/global-metadata.dat"
RET_FALSE = bytes.fromhex("00008052c0035fd6")  # mov w0, #0 ; ret
RET_TRUE = bytes.fromhex("20008052c0035fd6")   # mov w0, #1 ; ret
# Cpp2IL class file -> {method: what it should return}
TARGETS = {
    "Unity.TextMeshPro/TMPro/TMP_InputField.cs": {"TouchScreenKeyboardShouldBeUsed": RET_FALSE,
                                                  "isKeyboardUsingEvents": RET_TRUE},
    # uGUI's InputField: its LateUpdate returns early (keeps the field) when InPlaceEditing() is true
    "UnityEngine.UI/UnityEngine/UI/InputField.cs": {"TouchScreenKeyboardShouldBeUsed": RET_FALSE,
                                                   "InPlaceEditing": RET_TRUE},
}


def _executable(data: bytes) -> list[tuple[int, int]]:
    return [(s["p_offset"], s["p_offset"] + s["p_filesz"]) for s in elf._elf(data).iter_segments()
            if s["p_type"] == "PT_LOAD" and s["p_flags"] & 1]


def patch_methods(lib: bytes, found: dict[str, dict[str, tuple[int, int]]]) -> tuple[bytes | None, list[str]]:
    """Write the return values over the methods' first two instructions. found = il2cpp.find_methods' result.
    Returns (patched library or None when nothing changed, notes)."""
    out, notes, changed = bytearray(lib), [], False
    code = _executable(lib)
    for cls, methods in TARGETS.items():
        for method, new in methods.items():
            off, length = found.get(cls, {}).get(method, (None, 0))
            if off is None:
                continue
            name = f"{cls.rsplit('/', 1)[-1][:-3]}.{method}"
            if off % 4 or length < len(new) or not any(a <= off and off + len(new) <= b for a, b in code):
                raise RuntimeError(f"{name}: offset {off:#x} isn't code in libil2cpp.so (Cpp2IL mismatch)")
            if bytes(out[off:off + len(new)]) == new:
                notes.append(f"{name} already patched")
                continue
            out[off:off + len(new)] = new
            changed = True
            notes.append(f"{name} -> {'true' if new == RET_TRUE else 'false'} (at {off:#x})")
    return (bytes(out) if changed else None), notes


class UnityTextInput(Patch):
    id = "frame.unity_text_input"
    title = "Make Unity text fields work without a system keyboard"
    description = ("Unity's TMP_InputField / InputField wait for Android's on-screen keyboard and close themselves a "
                   "frame later when there is none (Lepton has no on-screen keyboard; Meta's system keyboard does "
                   "this on a Quest), so a selected text field only flashes a caret. Rewrites "
                   "TouchScreenKeyboardShouldBeUsed -> false and isKeyboardUsingEvents (TMP) / InPlaceEditing "
                   "(uGUI) -> true in libil2cpp.so, found per game with Cpp2IL (downloaded on first use), so fields "
                   "stay selected and take key presses: Steam's keyboard (with \"Show the app window\") or Type on "
                   "Frame from the PC.")
    order = 46
    needs_vr = False

    def applies(self, a):
        return a.engine == "Unity" and "libil2cpp.so" in a.libs and bool((a.extra or {}).get("text_fields"))

    def detect(self, a):
        if self.applies(a):
            kinds = " and ".join((a.extra or {}).get("text_fields"))
            return Suggestion(True, f"Unity app with text fields ({kinds}): they close at once on the Frame because "
                                    "it has no system keyboard.")
        return None

    def apply(self, ctx: ApkContext) -> bool:
        from ...analysis.il2cpp import find_methods

        ws = ctx.ws
        lib_name = ws.lib("libil2cpp.so")
        if ws.abi != "arm64-v8a" or not ws.has(lib_name) or not ws.has(METADATA):
            return False
        version = (ctx.analysis.extra or {}).get("unity_version")
        if not version:
            ctx.reporter.check("Unity text fields", False, "not fixed: unknown Unity version (rescan the game)")
            return False
        lib = ws.read(lib_name)
        ctx.reporter.log("finding the text field code (Cpp2IL; the first time can take a minute)")
        try:
            found = find_methods(lib, ws.read(METADATA), version, {c: list(m) for c, m in TARGETS.items()})
            patched, notes = patch_methods(lib, found)
        except Exception as exc:  # noqa: BLE001 - optional fix: the game still builds, its text fields as before
            ctx.reporter.check("Unity text fields", False, f"not fixed: {exc}")
            return False
        ctx.notes.extend(notes or ["no Unity text field code found"])
        if patched is not None:
            ws.put(lib_name, patched)
        return patched is not None


register(UnityTextInput)
