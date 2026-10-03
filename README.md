# FramePort

FramePort installs Meta Quest standalone games, other Android apps and PC VR games on the **Valve Steam Frame**. It
patches a game so it runs on the Frame's runtimes, copies it to the headset over Wi-Fi and adds it to the Frame's Steam
library with artwork.

![Library](docs/images/library.png)

## Purpose

FramePort is a **proof of concept**: it shows that VR software built for other platforms (Meta Quest, Oculus Rift) can
run on the Steam Frame with a translation layer and a few targeted fixes. Most of the work is done by the projects
FramePort wraps (see [Built on](#built-on)); FramePort selects and applies their fixes per game, adds Steam
Frame-specific patches, and handles installing, testing and launching.

FramePort is **not a piracy tool**. It does not download, share or unlock games, and it does not remove DRM, licence or
entitlement checks. Use it only with games you own. Games that check their licence through the Oculus Platform SDK are
marked as not runnable on the Frame.

## Highlights

- **Painless setup, no root.** One command in the Frame's Konsole connects it: it turns on Developer Mode with Valve's
  own helper and lets FramePort in. No `sudo`, no password, no packages, and SteamOS's read-only system stays
  untouched. Nothing is installed system-wide on your computer either.
- **One click from game backup to Steam library.** FramePort converts, patches, signs, checks, uploads and adds the
  game to the Frame's Steam library with full artwork, then runs a short launch test and reads the logs for known
  problems.
- **Knows what each game needs.** Recipes come from a catalog of games tested on the Frame, plus detection rules for
  everything else. Every patch is explained in plain words, with **Show technical details** for the exact change.
- **Fills in what the Frame lacks.** FramePort's own OpenXR adapter (FrameBridge) emulates what Quest games expect
  and the Frame doesn't provide: passthrough, a room for mixed-reality games, controller models, curved screens,
  360° video layers. **Game settings** (sharpness, refresh rate, controllers, menus) are switches and sliders,
  shown only when they matter for that game.
- **Long queues that finish.** Installs queue up and keep the Frame awake. If the Frame sleeps or drops off the
  Wi-Fi, the queue pauses and resumes, and uploads continue where they stopped. A USB cable or the Frame's own
  hotspot is used automatically for faster uploads.
- **Your files stay yours.** Your game files are never modified: the converted copy is temporary. Each game keeps
  its own signing key, so updates keep your saves.
- **Looks at home in Steam.** Store artwork, descriptions and genres become Steam art and tags. Apps without store
  art get generated artwork from their icon.
- **More than Quest games.** Ordinary Android apps open as windows in the headset, with Android's navigation bar
  hidden. PC VR games run through Proton on the Frame or through Revive and SteamVR on your PC. The **Files** tab
  sends videos and other files into the Frame's shared folders or a game's own storage, with drag and drop.
- **Easy to keep current and to report.** Signed releases update themselves. Diagnostics are redacted (no IP
  addresses, user names or paths), and problem reports and working configs open as prefilled GitHub issues. The
  interface is ready for translation.

What runs, and how well, is listed in [What works](#what-works).

## Getting started

1. **Download** the archive for your computer from the
   [latest release](https://github.com/spoopyghosty0/frameport/releases/latest) and extract it anywhere:

   | Computer | File | Start |
   |---|---|---|
   | Windows 10/11 (x64) | `FramePort-windows-x64.zip` | `FramePort.exe` |
   | macOS (Apple Silicon) | `FramePort-macos-arm64.zip` | `FramePort.app` |
   | Linux (x64) | `FramePort-linux-x64.tar.gz` | `FramePort/FramePort` |

   The builds are self-signed, so the first start shows a warning: Windows → **More info → Run anyway**;
   macOS → right-click the app → **Open**. Details: [docs/INSTALL.md](docs/INSTALL.md).
2. **Get the tools**: on first start FramePort downloads what it needs (Java runtime, OVRPort, apksigner) into its own
   folder. Nothing is installed system-wide.
3. **Connect the Frame**: in FramePort open **Steam Frame** → **Show setup command**, then on the Frame switch to
   Desktop mode, open Konsole and type that one short command. It turns on Developer Mode and lets FramePort in:
   no root, no `sudo`, no password. This is needed once. Everything it changes is listed in
   [What FramePort changes on the Frame](#what-frameport-changes-on-the-frame); firewall notes are in
   [Network and firewalls](#network-and-firewalls).
4. **Add games**: **Add games → Scan a folder** with your game backups: Android/Quest games (APK + OBB) or PC VR
   game folders.
   FramePort identifies each game and fetches its artwork and store details.
5. **Install**: open a game and click **Install on Frame**. FramePort patches, checks, uploads and adds the game to the
   Frame's Steam library, then runs a short launch test. Several installs queue up; if the Frame goes to sleep or
   drops off the Wi-Fi, the queue waits and continues where it stopped (FramePort keeps the Frame awake meanwhile).
   Your game files are never changed: the converted copy is temporary.
6. **Play**: put the headset on and start the game from the Steam library (or click **Play on Frame**).

![Game page](docs/images/game.png)

Each game has **Game settings** in plain words (sharpness, refresh rate, controllers, menus, 360° video, mixed
reality), showing only what matters for that game. Changes are kept with the game and reach the Frame right away.

![Game settings](docs/images/game-settings.png)

FramePort updates itself: when a new version is released, the Library shows **Update now**.

### Network and firewalls

During first-time setup the Frame downloads the setup script from FramePort on your computer: TCP ports 8765–8767,
open only while the setup command is shown and for at most 30 minutes. After that, every connection goes from your
computer to the Frame (SSH), and nothing needs to reach your computer.

- **Windows:** allow FramePort when Windows asks (Private networks). If Windows treats your network as Public, allow
  Public too or switch the network to Private.
- **macOS:** if the macOS firewall is on, allow incoming connections when it asks.
- **Linux:** if firewalld or ufw blocks the port, FramePort shows the command that opens it (firewalld: until the next
  restart).
- **WSL:** FramePort adds a temporary Hyper-V firewall rule (one admin prompt) and removes it when setup is done. WSL
  needs mirrored networking (`networkingMode=mirrored` in `.wslconfig`).
- If nothing reaches FramePort within about 45 seconds, the setup page says what is likely blocking it on your system.
- **No inbound connection at all:** turn on Developer Mode in the Frame's settings first. The Frame then appears under
  **On your network**. On the Frame open Settings → Developer → **Pair new host**, click **Connect** in FramePort and
  approve it (Valve's own devkit pairing). FramePort sets up the rest over SSH.

## What FramePort changes on the Frame

The setup command (step 3) runs [`bootstrap/bootstrap.sh`](bootstrap/bootstrap.sh), served by the app over your
local network. Everything it changes:

| Change | Where | How to undo |
|---|---|---|
| Turns on **Developer Mode** (only if it's off). Steam restarts once, which closes Desktop Mode; the rest of the setup finishes on its own as a user service (log: `~/.cache/frameport-setup.log`). | `"DevModeEnabled" "1"` in `~/.local/share/Steam/config/config.vdf` (old file kept as `config.vdf.before-frameport`), then Valve's own `steamos-polkit-helpers/steamos-devkit-mode --enable`. That helper enables the SSH server (`sshd`), the devkit service that makes the Frame findable on the network, the remote-desktop and debug services, and system crash dumps. | Settings → System → Developer → Developer Mode off. Valve's helper switches all of those services off again. |
| Lets the app's SSH key in. | One line ending in `frameport` in `~/.ssh/authorized_keys`. The folder and file are created if missing. | Delete that line. |
| Configures podman for Lepton. Rootless podman leaks one kernel keyring per container start, and after about 200 game starts every game fails. | `[containers]` / `keyring = false` in `~/.config/containers/containers.conf`. | Remove those lines. |
| Asks Steam to install **Lepton** (Valve's Android runtime, Steam app 3029110) if it's missing. You confirm it in Steam. | Steam library | Uninstall it in Steam. |

The script runs as your user: no root, no `sudo`, no password. The only system-level change, Developer Mode, is
made by Valve's own helper, the same one the Settings switch uses. If Developer Mode can't be turned on
automatically, the script asks you to turn it on in Settings → System → Developer and run the command again.

Nothing else on the system is touched: no packages, no read-only-filesystem changes, no polkit rules. The script
also leaves two files: `~/.cache/frameport-setup.sh` (the part that runs on its own) and its log.

**Later, the app adds** (all as your user, no root, no `sudo`):
- FramePort's helper in `~/.local/share/frameport/`;
- the games, each with a launcher and its data in `~/Applications/quest-frame/<package>/`;
- their Steam library entries and artwork (`shortcuts.vdf` + `config/grid/`);
- for PC VR games: an OpenXR layer (`~/.local/share/openxr/1/api_layers/explicit.d/XR_APILAYER_FRAMEPORT_timefix.json`)
  and, when the first PC VR game is installed, Valve's ARM64 Proton and its Steam Linux Runtime (Steam downloads them;
  Steam restarts once).

**Settings → Uninstall FramePort → Also remove from the Frame** deletes the games, their Steam entries, the helper
folder, the OpenXR layer and the setup script's files. Developer Mode, the SSH key line, the podman setting, Lepton
and Proton stay. Undo them as shown above or in Steam.

## What works

The built-in catalog has tested settings for 38 games (27 work, 5 work with known issues, 6 can't run on the Frame).
Other games get suggested patches from detection rules; each suggestion states its reason, and every patch can be
switched on or off under **Customize**: described in plain words, with **Show technical details** for the exact
effect of each patch.

Tried an untested game? Its page asks how it runs; **Share working config…** opens a prefilled GitHub issue so your
recipe can join the built-in catalog for everyone.

![Patches](docs/images/patches.png)

| Kind of app | On the Steam Frame |
|---|---|
| Meta Quest games (APK) | Translated to OpenXR (OVRPort) and patched for the Frame; run in Valve's Android runtime (Lepton) |
| Other Android VR apps using OpenXR (e.g. Pico builds) | Translated the same way; the other headset's own extensions and store services aren't available |
| Ordinary Android apps and games (no VR) | Installed unchanged and shown as a flat window in the headset |
| PC VR games (Windows; OpenXR, SteamVR or Oculus) | Run through Proton on the Frame (experimental), or on a Windows PC with SteamVR and streamed to the Frame; Oculus-only games use Revive |
| Can't run | 32-bit-only or x86-only APKs, Pico/HTC Wave SDK apps, Android XR apps, and games that check an Oculus licence (they need the Oculus app on a PC) |

Automated launch tests confirm that a game starts; visuals can only be checked in the headset.

![Steam Frame](docs/images/frame.png)

The **Files** tab manages files on the Frame: upload videos, documents, mods or saves from the computer (buttons or
drag-and-drop), download, rename and delete (one entry or a selection), in the shared folders every game sees or in
one game's own storage.

![Files](docs/images/files.png)

## Built on

FramePort is a front end for other projects; most of the functionality comes from them:

| Project | Used for |
|---|---|
| [OVRPort](https://github.com/Android-XR-Bridge/OVRPort) (overport, originally [ovrport/app](https://github.com/ovrport/app)) | Converts Quest games to OpenXR: its CLI applies the game patches and supplies the OpenXR loader; FramePort also includes its VrApi→OpenXR adapter |
| Valve Lepton, Proton and SteamVR | Run Android games, Windows games and OpenXR on the Frame |
| [Revive](https://github.com/LibreVR/Revive) (LibreVR) | Runs Oculus Rift games on OpenXR / SteamVR |
| Mesa (Zink) | OpenGL ES on Vulkan on the Frame |
| [Khronos OpenXR SDK](https://github.com/KhronosGroup/OpenXR-SDK) | OpenXR headers for the native layers |
| Eclipse Temurin, Android apksigner, Android NDK | Java runtime, APK signing, building the native layers |
| [Flet](https://flet.dev) | The desktop app |
| OculusDB, Steam store | Game descriptions, genres and artwork |

FramePort's own parts: game detection and recipes, the Steam Frame OpenXR adapter (FrameBridge) and the other native
fixes in `native/`, the installer agent that runs on the Frame, and the desktop/command-line app.

## Command line

The same functions are available as `frameport` (included in the source tree; also released as a Python wheel):
`frameport scan`, `frameport build`, `frameport install`, `frameport test`, `frameport update`. Run
`frameport --help` for the full list.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md): installing, first launch, updating, Rift games, sending files, reporting problems.
- [docs/PLAYBOOK.md](docs/PLAYBOOK.md): symptoms and fixes per game.
- [docs/FRAME_RUNTIME.md](docs/FRAME_RUNTIME.md): Steam Frame runtime facts.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the code is organised.
- [CONTRIBUTING.md](CONTRIBUTING.md): contributing code, recipes and translations.

## Development

```
uv sync --extra dev
uv run frameport-gui        # the app
uv run pytest               # tests
```

See `CLAUDE.md` for project notes and `native/README.md` for the native components.

## License

GPL-3.0-only (it includes GPL-3.0 code from OVRPort). Not affiliated with Valve or Meta.
