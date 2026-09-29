---
name: port-quest-game
description: Port a Meta Quest standalone game (APK + OBB dump) to the Valve Steam Frame with FramePort, mostly autonomously - analyze, pick patches, build, install over SSH, launch-test, triage logs and iterate. Use when asked to port, fix or debug a Quest game on the Steam Frame.
---

# Port a Quest game to the Steam Frame

Work from the repo root (`frameport/`). Read `CLAUDE.md` (facts) and `docs/PLAYBOOK.md` (symptom → fix) first.
Set `UV_PROJECT_ENVIRONMENT=~/.cache/frameport-venv` (the repo is on NTFS).

## 1. Setup (once)
```
uv run frameport tools status            # install with: uv run frameport tools install
uv run frameport frame info              # connected Frame (paired earlier) — or: frame connect steamos@<host>
```
If no Frame is paired, ask the user to run the pairing one-liner (`uv run frameport frame pair`) on the Frame.

## 2. Analyze and choose patches
```
uv run frameport scan "<folder with the dump>"
uv run frameport show <package>          # suggested recipe with reasons
```
- 32-bit only (no arm64-v8a) → stop: cannot run on the Frame; suggest the PC/Rift version (Revive).
- Direct VrApi engine → `frame.vrapi_bridge` (+ `frame.gl_shim` for GLES/GLAD engines).
- Unreal → an alternate build without ForceQuit is prepared; install it if the primary quits itself.
- Adjust with `uv run frameport recipe <pkg> --enable <id> --disable <id> --set key=value`.

## 3. Build, install, test
```
uv run frameport build <pkg>             # every static check must pass
uv run frameport install <pkg>           # uploads (resumable), Steam shortcut + artwork; Steam restarts once
uv run frameport test <pkg>              # headless launch + triage
```
Healthy headless result: `RUNNING`, milestones up to "Submitting frames", ~72 fps. Headless runs can never
reach FOCUSED (the headset isn't worn) — so they validate startup only.

## 4. Iterate on failures
- Apply triage suggestions: `frameport recipe <pkg> --enable <suggested id>` → build → install → test.
- For anything not in the triage database: fetch `<base>/launch.log` (path in `frameport show <pkg> --json`
  → installs) and look at the game's pid lines; compare with PLAYBOOK rows.
- Visual problems need the user in the headset: batch several hypotheses (diagnostic logging, `-DGLSHIM_TRACE`,
  `-DOVP_GL_DIAG`) into one build, ask the user for one short session, then read the log.
- New root cause? Add a patch module (`src/frameport/patches/frame/`), a triage signature, a PLAYBOOK row and a
  test; rebuild native parts with `native/build.py` if the adapter/bridge/shim changed, then run `frameport parity`.

## 5. Finish
When the user confirms it plays: save the recipe (`catalog/games/<package>.yaml`, or "Save as known-good" in the
GUI), with status/notes, and keep the signing keystore (overport workspace `signatures/`).
