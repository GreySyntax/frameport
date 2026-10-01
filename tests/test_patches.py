import zipfile

from frameport.analysis.stubgen import build_stub_library
from frameport.apk.workspace import ApkWorkspace
from frameport.core.events import Reporter
from frameport.core.models import Analysis, Recipe
from frameport.patches import base
from frameport.patches.settings import adapter_settings


def _analysis(**kw):
    d = dict(package="com.example.questgame", version="1.0", label="Quest Game", abis=["arm64-v8a"], engine="Unity",
             xr="OpenXR", graphics="Vulkan (declared in manifest)", direct_vrapi=False, libs=[], launcher_activity=None,
             has_info_category=True, meta_permissions=[], uses_glad_gl=False, unity_msaa_levels=0,
             oculus_os_classes=False, is_overport_output=True, debuggable=True, extra={"size": 1})
    d.update(kw)
    return Analysis(**d)


def _apk(tmp_path, manifest):
    p = tmp_path / "in.apk"
    loader_imports = build_stub_library(["ovr_Present"], soname="libovrplatformloader.so")
    game = build_stub_library(["game_main"], soname="libgame.so")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("AndroidManifest.xml", manifest)
        z.writestr("classes.dex", b"dex\n035\0")
        z.writestr("lib/arm64-v8a/libopenxr_loader_generic.so", b"\x7fELF-original-loader")
        z.writestr("lib/arm64-v8a/libovrplatformloader.so", loader_imports)
        z.writestr("lib/arm64-v8a/libgame.so", game)
    return p


def test_registry_has_everything():
    ids = {p.id for p in base.all_patches()}
    for pid in ("patch_copy_libraries", "patch_remove_unreal_force_quit", "frame.adapter", "frame.launcher",
                "frame.ovrstubs", "frame.vrapi_bridge", "frame.gl_shim", "adapter.scene_emul", "device.files"):
        assert pid in ids


def test_adapter_and_launcher(tmp_path, quest_manifest):
    apk = _apk(tmp_path, quest_manifest)
    recipe = {"frame.adapter": {}, "frame.launcher": {}, "adapter.scene_emul": {"value": 1}}
    with ApkWorkspace(apk) as ws:
        for pid in ("frame.adapter", "frame.launcher"):
            ctx = base.ApkContext(ws, _analysis(), {}, Reporter(), recipe)
            assert base.get(pid).apply(ctx)
        out = ws.write(tmp_path / "out.apk")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert z.read("lib/arm64-v8a/libopenxr_loader_original.so") == b"\x7fELF-original-loader"
        assert z.read("lib/arm64-v8a/libopenxr_loader_generic.so").startswith(b"\x7fELF")
        assert z.read("lib/arm64-v8a/libframe_settings.so") == b"scale=1.0\nfoveation_fix=1\ncontroller_fix=1\nscene_emul=1\n"
        assert names.count("lib/arm64-v8a/libopenxr_loader_generic.so") == 1


def test_controller_models_adds_xrshim(tmp_path, quest_manifest):
    from pathlib import Path

    from frameport.analysis import elf

    # like Meta's OVRPlugin: DT_NEEDED libopenxr_loader.so + dlopen("libopenxr_loader.so") / dlsym(xrGetInstanceProcAddr)
    plugin = (Path(__file__).with_name("fixtures") / "libfakeovrplugin_arm64.so").read_bytes()
    for enabled in (True, False):
        apk = _apk(tmp_path, quest_manifest)
        with zipfile.ZipFile(apk, "a") as z:
            z.writestr("lib/arm64-v8a/libOVRPlugin.so", plugin)
        recipe = {"frame.adapter": {}, **({"adapter.controller_models": {"value": 1}} if enabled else {})}
        with ApkWorkspace(apk) as ws:
            assert base.get("frame.adapter").apply(base.ApkContext(ws, _analysis(), {}, Reporter(), recipe))
            out = ws.write(tmp_path / f"out{enabled}.apk")
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            patched = z.read("lib/arm64-v8a/libOVRPlugin.so")
        if enabled:
            assert "lib/arm64-v8a/libframe_xrshim.so" in names
            assert len(patched) == len(plugin) and b"libframe_xrshim.so\0\0" in patched
            assert patched.count(b"libopenxr_loader.so\0") == 1  # only the DT_NEEDED name (.dynstr) is left
            assert elf.needed(patched) == elf.needed(plugin)
        else:  # off: builds stay byte-identical to before
            assert "lib/arm64-v8a/libframe_xrshim.so" not in names and patched == plugin


