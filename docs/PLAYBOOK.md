# Porting playbook: symptom → cause → fix

Everything here was hit for real while porting 34 Quest games to the Steam Frame (Sept 2026). The machine-readable
version is `catalog/triage.yaml` (used by `frameport test` / the Job screen); keep both in sync.

## Fast path for a new game
1. `frameport scan <folder>` → check the suggested recipe (`frameport show <pkg>`). Direct-VrApi engines get the bridge.
2. `frameport build <pkg>` → all static checks must pass (32-bit-only → stop, it can't run).
3. `frameport install <pkg>` → `frameport test <pkg>`: want **RUNNING** + "Submitting frames" at ~72 fps.
4. Put the headset on and look. Headless tests can't judge visuals (no FOCUSED state without the headset worn).
5. Fix by symptom below, rebuild, repeat. When it's good: **Save as known-good** (GUI) / add a catalog YAML.

## Startup failures (visible in launch.log)
| Symptom | Cause | Fix |
|---|---|---|
| Every game suddenly fails to start: `crun: create keyring …: Disk quota exceeded`, `is not a running context` | rootless podman leaked a kernel keyring per launch; 200-key quota exhausted | `keyring = false` in `~/.config/containers/containers.conf` (FramePort agent does it), then reboot the Frame once |
| `APP_ACTIVITY is empty`, nothing starts | Manifest has category INFO only; Lepton needs LAUNCHER | `frame.launcher` (automatic) |
| PC VR game on the Frame shows as a flat window / Revive: `Unable to load LibOVRRT DLL` / `LoaderInstance::CreateInstance chained CreateInstance call failed` | Frame SteamVR runtime rejects OpenXR apiVersion 1.1 (`XR_ERROR_API_VERSION_UNSUPPORTED`), which Proton 11's VR helper requests | `pcvr.xr_timefix` (Frame OpenXR layer, default on): retries xrCreateInstance as 1.0 |
| Unreal PC VR game on the Frame runs as a flat window although OpenXR works (no `LogHMD` OVRPlugin lines; Unreal logs nothing when it skips the Oculus plugin) / launch.log: `FramePort oculushmd: could not create the OculusHMDConnected event` | UE's OculusHMD (and LibOVR's `ovr_Detect`) only start when the Windows event `OculusHMDConnected` exists and is signalled; on a PC the Oculus service creates it. Revive hooks `OpenEventW` for it, but that relies on Detours patching Wine's (ARM64EC) kernelbase | `pcvr.oculus_unreal` (default for Unreal Rift games; PC VR counterpart of overport's `patch_oculus_unreal`): launch.sh runs the injector through `fp_oculushmd.exe`, which provides the real event until the game exits |
| PC VR repack fails via Revive but the exe runs when launched directly | The repack is pre-patched (SteamVR/OpenXR-native); Revive interferes | Default is now run-as-is (no Revive). Turn `pcvr.revive` OFF (it is off by default) |
| Un-cracked Oculus PC VR game: "Initializing OVR session" then exits / signature check on the runtime | Revive's hooks don't install on Proton-arm64; the Oculus shim rejects the unsigned Revive runtime | Not runnable on the Frame without the repack's crack extracted (not done by FramePort); use the PC version |
| PC VR game: `Failed to initialize Oculus API (-3001)` / `Unable to load LibOVRRT DLL` | Revive's LoadLibrary hook doesn't work under Proton arm64 (ARM64EC kernelbase); the game's LibOVR shim finds no runtime DLL | Symlink `LibOVRRT64_1.dll` → Revive's DLL in the prefix's system32 (manual so far) → then -3021 (runtime signature check; open) |
| Uploads to the Frame are slow (~15 MB/s) | PC and Frame both on Wi-Fi through the home router | Connect the PC to the Frame's own hotspot (or a USB cable): FramePort uses the direct link automatically (~80-100 MB/s); job log line "Transfer link" |
| PC VR game exits right away: `Unhandled Exception: 0xc06d007e` (after `FOnlineSubsystemOculus::InitWithWindowsPlatform`) | Delay-loaded `LibOVRPlatform64_1.dll` (Oculus Platform SDK, installed with the Oculus app) is missing | None on the Frame (entitlement check; FramePort doesn't replace it). PC mode with the Oculus app |
| Unreal PC VR game opens the crash reporter instead of closing | UE starts CrashReportClient.exe on a crash | `pcvr.no_crash_reporter` (default for Unreal Rift games): `-nocrashreports` + CrashReportClient.exe renamed `.disabled` in the Frame copy |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | 32-bit-only APK; Frame has no AArch32 | None. PC/Rift version via Revive |
| `UnsatisfiedLinkError` / `cannot locate symbol "ovr_…"` | overport's platform loader lacks Meta platform functions | `frame.ovrstubs` (automatic, generated stubs) |
| missing `ovrMessageType_ToString` | same, but the game needs a real string | `frame.ovrplatformcompat` (automatic) |
| `ClassNotFoundException com.oculus.os.AnalyticsEvent` → abort | Quest telemetry lookup in Meta XR Audio (Unreal build) or native code | `frame.metaxr_telemetry` + `frame.oculusos` (automatic) |
| `JNI DETECTED ERROR`, `GetStringUTFChars … NULL` | CheckJNI is on because overport marks the app debuggable | `frame.nodebug` |
| Unreal game quits a few seconds after start (`System.exit`) | ForceQuit after a failed Quest platform check | alternate build with `patch_remove_unreal_force_quit` (`use_alt`) |
| `xrCreateSwapchain` -26 / format unsupported (GLES) | Frame takes only sRGB formats, no MSAA | adapter `swapchain_fix` (default on) |
| Frames rejected, "Waiting…" forever | a layer uses a failed swapchain or an extension that isn't enabled (e.g. equirect2) | adapter `layer_fix` (default on) |
| `xrConvert…TimeKHR` FUNCTION_UNSUPPORTED spam; VrApi bridge stalls before recenter | runtime lacks timespec conversion | current adapter emulates it |
| Game (Unity, GLES) freezes, `zink: DEVICE LOST` | multisampled render-to-texture hangs the GPU | `frame.unity_no_msaa`; if it persists: unfixable → PC version |
| Setup/intro loops every launch (Espire 2) | save folders created without write permission | launcher repairs permissions every 2 s (built in) |
| VrApi bridge: never enters VR | Frame reaches FOCUSED later than Quest; tracking only when worn | bridge waits 30 s; test in the headset |

## Picture problems (headset on)
| Symptom | Cause | Fix |
|---|---|---|
| Black screen, audio works, GLES engine with direct VrApi | Mesa rejects Quest-style GLSL | `frame.gl_shim` (logs `GLShim: SHADER COMPILE FAILED` + source lines) |
| Black screen, audio works, log `Unsupported VrApi layer type N` | bridge drops frames containing that layer | extend the bridge (cylinder=3 is converted to a quad already) |
| Only some draws visible (e.g. controllers) + `glGetError 0x502` | multiview shaders used on single-view FBOs | GL shim `gl_hide_multiview=1` (default) |
| Upside-down image in a GL bridge game | GL images start at the bottom row | fixed in the bridge (swap angleUp/Down for GL chains) |
| UI panels upside down (AC Nexus) | XrCompositionLayerImageLayoutFB VERTICAL_FLIP unsupported | adapter `flip_emul=1` (default); rotating quads makes them vanish |
| Passthrough black (BAM) | XR_FB_passthrough missing | adapter `passthrough_emul=1` (default) + `patch_force_passthrough` for MR-only games |
| MR game stuck waiting for room data (Demeter) | no Meta scene API | adapter `scene_emul=1` (+ `frame.meta_permissions`) |
| Hand-tracking game janky (Silhouette) | Frame synthesizes hands from controllers | `controller_fix=0` passes hands through; not really fixable |
| Eye distortion while moving (Arcsmith, Time Stall) | unknown (not eye swap, tracking, Valve layers, depth or pacing) | unresolved |

## Debugging techniques that worked
- Read `<base>/launch.log` (logcat mirror). Filter the game's pid: `Start proc <pid>:<package>`.
- The adapter logs as `FrameBridge` (settings, xrCreateInstance result, swapchain retries, pacing fps).
- For GLES/GLAD engines, wrap `eglGetProcAddress` to see shader compile errors (build the shim with `-DGLSHIM_TRACE`
  for per-FBO draw counts and draw-call errors; the bridge has `-DOVP_GL_DIAG` for eye-image readback).
- Anything visual needs one headset session per iteration — batch hypotheses into each build.
- Keep the known-good APK and roll back if a change regresses (`PATCHED/_known-good-*`).

## When it can't run on the Frame
Stream PC VR instead: native Steam version if one exists (Espire 1: 669290; HITMAN WoA: 1659040; Sniper Elite VR),
or the Rift version through Revive (Journey of the Gods, Shadow Point). The catalog stores these as
`pcvr_alternative`. FramePort handles Rift games itself (next section).

## Oculus Rift (PC VR) games
Scan a folder with Rift game dumps (Windows game folders) like Quest dumps; they get ids `rift.<slug>` and the
"PC VR (Revive / Proton)" patch group. Two ways to run them:
- **This PC** (`--to pc`, GUI "Install on this PC"): a non-Steam shortcut in the local Windows Steam runs
  `ReviveInjector.exe /openxr "<game.exe>"` (FramePort's portable Revive; on WSL copied to
  `%LOCALAPPDATA%\FramePort`). Play on the Frame by streaming from SteamVR.
- **The Frame** (GUI "Install on Frame (Proton)"): game + Revive are uploaded to `~/Applications/quest-frame/rift.*`,
  and launch.sh runs them with the Frame's ARM64 Proton (Frame → Install Proton first). Experimental.

| Symptom | Cause | Fix |
|---|---|---|
| "Proton isn't installed on the Frame yet" | ARM64 Proton / Steam Linux Runtime 4 (arm64) not downloaded | Frame → Install Proton (confirm in the headset) or "Install without confirming" (`frameport frame proton --install --unattended`) |
| Game quits at once; `ovrPlatformInitialize_NotEntitled` / entitlement failed | Oculus Platform SDK entitlement check (FramePort flags these: "Uses the Oculus Platform SDK") | PC mode with the Oculus app installed and a license you own. FramePort doesn't bypass entitlement checks |
| `Failed to create process` in ReviveInjector.txt | wrong exe, or 32/64-bit mismatch | check the detected exe on the game page; rescan |
| `XR_ERROR_RUNTIME_UNAVAILABLE` / no OpenXR runtime (PC) | SteamVR not running / not the OpenXR runtime | start SteamVR, set it as OpenXR runtime; or `pcvr.revive_openvr` |
| `XR_ERROR_EXTENSION_NOT_PRESENT` / `xrConvertTimespecTimeToTimeKHR failed` (Frame) | ReviveXR needs `XR_KHR_win32_convert_performance_counter_time`; wineopenxr builds it on the host's timespec conversion. The Frame's Linux runtime supports it (2026-09-29), unlike its Android runtime | `pcvr.xr_timefix` (off by default): FramePort's timefix OpenXR layer emulates it like the Quest adapter |
| Proton game hangs at start in a headless/SSH launch; log `no driver could be loaded` / `explorer process failed to start` | no display session (DISPLAY/GAMESCOPE_WAYLAND_DISPLAY) | the Proton launch.sh takes them from the running Steam (built in) |
| Unreal game starts (window created) then `CrashReportClient` runs | the game crashed under Proton; often no VR runtime reached it (Revive off) | keep `pcvr.revive` on (repacks' bundled LibRevive64.dll isn't loaded by itself); read the game log + crash summary in the launch log |
| `VK_ERROR_DEVICE_LOST` under DXVK | freedreno GPU hang | `pcvr.proton_log` to capture; PC mode |
