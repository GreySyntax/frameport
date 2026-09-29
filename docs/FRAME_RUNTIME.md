# Steam Frame runtime reference (SteamOS 0.3.0, build 20260922)

## Lepton (Android container)
- Steam app **3029110 "Lepton"** (runtime) and **3056000 "Lepton Development"** (needs Developer Mode). The binary
  is `<Steam library>/steamapps/common/Lepton/lepton`; FramePort finds it via the appmanifests.
- Started per game with `lepton start` and these env vars: `SteamAppId`, `STEAM_COMPAT_INSTALL_PATH` (dir with
  `game.apk` + `obb/`), `STEAM_COMPAT_DATA_PATH` (container data/saves), `STEAM_COMPAT_SHADER_PATH`,
  `STEAM_COMPAT_LIBRARY_PATHS`, `IS_PARENT=true`, plus `LEPTON_ENV_<NAME>` to pass `<NAME>` into the container
  (FramePort passes `FRAMEBRIDGE_CONFIG`).
- Runs as podman container `lepton-steamlaunch-<appid>`; logcat is mirrored to stdout (→ `launch.log`) and to
  `~/.local/share/Steam/logs/lepton-logcats/steamlaunch-<appid>`. "Exited!" / "Early-exit" mark the end.
- Picks the activity with category LAUNCHER (`APP_ACTIVITY`). Game data is visible at `/sdcard/Android/{obb,data}/<pkg>`.
- 64-bit only (no AArch32).

## OpenXR runtime (as seen by games through overport's loader)
- Instance extensions present include KHR_android_create_instance (must be enabled; the adapter adds it),
  KHR_vulkan_enable(2), KHR_opengl_es_enable, KHR_composition_layer_depth, FB_display_refresh_rate, EXT_hand_tracking
  (synthesized from controllers), VALVE_frame_controller_interaction, META_recommended_layer_resolution,
  FB_swapchain_update_state, FB_space_warp, EXT_frame_synthesis, … (full list in any launch.log, tag overportOXR).
- Missing (emulated by the adapter): XR_FB_passthrough, XR_FB_scene / spatial_entity(_query/_storage/_container) /
  scene_capture, XR_FB_composition_layer_image_layout (flip), XR_KHR_convert_timespec_time (returns
  FUNCTION_UNSUPPORTED). Missing (dropped): XR_KHR_composition_layer_equirect2, XR_KHR_composition_layer_cylinder
  (the VrApi bridge converts cylinders to quads).
- Swapchain formats: GLES `GL_SRGB8_ALPHA8` (35907) / `GL_SRGB8` (35905) only, samples = 1. Vulkan: 43 (R8G8B8A8_SRGB)
  and 50, not 37/44 (UNORM).
- Environment blend: ALPHA_BLEND available (greyscale passthrough cameras).
- Reference spaces: STAGE bounds are reported as 1×1 m.
- Display 72 Hz by default in tests. Head pose is only tracked while the headset is worn; otherwise flags 0x3 and
  the session stays below FOCUSED.

## Graphics stack
- Vulkan: freedreno (Mesa Turnip); Valve injects `VK_LAYER_VALVE_rpo` and `VALVE_fdm_injection` via
  VK_INSTANCE_LAYERS (set `VK_INSTANCE_LAYERS=""` through `device.lepton_env` to test without them).
- GL ES: Zink (Mesa GL on Vulkan). Strict GLSL (see PLAYBOOK) and occasional `DEVICE LOST` with MSAA render-to-texture.

## Steam integration
- Non-Steam shortcut: binary `userdata/<id>/config/shortcuts.vdf`; appid = crc32(exe+title) | 0x80000000;
  artwork in `config/grid/<appid>p.*` (portrait), `<appid>.*` (landscape), `<appid>_hero.*`, `<appid>_logo.*`.
  Steam reads the file only at start → stop steam.service, edit, start (FramePort does it once per batch).
- Processes started from Steam (Konsole, SSH sessions?) share steam.service's cgroup: use `systemd-run --user`.
- SSH: `sshd` must be enabled (`sudo systemctl enable --now sshd`), which needs a user password (`passwd`).
- mDNS: avahi-daemon runs by default; hostname `frame` → `frame.local`.
