# FramePort

[![Release](https://img.shields.io/github/v/release/spoopyghosty0/frameport)](https://github.com/spoopyghosty0/frameport/releases/latest)
[![Build](https://img.shields.io/github/actions/workflow/status/spoopyghosty0/frameport/build.yml?branch=main)](https://github.com/spoopyghosty0/frameport/actions/workflows/build.yml)
[![Downloads](https://img.shields.io/github/downloads/spoopyghosty0/frameport/total)](https://github.com/spoopyghosty0/frameport/releases)
[![License](https://img.shields.io/github/license/spoopyghosty0/frameport)](LICENSE)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-blue)
![Status](https://img.shields.io/badge/status-proof%20of%20concept-orange)

Install Meta Quest games, Android apps and PC VR games on the **Valve Steam Frame**: FramePort patches them for the
Frame, copies them over Wi-Fi and adds them to the Frame's Steam library with artwork.

![Library](docs/images/library.png)

> **Proof of concept, not a piracy tool.** FramePort doesn't download, share or unlock games and doesn't remove DRM,
> licence or entitlement checks. Use it only with games you own. Most of the work is done by the projects it wraps
> ([Built on](#built-on)).

## Features

- **Painless setup:** one short command on the Frame. No root, no `sudo`, no password.
  [What it changes](docs/FRAME_SETUP.md).
- **One click per game:** convert, patch, sign, upload, add to Steam with artwork, launch test.
- **Per-game recipes:** a tested catalog plus detection rules; every patch explained in plain words.
- **FrameBridge:** FramePort's OpenXR adapter emulates what the Frame lacks (passthrough, room, controller models,
  curved and 360° layers); game settings as simple switches.
- **Long queues that finish:** wake lock, resumes after sleep or disconnects, resumable uploads, fast USB/hotspot link.
- **Your files stay untouched:** converted copies are temporary; per-game signing keys keep saves across updates.
- **Beyond Quest:** Android apps as windows, PC VR via Proton or Revive, a Files tab with drag and drop.
- **Self-updating** releases, redacted diagnostics, one-click problem reports and working-config sharing.

## Quick start

1. [Download](https://github.com/spoopyghosty0/frameport/releases/latest) and unzip the build for Windows, macOS
   (Apple Silicon) or Linux, then start FramePort.
2. **Steam Frame → Show setup command**, then run it in the Frame's Konsole (Desktop mode).
3. **Add games → Scan a folder** with your game backups (APK + OBB, or PC VR game folders).
4. Open a game → **Install on Frame**, then play it from the Frame's Steam library.

Full guide, firewalls and troubleshooting: [docs/INSTALL.md](docs/INSTALL.md).

## Compatibility

38 games have tested recipes (27 work, 5 with known issues, 6 can't run); others get suggested patches.
Details: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Built on

[OVRPort](https://github.com/Android-XR-Bridge/OVRPort) (Quest → OpenXR, originally
[ovrport/app](https://github.com/ovrport/app)) · Valve Lepton, Proton and SteamVR ·
[Revive](https://github.com/LibreVR/Revive) · Mesa (Zink) · [Khronos OpenXR SDK](https://github.com/KhronosGroup/OpenXR-SDK)
· Eclipse Temurin, Android apksigner and NDK · [Flet](https://flet.dev) · OculusDB and Steam store data.
What FramePort adds itself: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Documentation

| | |
|---|---|
| [INSTALL.md](docs/INSTALL.md) | Install, connect, update, PC VR, files, problem reports |
| [FRAME_SETUP.md](docs/FRAME_SETUP.md) | What setup changes on the Frame, networks and firewalls, undoing it |
| [COMPATIBILITY.md](docs/COMPATIBILITY.md) | What runs and how well |
| [PLAYBOOK.md](docs/PLAYBOOK.md) | Symptoms and fixes per game |
| [FRAME_RUNTIME.md](docs/FRAME_RUNTIME.md) | Steam Frame runtime facts |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the code is organised |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Code, recipes, translations |

## Development

```
uv sync --extra dev
uv run frameport-gui        # the app
uv run frameport --help     # command line
uv run pytest               # tests
```

## License

GPL-3.0-only (includes GPL-3.0 code from OVRPort). Not affiliated with Valve or Meta.
