# Native components

Sources of the prebuilt binaries in `../artifacts/` (committed, with `SHA256SUMS`). End users never compile anything;
developers rebuild with `python native/build.py` (downloads NDK r27c, OpenXR headers at pinned commits, d8 into
`native/.cache/`, git-ignored). The adapter, GL shim and platform compat rebuild byte-identically; the VrApi bridge
embeds debug paths (`-g`) and the dex depends on the d8 version, so those differ in bytes only.

| Dir | Artifact | What / why |
|---|---|---|
| `adapter/` | `{arm64-v8a,armeabi-v7a}/libopenxr_loader_generic.so` | **FrameBridge**: replaces overport's generic loader (which is renamed `libopenxr_loader_original.so`) and fixes Steam Frame runtime gaps. Hooks xrCreateInstance, swapchains, xrEndFrame, spaces, xrPollEvent, … everything else is forwarded by `gen_forwarders.py`-generated tail calls. `scene_emu.c` = Meta scene/spatial-entity emulation; `flip_vk.c` = Vulkan blit for VERTICAL_FLIP quads. Settings: `libframe_settings.so` in the APK + `settings.conf`/`framebridge.conf` on the Frame. Hook technique from Quest2Frame's `frame_bridge.c` (GPL-3.0). |
| `vrapi-bridge/` | `arm64-v8a/libvrapi.so` | VrApi → OpenXR bridge from [Android-XR-Bridge/OVRPort](https://github.com/Android-XR-Bridge/OVRPort) `native/vrapi` at 5e7df52 (GPL-3.0, `LICENSE.upstream`), with our changes in `upstream-patches/`: GLES sessions + GL texture swapchains, cylinder→quad layers, GL vertical flip, UNORM↔sRGB format twins, 30 s VR-mode deadline, VALID-only recenter, loading-icon layers skipped, diagnostics behind `-DOVP_GL_DIAG`. |
| `platformcompat/` | `arm64-v8a/libovrplatformcompat.so` | Real `ovrMessageType_ToString` (same fork, `native/platform`). |
| `glshim/` | `arm64-v8a/libglshim.so` | Mesa GLSL compatibility for GLAD engines (hooks eglGetProcAddress): comments out `#pragma` before `#extension`, enables `GL_EXT_shader_implicit_conversions`, hides GL_OVR_multiview (`gl_hide_multiview`, default 1) and logs failed shaders. `-DGLSHIM_TRACE` adds per-FBO draw/error tracing. |
| `java-stubs/` | `dex/oculusos-stubs.dex` | No-op `com.oculus.os.AnalyticsEvent` / `UnifiedTelemetryLogger` for native code that looks them up. |

The ovr_* stub library is *not* prebuilt: `src/frameport/analysis/stubgen.py` generates it per game from the exact
missing symbols (no compiler needed).

After changing anything here: `python native/build.py`, commit artifacts + SHA256SUMS, then run `frameport parity`
(the report marks updated binaries as "expected") and a device launch test.
