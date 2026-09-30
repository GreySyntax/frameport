# Installing FramePort

Download the archive for your OS from the [latest release](https://github.com/spoopyghosty0/frameport/releases/latest)
and extract it anywhere. No installer and no admin rights are needed. On first start, open **Tools → Install
missing** once: FramePort downloads its own Java runtime, the overport CLI and apksigner into its data folder.

| OS | Archive | Start |
|---|---|---|
| Windows 10/11 (x64) | `FramePort-windows-x64.zip` | `FramePort.exe` |
| macOS (Apple Silicon) | `FramePort-macos-arm64.zip` | `FramePort.app` |
| Linux (x64) | `FramePort-linux-x64.tar.gz` | `FramePort/FramePort` (needs GTK 3) |

## First launch

FramePort is free and signed with its own **self-signed** certificate (Windows) and **ad-hoc** signature (macOS),
not a paid one, so your OS warns the first time:

- **Windows:** SmartScreen shows "Windows protected your PC" → click **More info** → **Run anyway**.
  Optional, to make Windows show "FramePort" as a verified publisher: import `FramePort-selfsigned.cer` (attached
  to each release) into **Trusted Root Certification Authorities** (right-click → Install Certificate → Current User →
  "Place all certificates in the following store"). The certificate is limited to code signing and cannot issue other
  certificates. SmartScreen is reputation-based, so it may still warn for new versions. Only do this if you trust the
  builds from this repository; remove it any time with `certmgr.msc`.
- **macOS:** right-click `FramePort.app` → **Open** → **Open** (only needed once), or run
  `xattr -dr com.apple.quarantine FramePort.app`.
- **Linux:** `tar xzf FramePort-linux-x64.tar.gz && ./FramePort/FramePort`.

## Oculus Rift (PC VR) games
Scan a folder of Rift games (one folder per game; the game may sit a few levels down, next to installers and archives)
the same way as Quest dumps, or use **Add games → Add one game folder…**. FramePort finds the program that starts
each game; when it isn't sure (e.g. an Oculus and a Steam build side by side) it asks you. It also fetches artwork, descriptions, genres and
(for games also sold on Steam) screenshots, and gives each Steam library entry full artwork plus tags for how it
runs, its original platform (Meta Quest / Oculus Rift) and its genres. If your copies are already patched, switch on **Already patched** on the game page:
Quest APKs are installed unchanged, Rift games start without Revive. If you installed Revive
yourself (official installer), FramePort uses that one; otherwise it downloads its own portable copy (Tools shows which;
nothing is installed system-wide).
- **Play from this PC:** needs Windows with Steam and SteamVR. "Install on this PC" adds the game to your Steam
  library (Steam closes and reopens once); stream it to the Frame with Steam Link / SteamVR.
- **Play on the Frame (experimental):** Frame → "Install Proton" (confirm the download in the headset), then "Install
  on Frame (Proton)" on the game page.
- Games that use the Oculus Platform SDK (FramePort shows a note) check your Oculus license: they need the Oculus app
  installed on the PC with a license you own, so they can't run on the headset.

## Uninstalling
Settings → **Uninstall FramePort…** (or `frameport uninstall-app`) removes everything FramePort created: its data
folder (tools, library, artwork, cache, builds), the Steam shortcuts it added on this PC and, optionally, its games and
files on the Frame (saves can be kept). It first saves a zip of your signing keys to Documents (game updates must be
signed with the same key). Then delete the FramePort program folder.

## Verify a download

Every archive has a GitHub build attestation proving it was built by this repository's CI from a tagged commit:

```
gh attestation verify FramePort-windows-x64.zip -R spoopyghosty0/frameport
```

and `SHA256SUMS.txt` lists the checksums (`sha256sum -c SHA256SUMS.txt`). The Windows certificate's SHA-256
fingerprint is `4E:12:98:91:62:C0:E4:50:FB:65:1D:34:BB:73:00:09:7B:78:BE:88:5C:A7:6C:42:23:46:9B:92:A1:59:A7:6E`.
