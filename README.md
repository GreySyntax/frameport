# FramePort

Play your Meta Quest standalone games on the **Valve Steam Frame**. FramePort patches a Quest APK so it runs in the
Frame's Lepton Android runtime, validates the result, installs it over Wi-Fi and adds it to your Steam library with
artwork. Think Morphe/ReVanced Manager, for Quest → Steam Frame:

1. **Connect your Frame** once: the app shows one command to paste into the Frame's desktop terminal (it turns on SSH,
   trusts the app, and installs Lepton). After that the app finds the Frame on your network by itself.
2. **Add games**: scan a folder of Quest dumps (APK + OBB) or add an APK.
3. **Review patches**: every game gets suggested patches (overport's plus Steam Frame fixes), each with a reason.
   34 games have verified recipes; unknown games get heuristics.
4. **Patch, validate, install**: signature/alignment/dependency checks, upload (resumable, skips data already on the
   Frame), Steam shortcut + artwork, and a headless launch test with log triage that suggests fixes.

FramePort downloads and manages its own tools (Java runtime, [overport](https://github.com/ovrport/app) CLI,
apksigner). Nothing is installed system-wide, and no Android SDK/NDK is needed.

## Install
- **App bundles**: download from [Releases](https://github.com/spoopyghosty0/frameport/releases) (Windows x64, macOS
  Apple Silicon, Linux x64); see `docs/INSTALL.md` for the first-launch warning (self-signed / ad-hoc signed) and
  how to verify a download. Built by CI (`.github/workflows/build.yml`, `flet build`). FramePort **updates itself**:
  when a new release is out, click **Update now** in the Library (or run `frameport update`).
- **Command line only**: `uv tool install <the release's frameport-*.whl link>` (or pipx / pip), then `frameport --help`.
- **From source**: `uv sync && uv run frameport-gui` (CLI: `uv run frameport --help`).

## Status of tested games
See `catalog/games/` (and the Library screen): 23 work, 5 work with issues, 6 can't run on the Frame (3 are 32-bit
only; PC VR alternatives are listed). Details and how problems were solved: `docs/PLAYBOOK.md`.
All 34 recipes were verified by rebuilding from the original dumps (`docs/parity-report.md`: 34/34 match the
hand-made known-good builds) and reinstalling + launch-testing on a Frame (`docs/parity-device-report.md`: 0 regressions).

## Legal
Use only with games you own. FramePort does not download games. It is GPL-3.0-only because it bundles code derived
from GPL-3.0 projects (Quest2Frame's hook technique in the adapter, the Android-XR-Bridge/OVRPort VrApi bridge).
overport is downloaded from its official releases and is not redistributed.

## Development
See `CLAUDE.md` (architecture, commands, findings) and `docs/ARCHITECTURE.md`.
