"""Inspect an APK and describe everything the patch suggestions depend on."""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from ..apk import axml
from ..core.models import Analysis
from . import elf

logging.getLogger("pyaxmlparser").setLevel(logging.ERROR)

UNITY_GGM = "assets/bin/Data/globalgamemanagers"


def _read_manifest_info(path: Path) -> tuple[str, str, str, str | None]:
    from pyaxmlparser import APK

    apk = APK(str(path))
    label = apk.application or apk.package
    return apk.package, apk.version_name or "", label, apk.get_main_activity()


def analyze(path: Path, deep: bool = True) -> Analysis:
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        abis = sorted({n.split("/")[1] for n in names if n.startswith("lib/") and n.count("/") >= 2})
        abi = next((a for a in ("arm64-v8a", "armeabi-v7a") if a in abis), None)
        prefix = f"lib/{abi}/" if abi else None
        libs = sorted(n[len(prefix):] for n in names if prefix and n.startswith(prefix) and n.endswith(".so"))
        manifest = z.read("AndroidManifest.xml")
        lib_bytes = {}
        if deep and prefix:
            for lib in libs:
                # only the libraries that drive decisions (all engine libs can be hundreds of MB)
                if lib in ("libovrplatformloader.so",) or not lib.startswith(("libopenxr_loader", "libfrda")):
                    info = z.getinfo(prefix + lib)
                    if info.file_size < 400 * 2**20:
                        lib_bytes[lib] = z.read(info)
        boot = z.read("assets/bin/Data/boot.config").decode("utf-8", "replace") if "assets/bin/Data/boot.config" in names else ""
        ggm = z.read(UNITY_GGM) if deep and UNITY_GGM in names else None

    package, version, label, activity = _read_manifest_info(path)
    libset = set(libs)
    engine = "Unreal" if libset & {"libUE4.so", "libUnreal.so"} else "Unity" if "libunity.so" in libset else "Other"
    has_openxr, has_vrapi = "libopenxr_loader.so" in libset, "libvrapi.so" in libset
    xr = "OpenXR+VrApi" if has_openxr and has_vrapi else "OpenXR" if has_openxr else "VrApi" if has_vrapi else "?"
    is_overport = "libopenxr_loader_generic.so" in libset or "liboverport.config.so" in libset
    # VrApi called directly by the engine (no OVRPlugin): overport cannot translate it.
    direct_vrapi = has_vrapi and "libOVRPlugin.so" not in libset
    if is_overport:  # overport adds OVRPlugin to direct-VrApi games; judge by the engine lib instead
        direct_vrapi = any("libvrapi.so" in elf.needed(b) for n, b in lib_bytes.items()
                           if elf.is_elf(b) and n not in ("libOVRPlugin.so", "libvrapi.so"))
    vulkan_declared = b"android.hardware.vulkan" in manifest or _uses_feature(manifest, "android.hardware.vulkan")
    if vulkan_declared:
        graphics = "Vulkan (declared in manifest)"
    elif engine == "Unity" and "vulkan" in boot.lower():
        graphics = "Vulkan (Unity boot.config)"
    else:
        graphics = "GLES or unknown (no Vulkan declaration)"

    uses_glad = False
    oculus_os = False
    for name, data in lib_bytes.items():
        if not elf.is_elf(data):
            continue
        if b"com/oculus/os/AnalyticsEvent" in data:
            oculus_os = True
        if name not in ("libvrapi.so", "libOVRPlugin.so") and b"GLAD_GL_" in data and "eglGetProcAddress" in elf.dyn_symbols(data, False):
            uses_glad = True

    msaa_levels = 0
    if ggm is not None:
        try:
            from ..patches.frame.unity_no_msaa import count_msaa_levels

            msaa_levels = count_msaa_levels(ggm)
        except Exception:
            msaa_levels = 0

    cats = axml.categories(manifest)
    return Analysis(
        package=package,
        version=version,
        label=label,
        abis=abis,
        engine=engine,
        xr=xr,
        graphics=graphics,
        direct_vrapi=direct_vrapi,
        libs=libs,
        launcher_activity=activity,
        has_info_category=axml.INFO in cats and axml.LAUNCHER not in cats,
        meta_permissions=axml.undeclared_meta_permissions(manifest),
        uses_glad_gl=uses_glad,
        unity_msaa_levels=msaa_levels,
        oculus_os_classes=oculus_os,
        is_overport_output=is_overport,
        debuggable=bool(axml.Axml(manifest).get_bool("application", "debuggable")),
        extra={"missing_ovr_symbols": sorted(missing_ovr_symbols(lib_bytes)), "size": path.stat().st_size},
    )


def _uses_feature(manifest: bytes, feature: str) -> bool:
    x = axml.Axml(manifest)
    return any(el.name == "uses-feature" and (x.attr_str(el, "name") or "").startswith(feature) for el in x.elements())


def missing_ovr_symbols(lib_bytes: dict[str, bytes]) -> set[str]:
    """ovr_* / ovrMessageType_* functions the game imports that the platform loader (+compat/stub libs) lacks."""
    loader = lib_bytes.get("libovrplatformloader.so")
    if not loader or not elf.is_elf(loader):
        return set()
    exported = set(elf.dyn_symbols(loader, True))
    for extra in ("libovrplatformcompat.so", "libovrstubs.so"):
        if extra in lib_bytes:
            exported |= elf.dyn_symbols(lib_bytes[extra], True)
    wanted = set()
    for name, data in lib_bytes.items():
        if name.startswith(("libovrplatformloader", "libopenxr_loader", "libframe_settings", "libfrda")) or not elf.is_elf(data):
            continue
        wanted |= {s for s in elf.dyn_symbols(data, False) if s.startswith(("ovr_", "ovrMessageType_"))}
    return wanted - exported
