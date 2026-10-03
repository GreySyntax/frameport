# Tested games

Games tested on the Steam Frame with FramePort's recipes. Games not listed here may work too: FramePort suggests patches for them, and a working config can be shared from the app (**Share working config…**).
Generated from [catalog/games](../catalog/games) by `scripts/compat_list.py`.

| Game | Platform | Status | Notes |
|---|---|---|---|
| 4XVR Video Player | Quest | ✅ Works |  |
| Asgard's Wrath 2 | Quest | ✅ Works |  |
| BAM | Quest | ✅ Works |  |
| BARTENDER VR SIMULATOR | Quest | ✅ Works |  |
| Batman: Arkham Shadow | Quest | ✅ Works |  |
| Carve Snowboarding | Quest | ✅ Works |  |
| Demeter | Quest | ✅ Works |  |
| Espire 2 | Quest | ✅ Works |  |
| Genotype | Quest | ✅ Works |  |
| H.U.N.T | Quest | ✅ Works |  |
| In Death: Unchained | Quest | ✅ Works |  |
| LEGO® Bricktales | Quest | ✅ Works |  |
| Marvel's Deadpool VR | Quest | ✅ Works |  |
| Medieval Dynasty New Settlement | Quest | ✅ Works |  |
| Mobile Suit Gundam: Silver Phantom | Quest | ✅ Works |  |
| Nano | Quest | ✅ Works |  |
| NEX Player | Quest | ✅ Works |  |
| NOPE CHALLENGE | Quest | ✅ Works |  |
| Path of the Warrior | Quest | ✅ Works |  |
| PowerWash Simulator VR | Quest | ✅ Works |  |
| Rick and Morty: Virtual Rick-ality | PC VR | ✅ Works |  |
| Robo Recall | Quest | ✅ Works |  |
| Sniper Elite VR: Winter Warrior | Quest | ✅ Works |  |
| Stremio | Quest | ✅ Works |  |
| The Climb 2 | Quest | ✅ Works |  |
| Toy Master | Quest | ✅ Works |  |
| Under Cover | Quest | ✅ Works |  |
| Wallace & Gromit in The Grand Getaway | Quest | ✅ Works |  |
| Arcsmith | Quest | ⚠️ Works with issues | Right eye distorts during movement (unresolved; swap, tracking, Valve layers, depth and pacing ruled out). |
| Assassin's Creed Nexus | Quest | ⚠️ Works with issues | Some launch warning text is still upside down; the rest of the UI is fixed by flip emulation. |
| Phantom: Covert Ops | Quest | ⚠️ Works with issues | DLC/store button crashes (no Meta store). |
| Silhouette | Quest | ⚠️ Works with issues | Hand-tracking game; the Frame synthesizes hands from controllers, so it is janky. |
| Time Stall | Quest | ⚠️ Works with issues | Both eyes distort during movement (unresolved). |
| Espire 1: VR Operative (Quest Edition) | Quest | ❌ Doesn't run | Mesa GL driver crash during texture upload. |
| HITMAN 3 VR: Reloaded | Quest | ❌ Doesn't run | Vulkan driver crash (freedreno), even without Valve layers. |
| Journey of the Gods | Quest | ❌ Doesn't run | 32-bit only; the Frame has no AArch32. |
| Shadow Point | Quest | ❌ Doesn't run | 32-bit only; the Frame has no AArch32. |
| Sniper Elite VR | Quest | ❌ Doesn't run | GPU hang (zink: DEVICE LOST) even with MSAA off. |
| Sports Scramble (Santa Cruz) | Quest | ❌ Doesn't run | 32-bit only; the Frame has no AArch32. |
