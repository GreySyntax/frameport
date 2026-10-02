# Installing FramePort

Download the archive for your computer from the [latest release](https://github.com/spoopyghosty0/frameport/releases/latest)
and extract it anywhere. No installer or admin rights are needed. On first start FramePort downloads its Java
runtime, the OVRPort CLI and apksigner into its data folder (Settings → Tools shows them).

| Computer | Archive | Start |
|---|---|---|
| Windows 10/11 (x64) | `FramePort-windows-x64.zip` | `FramePort.exe` |
| macOS (Apple Silicon) | `FramePort-macos-arm64.zip` | `FramePort.app` |
| Linux (x64, GTK 3; Ubuntu 22.04 or newer) | `FramePort-linux-x64.tar.gz` | `FramePort/FramePort` |
| Command line only (Python 3.11+) | `frameport-<version>-py3-none-any.whl` | `frameport --help` |

The command-line version installs from the wheel's release link with `uv tool install <link>` (or pipx / pip).

## First launch

The builds are signed with a free self-signed certificate (Windows) and an ad-hoc signature (macOS), so the first
start shows a warning:

- **Windows:** "Windows protected your PC" → **More info** → **Run anyway**. Optional: import
  `FramePort-selfsigned.cer` (attached to each release) into *Trusted Root Certification Authorities* (Current User) to
  show FramePort as the publisher; the certificate can only sign code. Remove it with `certmgr.msc`.
- **macOS:** right-click `FramePort.app` → **Open** → **Open** (once), or `xattr -dr com.apple.quarantine FramePort.app`.
- **Linux:** `tar xzf FramePort-linux-x64.tar.gz && ./FramePort/FramePort`.

## Connecting the Steam Frame

1. On the Frame: Settings → System → Developer → turn on **Developer Mode**. The Frame and the computer must be on the
   same network.
2. In FramePort open **Steam Frame**. A Frame in Developer Mode appears in the list.
3. First time only: on the Frame switch to Desktop mode (Steam button → Power → Switch to Desktop), open Konsole and
   run the command FramePort shows. It enables SSH, authorises this computer and installs Valve's Android runtime
   (Lepton) if needed.

## Updating

FramePort checks for a new release at start and every 6 hours (it only downloads the release information). When one
exists, the Library shows **Update now**: FramePort downloads the new version, verifies it against the release's
`SHA256SUMS.txt` (on Windows also the signature), restarts and opens as the new version. Games, settings, signing keys
and the Frame connection are kept. Running installs finish first. **Later** skips that version.

Settings → **Updates**: turn the check off, or turn on **Install updates automatically** (downloads in the background,
installs at the next start). If FramePort's folder isn't writable, **Update now** opens the release page instead. The
update log is `logs/update.log` in the data folder.

Command line: `frameport update` (`--check` only checks, exit code 10 = update available; `--yes` doesn't ask).
`FRAMEPORT_NO_UPDATE_CHECK=1` turns all checks off.

## PC VR games

PC VR games are Windows VR games (OpenXR, SteamVR or Oculus). Scan a folder of them (one folder per game) or use
**Add games → Add one game folder…**. FramePort finds the game's program and asks when there is more than one
candidate. **Already patched** on a game page installs a copy unchanged. Oculus-only games need Revive: FramePort uses
an installed Revive, or downloads a portable copy.

- **Play from this PC:** Windows with Steam and SteamVR. **Install on this PC** adds the game to Steam; stream it to the
  Frame with Steam Link. If a game can't keep up with the refresh rate, FramePort lowers the rate and enables motion
  smoothing in SteamVR's per-game settings the next time you press Play.
- **Play on the Frame (experimental):** Steam Frame → *PC VR games (Proton)* → **Install**, then **Install on Frame**
  on the game page.
- Games that use the Oculus Platform SDK check the licence through the Oculus app, so they run on the PC only.

## Files on the Frame (videos, documents, mods, saves)

The **Files** tab manages files on the Frame over the same connection as installs; no other transfer app is needed.
Pick a location, browse folders, and use **Upload files** / **Upload folder**, **New folder**, or the download, rename
and delete buttons on each entry. Tick several entries (or the box above the list for all) to download or delete them
together. In the downloaded app you can also drag files and folders from Explorer / Finder / your file manager onto
the list to upload them into the open folder. Uploads and downloads run in the Activity panel, resume after an interruption and
skip files that are already there.

- **Videos**, **Downloads** and **Documents** appear inside every Quest game as `/sdcard/Movies`, `/sdcard/Download`
  and `/sdcard/Documents`. Apps find files by browsing folders; Android's media index doesn't work on the Frame.
- Under **Game storage**, each installed game has its own `/sdcard` (mods, saves). A game's menu → **Add videos and
  files…** opens it. Video players that list only their own folder (e.g. 4XVR's "Internal Storage" = `4XPlayer`) find
  videos uploaded into that folder.
- **Home folder** shows everything in the Frame's home folder (hidden files with **Show hidden files**).

Command line: `frameport frame send <files> --dest videos` (`frameport frame storage` lists the destinations).

## Sharing a working game, reporting a problem

- **Share working config…** (game menu): opens a prefilled GitHub issue with the game's patches and settings. Accepted
  configs become built-in recipes.
- **Report a problem…** (game menu, or Settings → Problems and feedback): saves a diagnostics zip to Documents (logs,
  recipe, device details; IP addresses, user names, home folders and Steam ids replaced) and opens a prefilled GitHub
  issue to attach it to. Command line: `frameport diag report <game>`, `frameport share-recipe <game>`.

## Command line

Everything the app does is also a `frameport` command (in a source checkout: `uv run frameport`); `frameport --help`
and `frameport <command> --help` describe every option. The main ones:

| Command | What it does |
|---|---|
| `scan <folder>` / `list` / `show <game>` | add games, list the library, show a game's analysis and patches |
| `recipe <game> --enable/--disable <patch>` | change a game's patches (`patches` lists them all) |
| `build <game>` / `install <game>` / `test <game>` | build, install on the Frame (`--to pc` for PC VR on this PC), launch test |
| `frame discover` / `frame connect` / `frame info` | find, pair with and describe the Frame |
| `frame send` / `frame storage` / `frame cleanup` | copy files to the Frame, show where they go, free space |
| `tools status` / `tools install` | the tools FramePort downloads |
| `diag report <game>` / `share-recipe <game>` | report a problem / share a working recipe |
| `update` | update FramePort |

Exit codes: 0 done, 1 something failed, 2 wrong usage, 10 (`update --check`) a newer version exists.

## Uninstalling

Settings → **Uninstall FramePort…** (or `frameport uninstall-app`) removes its data folder, the Steam shortcuts it
added on this computer and, optionally, its games on the Frame (saves can be kept). It first saves your signing keys
to Documents: game updates must be signed with the same key. Then delete the program folder.

## Verify a download

Each archive has a GitHub build attestation:
`gh attestation verify FramePort-windows-x64.zip -R spoopyghosty0/frameport`. `SHA256SUMS.txt` lists the checksums
(`sha256sum -c SHA256SUMS.txt`). Windows certificate SHA-256 fingerprint:
`4E:12:98:91:62:C0:E4:50:FB:65:1D:34:BB:73:00:09:7B:78:BE:88:5C:A7:6C:42:23:46:9B:92:A1:59:A7:6E`.
