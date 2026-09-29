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


def test_settings_order_and_types():
    s = adapter_settings({"adapter.controller_fix": {"value": 0}, "adapter.scene_height": {"value": "3"}})
    assert list(s) == ["scale", "foveation_fix", "controller_fix", "scene_height"]
    assert s["controller_fix"] == 0 and s["scene_height"] == 3.0


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