def test_replace_rodata_string_whole_strings_only():
    from pathlib import Path

    from frameport.analysis import elf

    plugin = (Path(__file__).with_name("fixtures") / "libfakeovrplugin_arm64.so").read_bytes()
    out, n = elf.replace_rodata_string(plugin, "libopenxr_loader.so", "libframe_xrshim.so")
    assert n == 1 and elf.replace_rodata_string(out, "libopenxr_loader.so", "x")[1] == 0
    assert elf.replace_rodata_string(plugin, "openxr_loader.so", "y")[1] == 0  # a tail of a longer string


def test_settings_order_and_types():
    s = adapter_settings({"adapter.controller_fix": {"value": 0}, "adapter.scene_height": {"value": "3"}})
    assert list(s) == ["scale", "foveation_fix", "controller_fix", "scene_height"]
    assert s["controller_fix"] == 0 and s["scene_height"] == 3.0


def test_controller_models_setting():
    patch = base.get("adapter.controller_models")
    plain = _analysis()
    assert patch.detect(plain) is None and not patch.applies(plain)
    meta_sdk = _analysis(libs=["libOVRPlugin.so"])
    assert patch.applies(meta_sdk) and patch.detect(meta_sdk) is None  # SDK present, runtime models not declared
    for a in (_analysis(meta_permissions=["com.oculus.permission.RENDER_MODEL"]),
              _analysis(extra={"features": {"com.oculus.feature.RENDER_MODEL": False}})):
        s = patch.detect(a)
        assert s.recommended and s.params == {"value": 1} and patch.applies(a)
    assert adapter_settings({"adapter.controller_models": {"value": 1}})["controller_models"] == 1
    assert "controller_models" not in adapter_settings({})  # off unless selected: existing builds are unchanged


def test_equirect_emul_setting():
    patch = base.get("adapter.equirect_emul")
    gles = dict(graphics="GLES or unknown (no Vulkan declaration)")
    vulkan_360 = _analysis(extra={"xr_layer_exts": ["XR_KHR_composition_layer_equirect2"]})
    assert not patch.applies(vulkan_360) and patch.detect(vulkan_360) is None  # never offered for Vulkan games
    plain = _analysis(**gles, extra={"xr_layer_exts": ["XR_KHR_composition_layer_cylinder"]})
    assert patch.applies(plain) and patch.detect(plain) is None  # GLES but no 360 layers requested
    player = _analysis(**gles, extra={"xr_layer_exts": ["XR_KHR_composition_layer_cylinder", "XR_KHR_composition_layer_equirect2"]})
    s = patch.detect(player)
    assert s.recommended and s.params == {"value": 1}
    for key in ("equirect_face", "equirect_res", "equirect_flip", "equirect_fps", "equirect_stereo"):
        assert base.get(f"adapter.{key}").applies(player) and not base.get(f"adapter.{key}").applies(vulkan_360)


def test_per_game_session_settings_are_off_unless_selected():
    keys = ("equirect_emul", "layer_debug", "stable_local", "focus_hold", "aim_pitch", "aim_yaw", "aim_forward",
            "refresh_rate", "equirect_face", "equirect_flip", "equirect_fps", "equirect_stereo")
    assert not set(keys) & set(adapter_settings({}))  # existing builds are unchanged
    chosen = adapter_settings({"adapter.refresh_rate": {"value": 90}, "adapter.aim_pitch": {"value": "-12.5"},
                               "adapter.focus_hold": {"value": 1}})
    assert chosen["refresh_rate"] == 90.0 and chosen["aim_pitch"] == -12.5 and chosen["focus_hold"] == 1
    for key in keys:
        patch = base.get(f"adapter.{key}")
        assert patch.detect(_analysis()) is None


def test_detect_direct_vrapi_suggests_bridge_and_shim():
    a = _analysis(xr="VrApi", direct_vrapi=True, uses_glad_gl=True, graphics="GLES or unknown", package="x.y.unknown")
    from frameport.recommend import engine

    r = engine.suggest(a)
    assert "frame.vrapi_bridge" in r.patches and "frame.gl_shim" in r.patches
    assert r.source == "heuristics"


def test_catalog_recipe_climb2():
    from frameport.recommend import engine

    r = engine.suggest(_analysis(package="com.crytek.climb2", xr="VrApi", direct_vrapi=True, engine="Other"))
    assert r.source.startswith("catalog")
    assert "frame.vrapi_bridge" in r.patches
    assert r.params("device.files")["files"]["user.cfg"].startswith("r_variable_rate_shading = 0")
    assert r.status == "works"


