"""Help texts for the GUI's non-obvious terms and actions, shown as "?" hints and tooltips (components.help_icon).
One place for the wording, so the same term is explained the same way everywhere. Plain text, no Flet."""
from __future__ import annotations

HELP: dict[str, str] = {
    # ---- game status / library
    "status": "How well the game runs on the Steam Frame. \"Works\" and \"Works with issues\" come from recipes tested "
              "on a real Frame. \"Untested\" means FramePort suggested a recipe from the game's engine and VR API that "
              "nobody has confirmed yet. \"Can't run\" means there's a known blocker (for example a 32-bit-only game).",
    "update_ready": "The copy on your Frame differs from what FramePort would install now (the recipe or your game "
                    "files changed). Update to install the new build; saves are kept.",
    "check_exe": "This game's folder has several programs and FramePort isn't sure which one starts the game. Open the "
                 "game (or right-click → Change executable) to pick it.",
    "platform_quest": "A Meta Quest game (APK). It's rebuilt for the Frame and runs in Lepton, Valve's Android "
                      "container.",
    "platform_pcvr": "An Oculus Rift PC VR game. It runs through Revive (Oculus → OpenXR), either on this PC or on the "
                     "Frame with Proton.",
    "select": "Pick several games and install them in one go. FramePort asks every question first, then works through "
              "the queue in the background.",
    "tags": "Your own tags (filled) can be anything you like. Outlined tags are added automatically from the engine, "
            "VR API and store genres. Filter the library by either.",
    # ---- game page
    "recipe": "A recipe is the list of patches FramePort applies to a game before installing it. Known-good recipes "
              "were tested on a Frame; the others are suggested from the game's engine and VR API. Hover a patch "
              "for why it's there.",
    "standard_fixes": "Fixes every game of this kind gets (on by default). Customize lists them all.",
    "as_is": "Turn this on if your copy is already patched. Quest: the APK is installed unchanged instead of being "
             "patched again. PC VR: your folder is never modified either way; only the Frame's copy gets launch fixes, "
             "and Revive still runs when the game uses the Oculus PC SDK (a repack's own Oculus runtime, such as "
             "Virtual Desktop's launcher, doesn't exist on the Frame). Turn Revive off under Customize for SteamVR "
             "builds.",
    "alt_build": "Some games need a second build with different overport patches (for some Unreal games: one with "
                 "Unreal's ForceQuit removed). Turn this on to install that build instead.",
    "show_all": "Patches that can't matter for this game (wrong engine or API) are hidden. Show them to force one on "
                "anyway.",
    "where": "Steam Frame: installed on the headset with an entry in its Steam library. This PC: a Steam shortcut on "
             "this Windows PC that starts the game through Revive, for a PC-tethered headset.",
    "launch_test": "Starts the game on the Frame while nobody is wearing it and reads the log: did it start, create a "
                   "VR session and render frames. It can't check what you'd see: tracking only runs with the "
                   "headset on.",
    "uninstall": "Removes the game from the Frame. Saves are kept, so a reinstall picks up where you left off.",
    "known_good": "Saves the current patches as a known-good recipe in your own catalog, so they're used again when "
                  "the game is re-added. Do this after it worked in the headset.",
    "rebuild": "Applies the patches and runs the checks, without sending anything to the Frame.",
    "reset_recipe": "Discards your changes to the patches and goes back to the recipe FramePort suggests.",
    "experimental": "Not verified on many games yet: try it if the game doesn't work without it.",
    "abis": "The CPU types the game ships code for. The Frame runs only 64-bit ARM (arm64-v8a); 32-bit-only games "
            "can't run on it.",
    "graphics": "OpenGL ES games run on the Frame through Zink (OpenGL on top of Vulkan), which is stricter than "
                "Quest drivers. Vulkan games run natively.",
    "recipe_source": "Where this game's recipe came from: catalog (tested on a Frame), heuristics (suggested by "
                     "FramePort) or user (changed by you).",
    # ---- patch categories
    "cat_pcvr": "How a Rift game starts: Revive translates the Oculus API to OpenXR, Proton runs the Windows program on "
                "the Frame.",
    "cat_frame": "Fixes for what the Frame's runtime does differently from a Quest (graphics formats, missing OpenXR "
                 "extensions, Lepton's launcher requirements).",
    "cat_overport": "overport converts Quest games from Meta's own VR APIs to standard OpenXR. These are its "
                    "optional patches.",
    "cat_adapter": "Settings of FrameBridge, the adapter FramePort adds to Quest games. They can also be changed "
                   "after installing, from the Frame page.",
    "cat_device": "Files and environment variables placed next to the game on the Frame.",
    # ---- Frame
    "developer_mode": "Developer Mode (on the Frame: Settings → System → Developer) lets FramePort find the Frame on "
                      "your network and connect to it over SSH.",
    "first_time_setup": "The command fetches a small setup script from this app over your local network. It turns on "
                        "SSH, lets this app's key in, makes the Frame findable and installs Lepton if needed.",
    "password": "The Frame's desktop password, only needed the first time so FramePort can add its own key. After "
                "that it connects with the key.",
    "lepton": "Lepton is Valve's Android container on the Frame. Quest games run inside it, one container per game. "
              "It needs Developer Mode.",
    "proton": "Proton is Valve's Windows compatibility layer. On the Frame it runs Oculus Rift games (with Revive). "
              "Its ARM64 build isn't installed by default; FramePort can install it (Steam restarts once).",
    "openxr": "The VR runtime games talk to. On the Frame that's SteamVR.",
    "kernel_keys": "Every game start on the Frame used to leak one kernel key, and after about 200 launches every game "
                   "fails to start. FramePort switches that leak off, but keys already used only come back after a "
                   "restart of the Frame.",
    "free_space": "Deletes the rollback copies kept from each game's previous install and leftover uploads. The "
                  "games and saves stay.",
    "adapter_settings": "Change FrameBridge settings of an installed game (render scale, controller mapping and so "
                        "on). They're read when the game starts.",
    "frame_summary": "Quest ✓: Lepton is installed, so Quest games can run. PC VR ✓: Proton is installed, so Rift games "
                     "can run on the Frame.",
    # ---- settings
    "data_folder": "FramePort's tools, library, artwork and the signing keys of your rebuilt games. Back it up: an "
                   "update must be signed with the same key, or the game (and its saves) has to be reinstalled.",
    "catalog": "Recipes tested on a real Frame: bundled with FramePort, fetched from the online catalog, and the ones "
               "you saved as known-good.",
    "revive": "Revive (by LibreVR) lets Oculus Rift games run on OpenXR headsets. FramePort uses your installed Revive "
              "if you have one, and never replaces it.",
    "steamvr_pc": "PC VR games on this PC run through SteamVR, so a headset connected to this PC works with them.",
    "frame_agent": "The small helper program FramePort runs on the Frame (installs, launch tests, Steam entries). It "
                   "comes with this app and is replaced on the Frame automatically whenever it differs.",
    "transfer_link": "Uploads use the fastest link to the Frame: a USB cable, or the Frame's own Wi-Fi hotspot when "
                     "this PC is connected to it, else your home network (several times slower: both go through "
                     "the router).",
    # ---- sharing / diagnostics
    "share_config": "Sends this game's recipe (the patches and settings it uses) to FramePort's GitHub as a prefilled "
                    "issue, so it can become a built-in recipe for everyone. You review and submit it in the browser; "
                    "no GitHub token and no game files are involved.",
    "diag_bundle": "A zip with FramePort's logs, the game's recipe and analysis, launch-test logs and the Frame's "
                   "runtime details — enough to debug without the game files. IP addresses, host and user names, "
                   "home folders and Steam ids are replaced by placeholders. Attach it to a GitHub issue.",
}


def text(key: str) -> str:
    return HELP[key]
