# FramePort — notes for Claude

FramePort ports Meta Quest standalone APKs to the **Valve Steam Frame** (SteamOS, aarch64). Games run in Valve's **Lepton**
(Waydroid-based Android container), one container per game, launched from a Steam library shortcut. Pipeline:
`scan → analyze → suggest recipe (catalog/heuristics) → user confirms → overport → Frame fixes → sign → static checks →
install over SSH (agent) → Steam shortcut → headless launch test + log triage`.

Read `docs/PLAYBOOK.md` (symptom → fix) before debugging a game, and `docs/FRAME_RUNTIME.md` for runtime facts.

## Layout
- `src/frameport/` — Python package. `pipeline.py` is the API the CLI (`cli.py`) and GUI (`ui/app.py`, Flet 1.0) share.
  - `patches/` — **the unit of modularity**. `base.py` (Patch interface, registry), `overport.py` (overport CLI patch ids,
    discovered dynamically via `overport patches`), `frame/*.py` (one module per Frame fix), `settings.py` (FrameBridge
    adapter keys + device files as patches). Add a patch = add a module that calls `register(...)`.
  - `analysis/` — APK/ELF inspection (`detect.py`), `elf.py` (pyelftools reads; own DT_NEEDED writer), `stubgen.py`
    (generates the ovr_* stub .so without a compiler).
  - `apk/` — `axml.py` (binary manifest editor), `workspace.py` (staged zip edits), `sign.py` (apksigner; it aligns too).
  - `recommend/` — `catalog.py` (known-good recipes: user > remote `FRAMEPORT_CATALOG_URL` > bundled), `engine.py`.
  - `tools/` — portable toolchain (Temurin JRE, overport jar, apksigner) downloaded dynamically into the user data dir.
  - `frame/` — SSH (paramiko), mDNS discovery, pairing server; `install/installer.py`; `validate/` (static, device, triage).
  - `targets/` — `Target` interface; `frame_lepton.py` now, `revive.py` placeholder (PC VR later).
  - `parity.py` — rebuild catalog games from dumps and classify every APK entry difference vs known-good builds.
- `agent/frameport_agent.py` — runs **on the Frame** (python3 stdlib only), JSON over SSH. Owns the install layout,
  launch.sh template, Steam shortcuts (binary VDF), launch tests. Bump `AGENT_VERSION` when changing it.
- `bootstrap/bootstrap.sh` — one-time Frame setup served by the pairing server (sshd, app key, avahi service, Lepton).
- `catalog/games/<package>.yaml` — 34 recipes verified 2026-09-28; `catalog/triage.yaml` — log signatures → fixes.
- `native/` — sources of the prebuilt binaries in `artifacts/` (adapter, VrApi bridge patches, GL shim, stubs).
  `native/build.sh` rebuilds them with NDK r27c (downloaded on demand; never committed). Users never need the NDK.

## Dev commands
```
export UV_PROJECT_ENVIRONMENT=~/.cache/frameport-venv   # keep the venv off NTFS (repo lives on D:)
uv sync --extra dev          # or: uv pip install -e .[dev]
uv run pytest                # unit tests (no device, no game files)
uv run frameport --help      # CLI;  uv run frameport-gui  for the GUI
uv run frameport parity --known-good <PATCHED/_known-good-*> --sources "<VR CyberDeck downloads>"
```
Games/device tests: `pytest -m games` (FRAMEPORT_GAMES=<downloads dir>), `pytest -m device` (FRAMEPORT_FRAME=steamos@host).
Repo is on an NTFS drive (`core.fileMode=false`); line endings are LF (`.gitattributes`).

## Hard-won facts (don't re-learn these)
**Frame runtime (SteamOS 0.3.0, build 20260922):**
- No AArch32: 32-bit-only APKs fail with `INSTALL_FAILED_NO_MATCHING_ABIS`. Unfixable; point to Rift + Revive.
- GLES swapchains: only `GL_SRGB8_ALPHA8`/`GL_SRGB8` (35907/35905), no MSAA. Vulkan: format 43 (sRGB) but not 37 (UNORM).
- Missing: XR_FB_passthrough (emulate via ALPHA_BLEND), XR_FB_scene/spatial entities (emulated room from STAGE bounds),
  XR_KHR_composition_layer_equirect2/cylinder, XR_FB_composition_layer_image_layout (flip emulated by Vulkan blit).
- `xrConvertTimespecTimeToTimeKHR`/`xrConvertTimeToTimespecTimeKHR` return FUNCTION_UNSUPPORTED → adapter emulates
  (offset = predictedDisplayTime − period − CLOCK_MONOTONIC, sampled in xrWaitFrame).
- Guardian STAGE bounds report 1×1 m (scene emulation uses ≥1.5 m).
- GL goes through **Zink** (Mesa GL on Vulkan). Mesa GLSL is strict: `#pragma` before `#extension` fails, implicit
  int/float conversions fail (enable `GL_EXT_shader_implicit_conversions`), num_views=2 shaders on single-view FBOs
  give GL_INVALID_OPERATION. Some GLES games hit `zink: DEVICE LOST` (Unity MSAA RTT; Sniper Elite VR even without).
- Valve injects `VALVE_rpo`/`VALVE_fdm_injection` Vulkan layers (via VK_INSTANCE_LAYERS).
- **Tracking only works with the headset worn.** SSH/headless launches never reach VISIBLE/FOCUSED and poses have
  flags 0x3. So automated tests prove startup (process alive, instance/session created, frames paced), never visuals.

