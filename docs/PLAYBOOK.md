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
| `APP_ACTIVITY is empty`, nothing starts | Manifest has category INFO only; Lepton needs LAUNCHER | `frame.launcher` (automatic) |
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
or the Rift version through Revive + SteamVR (Journey of the Gods, Shadow Point). The catalog stores these as
`pcvr_alternative`; a Revive target is planned (`targets/revive.py`).
