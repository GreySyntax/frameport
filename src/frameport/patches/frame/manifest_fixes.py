"""AndroidManifest.xml fixes: LAUNCHER category, debuggable flag, Meta-only permissions."""
from __future__ import annotations

from ...apk import axml
from ..base import ApkContext, Patch, Suggestion, register

MANIFEST = "AndroidManifest.xml"


class Launcher(Patch):
    id = "frame.launcher"
    title = "Launcher category (INFO → LAUNCHER)"
    description = ("Lepton only launches an activity with category LAUNCHER (Quest apps often use INFO). "
                   "Symptom when missing: launch.log says 'APP_ACTIVITY is empty' and nothing starts.")
    order = 20
    default_on = True

    def detect(self, a):
        if a.has_info_category:
            return Suggestion(True, "Manifest uses category INFO only; Lepton needs LAUNCHER.")
        return Suggestion(True, "Applied automatically if the manifest needs it.")

    def apply(self, ctx: ApkContext) -> bool:
        fixed = axml.fix_launcher(ctx.ws.read(MANIFEST))
        if fixed:
            ctx.ws.put(MANIFEST, fixed)
        return bool(fixed)

    def validate(self, ctx):
        cats = axml.categories(ctx.ws.read(MANIFEST))
        return [("Launchable in Lepton", axml.LAUNCHER in cats, "category LAUNCHER present")]


class NoDebuggable(Patch):
    id = "frame.nodebug"
    title = "Clear android:debuggable"
    description = ("overport marks apps debuggable; that turns on CheckJNI, which aborts some Unreal games on sloppy "
                   "JNI calls ('JNI DETECTED ERROR', e.g. 'GetStringUTFChars ... NULL' in Time Stall). Clear it for "
                   "those.")
    order = 21

    def detect(self, a):
        from ..applicability import is_unreal, unreal_version

        if not is_unreal(a):
            return None
        v = unreal_version(a)
        if v and v < (4, 22):
            return Suggestion(True, f"Unreal Engine {v[0]}.{v[1]}: older UE4 makes JNI calls CheckJNI rejects "
                                    "(e.g. Time Stall aborted on GetStringUTFChars(NULL)).")
        if any(l.startswith("libmetaxraudio") for l in a.libs):
            return Suggestion(True, "Unreal build of Meta XR Audio: its telemetry lookup leaves a pending JNI exception "
                                    "that CheckJNI turns into an abort (e.g. NOPE Challenge).")
        return Suggestion(False, "Enable if the game aborts with 'JNI DETECTED ERROR' (CheckJNI).")

    def applies(self, a):
        return a.engine in ("Unreal", "Other", "CryEngine")

    def apply(self, ctx):
        fixed = axml.set_bool_attr(ctx.ws.read(MANIFEST), "application", "debuggable", False)
        if fixed:
            ctx.ws.put(MANIFEST, fixed)
        return bool(fixed)


class MetaPermissions(Patch):
    id = "frame.meta_permissions"
    title = "Declare Meta-only permissions"
    description = ("Declares com.oculus.permission.* / horizonos.permission.* that the app uses, so Android grants "
                   "them at install (e.g. the 'use spatial data' permission of mixed-reality games like Demeter).")
    order = 22

    def detect(self, a):
        from ..applicability import needs_scene

        if needs_scene(a):
            return Suggestion(True, "Mixed-reality game that needs the room model: its 'use spatial data' permission must "
                                    "be granted (e.g. Demeter).")
        scene = [p for p in a.meta_permissions if any(k in p for k in ("SCENE", "ANCHOR", "SPATIAL", "BOUNDARY"))]
        if scene:
            return Suggestion(False, "Uses Meta scene/anchor permissions (" + ", ".join(p.rsplit(".", 1)[-1] for p in scene[:3])
                              + "); enable if the game says it needs spatial data access.")
        return None

    def applies(self, a):
        return bool(a.meta_permissions or a.extra.get("meta_permissions_used"))

    def apply(self, ctx):
        fixed = axml.define_meta_permissions(ctx.ws.read(MANIFEST))
        if fixed:
            ctx.ws.put(MANIFEST, fixed[0])
            ctx.notes.append(f"declared {len(fixed[1])} permission(s)")
        return bool(fixed)


register(Launcher)
register(NoDebuggable)
register(MetaPermissions)