**Lepton:** needs an activity with category **LAUNCHER** (Quest apps often only have INFO → "APP_ACTIVITY is empty").
Lepton = Steam app 3029110 (+ "Lepton Development" 3056000, needs Developer Mode). Per-game env: STEAM_COMPAT_INSTALL_PATH
/DATA_PATH/SHADER_PATH, SteamAppId. Logs: `<base>/launch.log` and `~/.local/share/Steam/logs/lepton-logcats/steamlaunch-<appid>`.
Containers are podman `lepton-steamlaunch-<appid>`. Some Unreal games create save dirs without u+rwx → launcher repairs every 2 s.
**Rootless podman leaks one kernel session keyring per container start** (200-key quota per user): after ~200 launches
since boot every game fails with `crun: create keyring …: Disk quota exceeded` / `is not a running context`. Fix:
`keyring = false` in `~/.config/containers/containers.conf` (agent `ensure_host_fixes`, bootstrap); leaked keys only
go away with a reboot. Check usage: `grep "^ *1000:" /proc/key-users` (agent `info` → kernel_keys).

**Discovery/network:** Developer-Mode SteamOS devices announce `_steamos-devkit._tcp` (TXT `login=steamos`) — use it;
they don't publish `_ssh._tcp`. The Frame has several links: `wlan0` (home Wi-Fi), `wlanap` = its own hotspot at
10.35.78.1/24 (a PC can join it directly), `usb0` = USB gadget network 10.86.200.233/29 (up when cabled to a PC).
The dev PC runs WSL2 in mirrored networking mode (mDNS works). Beware `pkill -f <pattern>` killing your own shell.

**Steam:** shortcut appid = crc32('"<anchor>/launch.sh"' + title) | 0x80000000; shortcuts.vdf is only read at Steam
start, so Steam must be stopped while writing it. Terminals/SSH started from Steam live in steam.service's cgroup —
stopping Steam kills them → always run that work via `systemd-run --user` (the agent does). Artwork goes to
`userdata/<id>/config/grid/<appid>{p,,_hero,_logo}.<ext>`.

**overport:** always `--version=latest`; `--workspace` holds runtimes and **per-package keystores (password
"password", alias "key") — never lose them**: updates must be signed with the same key or saves are lost on reinstall.
Output is deterministic (same input + runtime → same bytes), which is what makes parity testing possible.

**Patching gotchas:**
- UnityPy re-serialization breaks scene loading → patch QualitySettings ints in place.
- LIEF's DT_NEEDED injection shifts segments; our `elf.add_needed` appends a new PT_LOAD (reuses PT_NOTE, else moves the
  phdr table + adds PT_PHDR) and leaves existing bytes untouched. Bionic requires section headers and matching .dynamic.
- apksigner 37 aligns (4 B / .so 16 KiB) itself — no zipalign needed.
- Titles from aapt with apostrophes got truncated once; we read labels with pyaxmlparser and store titles from the
  overport image API (`https://ovrp.crx.moe/images/by_package?package=`), which also serves Steam artwork.
- GLAD engines fetch all GL via eglGetProcAddress → wrap it (GL shim) to fix/trace shaders; that's how POTW was solved.
- VrApi-direct engines (CryEngine Climb 2, POTW) need the VrApi bridge; the bridge drops whole frames on unknown layer
  types (→ black screen with audio).

**Unresolved (as of 2026-09-28):** Arcsmith (right-eye distortion) and Time Stall (both eyes) — swap, tracking, Valve
layers, depth, pacing ruled out. Sniper Elite VR (DEVICE LOST), Espire 1 (Mesa GL upload crash), HITMAN 3 (freedreno
crash): use PC versions.

## Releases
Push a `v*` tag → CI builds Windows/macOS(arm64)/Linux bundles, signs Windows binaries with the self-signed
certificate (secrets `WINDOWS_CODESIGN_PFX` base64 + `WINDOWS_CODESIGN_PASSWORD`; public cert `packaging/`,
private copy only on the maintainer's machine + backup), ad-hoc signs the macOS app, adds build attestations and
publishes a GitHub Release with SHA256SUMS. macOS x86_64 isn't built (cryptography cross-build fails).

## Heuristics (games not in the catalog)
Each patch's `detect()` suggests itself from the Analysis; `applies()` says whether it can matter at all (the UI/CLI
hide non-applicable patches; enabled ones are always shown). Rules learned from the 34 games:
MR-only (PASSTHROUGH required + BOUNDARYLESS_APP) → force_passthrough, + USE_SCENE → scene_emul + meta_permissions;
hand tracking required → controller_fix=0; OVRPlugin + ≥20 GiB → disable_space_warp; Unreal ≤4.21 or Unreal Meta XR
Audio → nodebug; Oculus-OS class referenced by the Unreal audio build or ≥2 Meta libs → oculusos; legacy-VrApi Unity
GLES with MSAA → unity_no_msaa; CryEngine → user.cfg r_variable_rate_shading=0; direct VrApi → bridge (+GL shim for
GLAD/GLES); Unreal → alternate no-ForceQuit build. Score changes with
`python scripts/eval_heuristics.py "<dumps>"` (catalog off vs verified recipes; currently 33/34 exact — Phantom's
"use the no-ForceQuit build" is only detectable at runtime via triage). Add a rule → re-run the eval + `pytest -m games`.

## Conventions
- Dynamic first: fetch live data (tool versions, overport patch list/titles, artwork, catalog) with cache + bundled
  fallback (`core/cache.py`). Don't hardcode what can be discovered (e.g. Lepton path via appmanifests).
- Keep APK edits minimal and byte-stable (parity depends on it). Every new fix: a patch module + a triage signature +
  a PLAYBOOK row + a unit test.
- Known-good backups of every working APK and the signing keys: `PATCHED/_known-good-2026-09-28/` (don't delete).
- Your development Frame: `frameport frame info` (remembered in `<user data>/frames.json`); SSH as `steamos@<frame>`.