def test_catalog_phantom_uses_alt():
    from frameport.recommend import engine

    r = engine.suggest(_analysis(package="com.nDreams.PhantomQuest", engine="Unreal", xr="VrApi"))
    assert r.use_alt and r.alt_patches == ["patch_remove_unreal_force_quit"]


def test_32bit_unsupported():
    from frameport.recommend import engine

    r = engine.suggest(_analysis(package="x.y.old", abis=["armeabi-v7a"]))
    assert r.status == "unsupported"


def test_recipe_roundtrip():
    from frameport.core import library

    r = Recipe("a.b", {"frame.adapter": {}}, ["patch_remove_unreal_force_quit"], True)
    assert library.recipe_from_dict(library.recipe_to_dict(r)) == r


def _fixture(name):
    from pathlib import Path

    return (Path(__file__).with_name("fixtures") / name).read_bytes()


def test_swapchain_limit_raises_overport_guard():
    from frameport.analysis import elf
    from frameport.patches.frame.swapchain_limit import raise_swapchain_limit

    lib = _fixture("libfakeoverport_arm64.so")
    out, n = raise_swapchain_limit(lib)
    assert n == 2 and len(out) == len(lib)
    cmps = [o for _, m, o in elf.text_instructions(out) if m == "cmp" and "lsl #12" in o]
    assert cmps and all(o.endswith("#4, lsl #12") for o in cmps)  # 16384
    assert raise_swapchain_limit(out) == (None, 0)  # idempotent
    assert elf.dyn_symbols(out, True) == elf.dyn_symbols(lib, True)
    assert raise_swapchain_limit(_fixture("libfakeengine_arm64.so")) == (None, 0)  # no xrCreateSwapchain


def test_swapchain_limit_suggested_for_video_players():
    p = base.get("frame.swapchain_limit")
    assert p.detect(_analysis(engine="Other", libs=["libavcodec4x.so", "libvr4p-oculus.so"])).recommended
    assert p.default_on and p.detect(_analysis(engine="Unity", libs=["libunity.so"])).recommended


def test_vk_sanitize_routes_engine_vulkan_through_shim(tmp_path, quest_manifest):
    from frameport.analysis import elf

    engine = _fixture("libfakeengine_arm64.so")
    apk = _apk(tmp_path, quest_manifest)
    with zipfile.ZipFile(apk, "a") as z:
        z.writestr("lib/arm64-v8a/libUE4.so", engine)
    with ApkWorkspace(apk) as ws:
        assert base.get("frame.vk_sanitize").apply(base.ApkContext(ws, _analysis(engine="Unreal"), {}, Reporter(), {}))
        out = ws.write(tmp_path / "out.apk")
    with zipfile.ZipFile(out) as z:
        patched = z.read("lib/arm64-v8a/libUE4.so")
        shim = z.read("lib/arm64-v8a/libfp_vk.so")
    assert len(patched) == len(engine) and b"libfp_vk.so\0\0" in patched and b"libvulkan.so\0" not in patched
    assert elf.soname(shim) == "libfp_vk.so" and "libvulkan.so" in elf.needed(shim)
    assert {"vkGetInstanceProcAddr", "vkGetDeviceProcAddr", "vkCreateRenderPass2"} <= elf.dyn_symbols(shim, True)
    # a game without the dlopen string is left alone
    apk2 = _apk(tmp_path, quest_manifest)
    with ApkWorkspace(apk2) as ws:
        assert not base.get("frame.vk_sanitize").apply(base.ApkContext(ws, _analysis(engine="Unreal"), {}, Reporter(), {}))


def test_source_hints_are_generic_and_match_loosely():
    from frameport.recommend.catalog import generic_source_hint, source_hint_matches

    assert generic_source_hint("4XVR Video Player (Pro + Trial Bypass) v20022+2.0.22 -JF") == "4XVR Video Player"
    assert generic_source_hint("Marvels Deadpool VR (English Only) v8742+1.0.40.356975.Quest") == "Marvels Deadpool VR"
    assert generic_source_hint("Batman- Arkham Shadow (Inc Lang Packs) v350961+1.4.1-350961") == "Batman- Arkham Shadow"
    assert source_hint_matches("Marvels Deadpool VR", "Marvel's Deadpool VR v9000+1.1 -XYZ")  # other release, other name
    assert source_hint_matches("The Climb 2 v974+2.2", "the climb 2 (quest) v1000")
    assert not source_hint_matches("The Climb 2", "The Climb v100")
