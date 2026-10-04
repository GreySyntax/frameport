"""Unity XR settings fixed in libil2cpp.so: OVRManager's runtime MSAA and the Oculus XR Plugin's multiview."""
from pathlib import Path

from test_patches import _analysis

from frameport.analysis import il2cpp
from frameport.patches import base
from frameport.patches.frame import unity_runtime as R
from frameport.patches.frame import unity_text_input as U
from frameport.validate.triage import triage


def _lib() -> bytes:
    return sorted(Path(__file__).parent.joinpath("fixtures").glob("libfake*_arm64.so"))[0].read_bytes()


def test_all_il2cpp_patches_share_one_cpp2il_lookup():
    base.load_all()
    for p in (U.UnityTextInput, R.UnityRuntimeMsaa, R.UnityMultiPass):
        for cls, methods in p.targets.items():
            assert set(methods) <= set(U.ALL_TARGETS[cls])
    # keyed by the build's metadata: an earlier patch's bytes in libil2cpp.so don't force another Cpp2IL run
    assert il2cpp._cache_file(b"a" * 10, b"meta") == il2cpp._cache_file(b"b" * 10, b"meta")
    assert il2cpp._cache_file(b"a" * 10, b"meta") != il2cpp._cache_file(b"a" * 10, b"other")


def test_runtime_patches_write_zero_returns():
    lib = _lib()
    lo, _hi = U._executable(lib)[0]
    a, b = (lo + 0x40) & ~3, (lo + 0x80) & ~3
    found = {"Assembly-CSharp/OVRDisplay.cs": {"get_recommendedMSAALevel": (a, 0x5C)},
             "Unity.XR.Oculus/Unity/XR/Oculus/OculusSettings.cs": {"GetStereoRenderingMode": (b, 8)}}
    msaa, notes = U.patch_methods(lib, found, R.UnityRuntimeMsaa.targets)
    assert msaa[a:a + 8] == R.RET_ZERO and msaa[b:b + 8] == lib[b:b + 8] and "OVRDisplay" in notes[0]
    multi, _ = U.patch_methods(lib, found, R.UnityMultiPass.targets)
    assert multi[b:b + 8] == R.RET_ZERO and multi[a:a + 8] == lib[a:a + 8]


def test_runtime_msaa_suggested_for_gles_ovr_games_only():
    base.load_all()
    p = base.get("frame.unity_runtime_msaa_off")
    gles = _analysis(libs=["libil2cpp.so", "libunity.so"], graphics="GLES or unknown (no Vulkan declaration)",
                     extra={"ovr_runtime_msaa": True})
    assert p.detect(gles).recommended
    assert p.detect(_analysis(libs=["libil2cpp.so"], extra={"ovr_runtime_msaa": True})) is None  # Vulkan
    gles.extra["ovr_runtime_msaa"] = False
    assert p.detect(gles) is None and p.applies(gles)
    assert base.get("frame.unity_multipass").detect(gles) is None  # opt-in: only for the symptom
    assert not base.get("frame.unity_multipass").applies(_analysis(libs=["libunity.so"]))  # Mono: no Cpp2IL


def test_triage_points_runtime_msaa_to_the_patch():
    log = ("09-28 17:39:01.000  1000  1000 I ActivityManager: Start proc 1147:com.example.game/u0a55 for activity\n"
           "09-28 17:39:04.000  1147  1174 I Unity   : The current MSAA level is 0, but the recommended MSAA level is "
           "4. Switching to the recommended level.\n")
    f = {x.id: x for x in triage(log, "RUNNING", "com.example.game").findings}
    assert "frame.unity_runtime_msaa_off" in f["unity-runtime-msaa"].suggest



def test_library_recipes_follow_their_catalog_entry(tmp_path, monkeypatch):
    import json

    from frameport.core import library
    from frameport.recommend import catalog, engine

    monkeypatch.setattr(library, "_path", lambda: tmp_path / "library.json")
    entry = catalog.CatalogEntry.from_dict({"package": "com.x.game", "title": "X", "status": "issues",
                                            "frame": ["frame.unity_multipass"], "updated": "2026-10-04"}, "bundled")
    monkeypatch.setattr(catalog, "lookup", lambda pkg: entry if pkg in ("com.x.game", "com.x.mine") else None)
    an = {"package": "com.x.game", "engine": "Unity", "libs": ["libil2cpp.so", "libunity.so"], "abis": ["arm64-v8a"]}
    games = {"com.x.game": {"analysis": an, "recipe": {"package": "com.x.game", "source": "heuristics"}},
             "com.x.mine": {"analysis": dict(an, package="com.x.mine"),
                            "recipe": {"package": "com.x.mine", "source": "user", "patches": {"frame.adapter": {}}}}}
    (tmp_path / "library.json").write_text(json.dumps({"games": games, "settings": {}}))
    got = library.load()["games"]
    r = got["com.x.game"]["recipe"]
    assert "frame.unity_multipass" in r["patches"] and r["status"] == "issues" and r["catalog_rev"] == entry.rev()
    mine = got["com.x.mine"]["recipe"]  # the user's own choice stays (one-time migrations aside)
    assert "frame.adapter" in mine["patches"] and "frame.unity_multipass" not in mine["patches"]
    assert not library._follow_catalog(library.load())  # nothing changed since: no re-derive on every load
    assert engine.suggest(library.analysis_from_dict(an)).catalog_rev == entry.rev()


def test_maintained_entry_verified_later_beats_the_users_shared_one():
    from frameport.recommend import catalog

    def e(origin, **kw):
        return catalog.CatalogEntry.from_dict({"package": "p.q", "title": "P", **kw}, origin)
    user = e("user", verified={"date": "2026-10-03"})
    fixed, old = e("bundled", updated="2026-10-04"), e("bundled", verified={"date": "2026-09-28"})
    assert catalog._newer(fixed, user) and not catalog._newer(old, user) and not catalog._newer(None, user)
