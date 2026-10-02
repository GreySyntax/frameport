"""The Frame-side agent (stdlib only) — pieces that don't need a Frame."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT = Path(__file__).resolve().parents[1] / "agent" / "frameport_agent.py"


def load_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("frameport_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vdf_roundtrip_and_upsert(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    vdf = tmp_path / "shortcuts.vdf"
    exe = '"/home/steamos/Applications/quest-frame/com.x.y/launch.sh"'
    appid = a.upsert_shortcut(str(vdf), exe, "My Game", "/start", "/icon.png")
    assert appid == a.shortcut_appid(exe, "My Game") and appid & 0x80000000
    root = a.vdf_decode(vdf.read_bytes())
    entry = root["shortcuts"]["0"]
    assert entry["appname"] == "My Game" and entry["Exe"] == exe and entry["OpenVR"] == 1
    # upsert keeps the id and doesn't duplicate
    assert a.upsert_shortcut(str(vdf), exe, "Renamed", "/start") == appid
    assert len(a.vdf_decode(vdf.read_bytes())["shortcuts"]) == 1
    assert a.vdf_encode(a.vdf_decode(vdf.read_bytes())) == vdf.read_bytes()
    assert a.remove_shortcut(str(vdf), exe)
    assert a.vdf_decode(vdf.read_bytes())["shortcuts"] == {}


def test_launcher_template(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    a.write_launcher(str(anchor), "/data/game dir/pkg", "com.x.y", "Game's Title", 123, "/lepton/lepton",
                     {"VK_INSTANCE_LAYERS": "", "bad-name": "x"})
    text = (anchor / "launch.sh").read_text()
    assert "export SteamAppId=123" in text and "app_dir='/data/game dir/pkg'" in text
    assert "export VK_INSTANCE_LAYERS=''" in text and "bad-name" not in text
    assert subprocess.run(["bash", "-n", str(anchor / "launch.sh")]).returncode == 0


def test_cli_protocol(tmp_path):
    p = subprocess.run([sys.executable, str(AGENT), "nope"], capture_output=True, text=True)
    assert p.returncode == 2 and json.loads(p.stdout)["ok"] is False
    p = subprocess.run([sys.executable, str(AGENT), "set_settings"], input=json.dumps({"package": "bad name"}),
                       capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert json.loads(p.stdout) == {"ok": False, "error": "bad package name 'bad name'"}


def test_host_fix_keyring(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    assert a.ensure_host_fixes() == ["podman keyring=false"]
    text = (tmp_path / ".config/containers/containers.conf").read_text()
    assert "[containers]" in text and "keyring = false" in text
    assert a.ensure_host_fixes() == []  # idempotent
    # an existing [containers] section gets the key inserted, not a second section
    conf = tmp_path / ".config/containers/containers.conf"
    conf.write_text("[engine]\nfoo = 1\n[containers]\nlog_size_max = 10\n")
    assert a.ensure_host_fixes() == ["podman keyring=false"]
    assert conf.read_text().count("[containers]") == 1 and "keyring = false" in conf.read_text()


def test_cleanup_refuses_outside_paths(monkeypatch, tmp_path):
    import pytest

    a = load_agent(monkeypatch, tmp_path)
    old = tmp_path / "PATCHED"
    (old / "x").mkdir(parents=True)
    (old / "x" / "f.apk").write_bytes(b"1234")
    r = a.cmd_cleanup({"paths": ["~/PATCHED"]})
    assert r["freed_bytes"] == 4 and not old.exists()
    for bad in ("/etc", "~/Applications/quest-frame", "~/Applications/quest-frame/x", "~/..", "~/.ssh", "~/.steam",
                "~/.local/share", "~"):
        with pytest.raises(a.AgentError):
            a.cmd_cleanup({"paths": [bad]})


# ------------------------------------------------------------------------------------------ PC VR under Proton
def fake_steam_tools(a, tmp_path):
    """A Steam library with an ARM64 Proton (needing a runtime) installed, like the Frame's."""
    apps = tmp_path / ".local/share/Steam/steamapps"
    (apps / "common/Proton 11.0 (ARM64)").mkdir(parents=True)
    (apps / "common/SteamLinuxRuntime_4-arm64").mkdir(parents=True)
    (apps / "common/Proton 11.0 (ARM64)/toolmanifest.vdf").write_text(
        '"manifest"\n{\n  "version" "2"\n  "commandline" "/proton %verb%"\n  "require_tool_appid" "4185400"\n}\n')
    (apps / "common/SteamLinuxRuntime_4-arm64/toolmanifest.vdf").write_text(
        '"manifest"\n{\n  "commandline" "/_v2-entry-point --verb=%verb% --"\n}\n')
    for appid, name, d in ((4628740, "Proton 11.0 (ARM64)", "Proton 11.0 (ARM64)"),
                           (4185400, "Steam Linux Runtime 4.0 - Arm64", "SteamLinuxRuntime_4-arm64")):
        (apps / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"4"\n'
            f'\t"installdir"\t\t"{d}"\n}}\n')
    a.arm64_compat_tools = lambda: {
        "proton_11-arm64": {"appid": 4628740, "display_name": "Proton 11.0-2 (ARM64)", "from_oslist": "windows",
                            "require_tool_appid": 4185400, "aliases": "proton-stable-arm64"},
        "proton-experimental-arm64": {"appid": 4427310, "display_name": "Proton Experimental (ARM64)",
                                      "from_oslist": "windows", "aliases": "proton-experimental"},
        "steamlinuxruntime_steamrt4-arm64": {"appid": 4185400, "from_oslist": "linux"},
    }
    return apps


def test_proton_status_and_command(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    apps = fake_steam_tools(a, tmp_path)
    st = a.cmd_proton_status({})
    assert st["ready"]["name"] == "proton_11-arm64"
    assert [t["name"] for t in st["tools"]] == ["proton_11-arm64", "proton-experimental-arm64"]  # stable first
    cmd = a.compat_command(st["ready"]["dir"])
    assert cmd == [str(apps / "common/SteamLinuxRuntime_4-arm64/_v2-entry-point"), "--verb=waitforexitandrun", "--",
                   str(apps / "common/Proton 11.0 (ARM64)/proton"), "waitforexitandrun"]
    assert a.pick_proton(st["tools"], "proton-experimental")["installed"] is False


def test_install_proton_request_mode(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    fake_steam_tools(a, tmp_path)
    calls = []
    monkeypatch.setattr(a.subprocess, "Popen", lambda args, **k: calls.append(args))
    r = a.cmd_install_proton({"tool": "proton-experimental-arm64"})
    assert r["requested"] == [4427310] and calls == [["steam", "-ifrunning", "steam://install/4427310"]]
    assert a.cmd_install_proton({})["installed"] is True


def test_stub_manifest(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    lib = tmp_path / "lib"
    lib.mkdir()
    assert a.write_stub_manifest(4427310, "Proton Experimental (ARM64)", "Proton Experimental (ARM64)", str(lib))
    text = (lib / "appmanifest_4427310.acf").read_text()
    assert '"StateFlags"\t\t"1026"' in text and '"installdir"\t\t"Proton Experimental (ARM64)"' in text
    assert not a.write_stub_manifest(4427310, "x", "x", str(lib))  # never overwrites a real manifest


def test_pcvr_install_flow(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    fake_steam_tools(a, tmp_path)
    monkeypatch.setattr(a, "pcvr_pids", lambda base: [])
    prep = a.cmd_prepare_pcvr({"package": "rift.space_game", "title": "Space Game"})
    inc = Path(prep["incoming"])
    (inc / "game/Space Game_Data").mkdir(parents=True)
    (inc / "game/Space Game.exe").write_bytes(b"MZexe")
    (inc / "game/Space Game_Data/level0").write_bytes(b"1234")
    (inc / "revive").mkdir(exist_ok=True)
    (inc / "revive/ReviveInjector.exe").write_bytes(b"MZ")
    (inc / "xrlayer").mkdir(exist_ok=True)
    layer_json = ('{"api_layer": {"name": "XR_APILAYER_FRAMEPORT_timefix", '
                  '"library_path": "./libxr_frameport_timefix.so"}}')
    (inc / "xrlayer/XR_APILAYER_FRAMEPORT_timefix.json").write_text(layer_json)
    (inc / "xrlayer/libxr_frameport_timefix.so").write_bytes(b"ELF")
    manifests = {"game": {"Space Game.exe": 5, "Space Game_Data/level0": 4}, "revive": {"ReviveInjector.exe": 2},
                 "xrlayer": {"XR_APILAYER_FRAMEPORT_timefix.json": len(layer_json), "libxr_frameport_timefix.so": 3}}
    r = a.cmd_finalize_pcvr({"package": "rift.space_game", "title": "Space Game", "exe": "Space Game.exe",
                             "manifests": manifests, "env": {"PROTON_LOG": "1", "bad key": "x"}, "xr_layer": True})
    assert r["ok"] and r["proton"] == "proton_11-arm64"
    launch = Path(prep["anchor"]) / "launch.sh"
    text = launch.read_text()
    assert subprocess.run(["bash", "-n", str(launch)]).returncode == 0
    assert "SteamGameId=" in text and "export PROTON_LOG=1" in text and "bad key" not in text
    assert "ReviveInjector.exe /openxr 'Z:" in text and "Space Game.exe'" in text
    assert 'XR_ENABLE_API_LAYERS="XR_APILAYER_FRAMEPORT_timefix' in text and 'XR_API_LAYER_PATH="$base/xrlayer' in text
    env = subprocess.run(["bash", "-c", text.split("export XDG_RUNTIME_DIR")[0].replace("set -euo pipefail", "set -eu")
                          .split("[[ -d")[0] + 'base=/b\n' + text.split("export PROTON_LOG_DIR=\"$base\"\n")[1]
                          .split("export XDG_RUNTIME_DIR")[0] + 'echo "$XR_API_LAYER_PATH|$XR_ENABLE_API_LAYERS"'],
                         capture_output=True, text=True)
    assert env.stdout.strip() == "/b/xrlayer|XR_APILAYER_FRAMEPORT_timefix", env.stderr
    # the layer is registered as an explicit layer in the user's XDG data dir (Proton's container drops
    # XR_API_LAYER_PATH), pointing at a shared absolute copy
    reg = json.loads((tmp_path / ".local/share/openxr/1/api_layers/explicit.d/XR_APILAYER_FRAMEPORT_timefix.json")
                     .read_text())
    lib = Path(reg["api_layer"]["library_path"])
    assert lib.is_absolute() and lib.read_bytes() == b"ELF"
    dep = a.deployment("rift.space_game")
    assert dep["kind"] == "pcvr" and dep["files"]["game"] == manifests["game"]
    listed = a.cmd_list_installed({})["games"]
    assert listed[0]["kind"] == "pcvr" and listed[0]["apk_present"] and "files" not in listed[0]
    # an update that drops a file removes it, and a new prepare sees what's there
    prep2 = a.cmd_prepare_pcvr({"package": "rift.space_game", "title": "Space Game"})
    assert prep2["existing"]["game"] == manifests["game"] and prep2["appid"] == prep["appid"]
    a.cmd_finalize_pcvr({"package": "rift.space_game", "title": "Space Game", "exe": "Space Game.exe",
                         "manifests": {"game": {"Space Game.exe": 5}, "revive": manifests["revive"]}})
    assert not (Path(prep["base"]) / "game/Space Game_Data/level0").exists()
    # uninstall keeps the Proton prefix (saves)
    (Path(prep["base"]) / "compatdata/pfx").mkdir(parents=True)
    assert a.cmd_uninstall({"package": "rift.space_game", "keep_data": True})["removed"]
    assert (Path(prep["base"]) / "compatdata/pfx").is_dir() and not (Path(prep["base"]) / "game").exists()


def test_pcvr_finalize_rejects_bad_exe(monkeypatch, tmp_path):
    import pytest

    a = load_agent(monkeypatch, tmp_path)
    fake_steam_tools(a, tmp_path)
    monkeypatch.setattr(a, "pcvr_pids", lambda base: [])
    with pytest.raises(a.AgentError):
        a.cmd_finalize_pcvr({"package": "rift.x", "title": "X", "exe": "../../etc/passwd", "manifests": {}})


def test_shortcut_tag_and_launch_options(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    vdf = tmp_path / "shortcuts.vdf"
    a.upsert_shortcut(str(vdf), '"C:\\Revive\\ReviveInjector.exe"', "Game", '"D:\\G\\"', "", "Rift via Revive",
                      '/openxr "D:\\G\\Game.exe"')
    e = a.vdf_decode(vdf.read_bytes())["shortcuts"]["0"]
    assert e["tags"] == {"0": "Rift via Revive"} and e["LaunchOptions"] == '/openxr "D:\\G\\Game.exe"'


def test_appinfo_parser(monkeypatch, tmp_path):
    """appinfo.vdf v29: header, one app (binary KV with string-table keys), string table."""
    import struct

    a = load_agent(monkeypatch, tmp_path)
    keys = ["appinfo", "appid", "common", "name", "extended", "compat_tools", "proton-x-arm64", "from_oslist"]
    k = {n: i for i, n in enumerate(keys)}

    def s(key, val):
        return b"\x01" + struct.pack("<I", k[key]) + val.encode() + b"\0"

    def m(key, body):
        return b"\x00" + struct.pack("<I", k[key]) + body + b"\x08"
    kv = m("appinfo", b"\x02" + struct.pack("<Ii", k["appid"], 7) + m("common", s("name", "Compat List")) +
           m("extended", m("compat_tools", m("proton-x-arm64", s("from_oslist", "windows") +
                                                         b"\x02" + struct.pack("<Ii", k["appid"], 99))))) + b"\x08"
    app = struct.pack("<I", 7) + struct.pack("<I", 60 + len(kv)) + b"\0" * 60 + kv
    body = app + struct.pack("<I", 0)
    str_off = 16 + len(body)
    table = struct.pack("<I", len(keys)) + b"".join(n.encode() + b"\0" for n in keys)
    data = struct.pack("<II", 0x07564429, 1) + struct.pack("<q", str_off) + body + table
    f = tmp_path / "appinfo.vdf"
    f.write_bytes(data)
    out = a.appinfo_entries(path=str(f))
    assert out[7]["common"]["name"] == "Compat List"
    assert out[7]["extended"]["compat_tools"]["proton-x-arm64"] == {"from_oslist": "windows", "appid": 99}


def test_run_tree_kills_the_whole_group(monkeypatch, tmp_path):
    """Wine leaves children holding the output open; a timeout must still return and kill them."""
    import time

    a = load_agent(monkeypatch, tmp_path)
    start = time.time()
    out, code = a.run_tree(["bash", "-c", "echo started; (sleep 60 &) ; sleep 60"], dict(a.os.environ), str(tmp_path),
                           str(tmp_path / "log"), 2)
    assert code is None and "started" in out and time.time() - start < 20
    out, code = a.run_tree(["bash", "-c", "echo ok; exit 3"], dict(a.os.environ), str(tmp_path), str(tmp_path / "l2"),
                           10)
    assert (out.strip(), code) == ("ok", 3)


def test_purge_removes_frameport_and_keeps_saves(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    fake_steam_tools(a, tmp_path)
    monkeypatch.setattr(a, "pcvr_pids", lambda base: [])
    monkeypatch.setattr(a, "stop_steam", lambda: True)
    monkeypatch.setattr(a, "start_steam", lambda s: None)
    users = tmp_path / ".local/share/Steam/userdata/42/config"
    (users / "grid").mkdir(parents=True)
    # a Quest-style install (base = anchor, saves in lepton-data) and a PC VR one (saves in compatdata)
    q = Path(a.ANCHORS) / "com.x.y"
    (q / "lepton-app").mkdir(parents=True)
    (q / "lepton-data" / "external").mkdir(parents=True)
    (q / "launch.sh").write_text("#!/bin/sh")
    (q / "deployment.json").write_text(json.dumps({"package": "com.x.y", "appid": 7, "base": str(q), "title": "Q"}))
    a.upsert_shortcut(str(users / "shortcuts.vdf"), f'"{q}/launch.sh"', "Q", str(q))
    (users / "grid" / "7p.png").write_bytes(b"x")
    agent_home = Path(a.AGENT_HOME)
    (agent_home / "agent").mkdir(parents=True)
    a.purge_worker(json.dumps({"keep_saves": True, "status": str(tmp_path / "st.json")}))
    st = json.loads((tmp_path / "st.json").read_text())
    assert st["state"] == "done", st
    assert (q / "lepton-data").is_dir() and not (q / "lepton-app").exists() and not (q / "launch.sh").exists()
    assert a.vdf_decode((users / "shortcuts.vdf").read_bytes())["shortcuts"] == {}
    assert not (users / "grid" / "7p.png").exists() and not agent_home.exists()
    # without keeping saves everything goes
    a.purge_worker(json.dumps({"keep_saves": False, "status": str(tmp_path / "st.json")}))
    assert not Path(a.ANCHORS).exists()


def test_shortcut_tags_merge(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    vdf = tmp_path / "shortcuts.vdf"
    exe = '"/x/launch.sh"'
    a.upsert_shortcut(str(vdf), exe, "G", "/x", tags=["Meta Quest", "Action"])
    root = a.vdf_decode(vdf.read_bytes())
    root["shortcuts"]["0"]["tags"]["9"] = "My collection"  # a tag the user set in Steam
    vdf.write_bytes(a.vdf_encode(root))
    a.upsert_shortcut(str(vdf), exe, "G", "/x", tags=["Meta Quest", "Action", "Indie"])
    tags = list(a.vdf_decode(vdf.read_bytes())["shortcuts"]["0"]["tags"].values())
    assert tags == ["Quest on Frame", "Meta Quest", "Action", "My collection", "Indie"]


def test_pcvr_launcher_oculus_hmd_helper(monkeypatch, tmp_path):
    """pcvr.oculus_unreal: launch.sh runs Revive's injector (or the game) through fp_oculushmd.exe."""
    import pytest

    a = load_agent(monkeypatch, tmp_path)
    fake_steam_tools(a, tmp_path)
    monkeypatch.setattr(a, "pcvr_pids", lambda base: [])

    def install(pkg, helper=True, revive=True, oculus_hmd=True):
        prep = a.cmd_prepare_pcvr({"package": pkg, "title": "UE Game"})
        inc = Path(prep["incoming"])
        (inc / "game").mkdir(parents=True, exist_ok=True)
        (inc / "game/UEGame.exe").write_bytes(b"MZexe")
        manifests = {"game": {"UEGame.exe": 5}}
        if revive:
            (inc / "revive").mkdir(exist_ok=True)
            (inc / "revive/ReviveInjector.exe").write_bytes(b"MZ")
            manifests["revive"] = {"ReviveInjector.exe": 2}
        if helper:
            (inc / "helpers").mkdir(exist_ok=True)
            (inc / "helpers/fp_oculushmd.exe").write_bytes(b"MZhelp")
            manifests["helpers"] = {"fp_oculushmd.exe": 6}
        a.cmd_finalize_pcvr({"package": pkg, "title": "UE Game", "exe": "UEGame.exe", "manifests": manifests,
                             "revive": revive, "oculus_hmd": oculus_hmd, "game_args": ["-nocrashreports"]})
        launch = Path(prep["anchor"]) / "launch.sh"
        assert subprocess.run(["bash", "-n", str(launch)]).returncode == 0
        return launch.read_text().splitlines()[-1], prep["base"]

    cmd, base = install("rift.ue_game")
    helper = f"{base}/helpers/fp_oculushmd.exe"
    assert helper in cmd and cmd.index(helper) < cmd.index("ReviveInjector.exe")
    # everything after the helper is a Windows command line: the injector by its Z: path, then the game + args
    assert f"'Z:{base}/revive/ReviveInjector.exe'".replace("/", "\\") in cmd
    assert (cmd.index("ReviveInjector.exe") < cmd.index("/openxr") < cmd.index("UEGame.exe")
            < cmd.index("-nocrashreports"))
    assert a.deployment("rift.ue_game")["oculus_hmd"] is True
    cmd, base = install("rift.ue_direct", revive=False)
    assert f"{base}/helpers/fp_oculushmd.exe" in cmd and "ReviveInjector" not in cmd and "'Z:" in cmd
    cmd, base = install("rift.ue_plain", helper=False, oculus_hmd=False)
    assert "fp_oculushmd" not in cmd and f"{base}/revive/ReviveInjector.exe /openxr" in cmd
    with pytest.raises(a.AgentError, match="fp_oculushmd.exe missing"):
        install("rift.ue_nohelper", helper=False)


def test_list_files(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    base = tmp_path / "Applications/quest-frame/rift.g"
    (base / "game/Bin").mkdir(parents=True)
    (base / "game/Bin/Game.exe").write_bytes(b"MZ12")
    (base / "game/Bin/CrashReportClient.exe.disabled").write_bytes(b"MZ")
    (base / "launch.sh").write_text("#!/bin/sh\n")
    (base / "deployment.json").write_text(json.dumps({
        "package": "rift.g", "kind": "pcvr", "base": str(base),
        "files": {"game": {"Bin/Game.exe": 4, "Bin/Data.pak": 10, "Bin/CrashReportClient.exe": 2}}}))
    r = a.cmd_list_files({"package": "rift.g"})
    assert len(r["roots"]) == 1 and r["roots"][0]["name"] == "Install folder"  # the anchor is the same folder
    files = dict(map(tuple, r["roots"][0]["files"]))
    assert files["game/Bin/Game.exe"] == 4 and "launch.sh" in files
    assert r["missing"] == [["game/Bin/Data.pak", 10, None]]  # the renamed crash reporter isn't "missing"
    assert not r["truncated"]


def test_launch_uses_steam_shortcut(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    anchor = tmp_path / "Applications/quest-frame/com.x.y"
    anchor.mkdir(parents=True)
    deployment = {"package": "com.x.y", "appid": 2546384938, "base": str(anchor)}
    (anchor / "deployment.json").write_text(json.dumps(deployment))
    calls = []
    monkeypatch.setattr(a, "run", lambda cmd, **k: (calls.append(cmd), SimpleNamespace(returncode=0, stdout=""))[1])
    r = a.cmd_launch({"package": "com.x.y"})
    assert r["gameid"] == (2546384938 << 32) | 0x02000000
    assert calls[-1][-1] == f"steam://rungameid/{r['gameid']}" and calls[-1][0] == "systemd-run"


def test_uninstall_quest_keeping_saves_drops_the_install_record(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    anchor = tmp_path / "Applications/quest-frame/com.x.y"
    (anchor / "lepton-app").mkdir(parents=True)
    (anchor / "lepton-app/game.apk").write_bytes(b"PK")
    (anchor / "lepton-data/saves").mkdir(parents=True)
    (anchor / "lepton-data/saves/slot1").write_text("progress")
    (anchor / "launch.sh").write_text("#!/bin/sh\n")
    (anchor / "deployment.json").write_text(json.dumps({"package": "com.x.y", "base": str(anchor), "appid": 1}))
    monkeypatch.setattr(a, "container_running", lambda appid: False)
    assert a.cmd_uninstall({"package": "com.x.y"})["removed"]
    assert not a.cmd_list_installed({})["games"]  # no longer reported as installed
    assert (anchor / "lepton-data/saves/slot1").read_text() == "progress"  # saves kept


def test_prune_shortcuts_on_relaunch_change(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    vdf = str(tmp_path / "shortcuts.vdf")
    old = a.upsert_shortcut(vdf, '"C:\\Revive\\ReviveInjector.exe"', "Vader", "/d", tag="Rift via Revive",
                            tags=["Rift via Revive"])
    a.upsert_shortcut(vdf, '"C:\\Other.exe"', "Other game", "/d", tag="Rift via Revive", tags=["Rift via Revive"])
    removed = a.prune_shortcuts(vdf, "Vader", '"C:\\game\\WKND.exe"', "Rift via Revive")  # new direct launch
    assert removed == [old]
    root = a.vdf_decode(open(vdf, "rb").read())
    names = {v.get("appname") for v in root["shortcuts"].values() if isinstance(v, dict)}
    assert names == {"Other game"}  # the stale Vader entry is gone, the unrelated one stays
    # re-adding Vader directly, then pruning again, is a no-op for the kept entry
    a.upsert_shortcut(vdf, '"C:\\game\\WKND.exe"', "Vader", "/d", tag="Rift via Revive", tags=["Rift via Revive"])
    assert a.prune_shortcuts(vdf, "Vader", '"C:\\game\\WKND.exe"', "Rift via Revive") == []


def test_libovr_redirect_symlink(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    base = tmp_path / "b"
    (base / "game/Bin/Win64").mkdir(parents=True)
    (base / "game/Bin/Win64/Game.exe").write_bytes(b"MZ")
    (base / "revive").mkdir()
    (base / "revive/LibReviveXR64.dll").write_bytes(b"REVIVE")
    # an Unreal-style OVRPlugin.dll in its own dir -> the redirect must land there too
    (base / "game/Engine/Plug/OVRPlugin/Win64").mkdir(parents=True)
    (base / "game/Engine/Plug/OVRPlugin/Win64/OVRPlugin.dll").write_bytes(b"MZ")
    exe_rel = "Bin/Win64/Game.exe"
    link = base / "game/Bin/Win64/LibOVRRT64_1.dll"
    plugin_link = base / "game/Engine/Plug/OVRPlugin/Win64/LibOVRRT64_1.dll"
    a.set_libovr_redirect(str(base), exe_rel, enabled=True)
    assert link.is_symlink() and link.read_bytes() == b"REVIVE"  # redirect to Revive's runtime (exe dir)
    assert plugin_link.is_symlink() and plugin_link.read_bytes() == b"REVIVE"  # and next to OVRPlugin.dll
    assert not (base / "game/Bin/Win64/LibOVRRT32_1.dll").exists()  # no 32-bit Revive dll -> not created
    # disabling removes our symlink
    a.set_libovr_redirect(str(base), exe_rel, enabled=False)
    assert not link.exists()
    # never clobber a real game-shipped LibOVRRT
    real = base / "game/Bin/Win64/LibOVRRT64_1.dll"
    real.write_bytes(b"REAL")
    a.set_libovr_redirect(str(base), exe_rel, enabled=True)
    assert not real.is_symlink() and real.read_bytes() == b"REAL"


def test_libovr_redirect_to_bundled_revive(monkeypatch, tmp_path):
    """A repack's own LibRevive64.dll (no FramePort Revive) becomes the LibOVRRT the game loads on the Frame."""
    a = load_agent(monkeypatch, tmp_path)
    base = tmp_path / "b"
    (base / "game/G").mkdir(parents=True)
    (base / "game/G/Game.exe").write_bytes(b"MZ")
    (base / "game/G/LibRevive64.dll").write_bytes(b"BUNDLED")
    (base / "revive").mkdir()
    (base / "revive/LibReviveXR64.dll").write_bytes(b"REVIVE")
    link = base / "game/G/LibOVRRT64_1.dll"
    a.set_libovr_redirect(str(base), "G/Game.exe", enabled=True)
    assert link.read_bytes() == b"REVIVE"
    a.set_libovr_redirect(str(base), "G/Game.exe", enabled=True, bundled=True)  # switches target
    assert link.is_symlink() and link.read_bytes() == b"BUNDLED"
    a.set_libovr_redirect(str(base), "G/Game.exe", enabled=False, bundled=True)
    assert not link.exists() and (base / "game/G/LibRevive64.dll").read_bytes() == b"BUNDLED"


def test_proton_launcher_game_args(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(a, "compat_command", lambda d: ["/proton", "waitforexitandrun"])
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    a.write_proton_launcher(str(anchor), "/b", "rift.x", "X", 1, {"dir": "/p", "name": "proton"}, "G/X.exe", False,
                            {"WINEDLLOVERRIDES": "xinput1_3=n,b"}, game_args=["-vrmode", "OpenVR", "-hmd=OpenXR",
                                                                              "$(rm -rf /)"])
    text = (anchor / "launch.sh").read_text()
    assert "/b/game/G/X.exe -vrmode OpenVR -hmd=OpenXR >>" in text and "rm -rf" not in text
    assert "export WINEDLLOVERRIDES=xinput1_3=n,b" in text


PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8"
                        "cfc0f01f0005000201a5d6f1c80000000049454e44ae426082")


def _fake_render_model(root, name, grip_origin=(0.0, 0.0, 0.0), grip_rot=(0, 0, 0)):
    d = root / "resources/rendermodels" / name
    d.mkdir(parents=True)
    (d / "diffuse.png").write_bytes(PNG_1PX)
    (d / "body.mtl").write_text("newmtl skin\nmap_Kd diffuse.png\n")
    # a quad (fan-triangulated) with v/vt/vn corners, plus a negative-index face
    (d / "body.obj").write_text("mtllib body.mtl\nusemtl skin\n"
                                "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\n"
                                "f 1/1/1 2/2/1 3/3/1 4/4/1\nf -4/-4/-1 -3/-3/-1 -1/-1/-1\n")
    (d / "status.obj").write_text("v 5 5 5\nv 6 5 5\nv 5 6 5\nf 1 2 3\n")
    (d / f"{name}.json").write_text(json.dumps({"components": {
        "body": {"filename": "body.obj"}, "status": {"filename": "status.obj"},
        "openxr_grip": {"component_local": {"origin": list(grip_origin), "rotate_xyz": list(grip_rot)}}}}))
    return d


def _read_glb(data):
    import struct as st
    magic, version, total = st.unpack_from("<III", data)
    assert magic == 0x46546C67 and version == 2 and total == len(data)
    jlen, jtype = st.unpack_from("<I4s", data, 12)
    assert jtype == b"JSON" and jlen % 4 == 0
    gltf = json.loads(data[20:20 + jlen])
    blen, btype = st.unpack_from("<I4s", data, 20 + jlen)
    assert btype == b"BIN\x00" and blen == gltf["buffers"][0]["byteLength"]
    return gltf, data[28 + jlen:28 + jlen + blen]


def test_controller_models_pick_and_convert(monkeypatch, tmp_path):
    import struct as st
    a = load_agent(monkeypatch, tmp_path)
    root = tmp_path / "steamvr"
    _fake_render_model(root, "valve_frame_controller_left", grip_origin=(1.0, 0.0, 0.0))
    _fake_render_model(root, "valve_frame_controller_right", grip_rot=(0, 0, 90))
    _fake_render_model(root, "vr_controller_vive_1_5")
    dirs = a.render_model_dirs([str(root)])
    assert set(dirs) == {"valve_frame_controller_left", "valve_frame_controller_right", "vr_controller_vive_1_5"}
    picked = a.pick_controller_models(dirs)
    assert picked == {"left": dirs["valve_frame_controller_left"], "right": dirs["valve_frame_controller_right"]}
    assert a.pick_controller_models({"frame_left": "/x"}) == {}  # needs both sides
    assert a.model_side("controller_r") == "right" and a.model_side("hmd") is None

    gltf, blob = _read_glb(a.controller_glb(picked["left"]))
    prim = gltf["meshes"][0]["primitives"]
    assert len(prim) == 1  # status component skipped, one texture
    acc = gltf["accessors"]
    pos = acc[prim[0]["attributes"]["POSITION"]]
    assert pos["count"] == 4 and acc[prim[0]["indices"]]["count"] == 9  # 4 unique corners, 3 triangles
    # grip origin (1, 0, 0) -> vertices shifted by -1 on x
    assert pos["min"] == [-1.0, 0.0, 0.0] and pos["max"] == [0.0, 1.0, 0.0]
    assert gltf["images"][0]["mimeType"] == "image/png"
    view = gltf["bufferViews"][gltf["images"][0]["bufferView"]]
    assert blob[view["byteOffset"]:view["byteOffset"] + view["byteLength"]] == PNG_1PX
    uv_view = gltf["bufferViews"][acc[prim[0]["attributes"]["TEXCOORD_0"]]["bufferView"]]
    uvs = st.unpack_from("<8f", blob, uv_view["byteOffset"])
    assert uvs[:2] == (0.0, 1.0)  # OBJ (0, 0) -> glTF (0, 1)

    # 90° about z: raw +x becomes grip -y (R^T applied)
    gltf, blob = _read_glb(a.controller_glb(picked["right"]))
    pos = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    x = st.unpack_from("<6f", blob, gltf["bufferViews"][pos["bufferView"]]["byteOffset"])
    assert abs(x[3]) < 1e-6 and abs(x[4] + 1.0) < 1e-6  # vertex (1, 0, 0)


def test_controller_models_install(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    root = tmp_path / "steamvr"
    _fake_render_model(root, "frame_controller_left")
    _fake_render_model(root, "frame_controller_right")
    monkeypatch.setattr(a, "steamvr_roots", lambda: [str(root)])
    files = tmp_path / "files"
    result = a.install_controller_models(str(files), True)
    assert result["ok"] and set(result["sources"]) == {"left", "right"}
    left = files / "framebridge/controller_left.glb"
    assert left.read_bytes()[:4] == b"glTF" and (files / "framebridge/controller_right.glb").exists()
    assert len(list((tmp_path / ".local/share/frameport/controller-models").glob("*.glb"))) == 2  # cached
    assert a.install_controller_models(str(files), False) is None and not left.exists()
    monkeypatch.setattr(a, "steamvr_roots", lambda: [])
    missing = a.install_controller_models(str(files), True)
    assert missing["ok"] is False and "no Steam Frame controller render models" in missing["error"]
    assert a.cmd_controller_models({})["picked"] == {}


def test_storage_targets_follow_lepton(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    lepton = tmp_path / "Lepton"
    (lepton / "liblepton").mkdir(parents=True)
    (lepton / "lepton").write_text("#!/bin/sh\n")
    (lepton / "liblepton/mounting.sh").write_text(
        'ln -s "${HOME}/Videos" "${TARGET_PATH}/Movies"\n'
        'ln -s "${HOME}/Downloads" "${TARGET_PATH}/Download"\n')
    monkeypatch.setattr(a, "lepton_path", lambda: (str(lepton / "lepton"), None))
    base = tmp_path / "Applications/quest-frame/com.x.y"
    base.mkdir(parents=True)
    (base / "deployment.json").write_text(json.dumps({"package": "com.x.y", "base": str(base)}))
    t = {x["id"]: x for x in a.cmd_storage_targets({"package": "com.x.y"})["targets"]}
    assert t["videos"]["android"] == "/sdcard/Movies" and t["videos"]["shared"]
    assert t["downloads"]["android"] == "/sdcard/Download" and "documents" not in t  # read from Lepton, not assumed
    assert (tmp_path / "Videos").is_dir()  # Lepton only links folders that exist
    assert t["app"]["path"].endswith("lepton-data/external") and t["app"]["android"] == "/sdcard"
    assert t["app-files"]["android"] == "/sdcard/Android/data/com.x.y/files"
    # without Lepton's script: the known defaults
    monkeypatch.setattr(a, "lepton_path", lambda: (None, None))
    ids = {x["id"] for x in a.cmd_storage_targets({})["targets"]}
    assert ids == {"documents", "downloads", "videos"}


def test_link_media_into_app_own_folder(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(a, "lepton_path", lambda: (None, None))  # default shared folders
    base = tmp_path / "Applications/quest-frame/cn.player"
    ext = base / "lepton-data/external"
    for d in ("4XPlayer", "Android", "DCIM", "Music", ".hidden"):
        (ext / d).mkdir(parents=True)
    (tmp_path / "Videos").mkdir()
    (ext / "Movies").symlink_to(tmp_path / "Videos")  # Lepton's link to the shared folder: not the app's own
    (base / "deployment.json").write_text(json.dumps({"package": "cn.player", "base": str(base)}))
    assert a.app_media_dirs(str(ext)) == ["4XPlayer"]
    t = {x["id"]: x for x in a.cmd_storage_targets({"package": "cn.player"})["targets"]}
    assert t["app-media"]["android"] == "/sdcard/4XPlayer"
    video = tmp_path / "Videos/a_360.mp4"
    video.write_bytes(b"x" * 10)
    r = a.cmd_link_media({"package": "cn.player", "files": [str(video), str(tmp_path / "Videos/gone.mp4")]})
    assert r["folder"] == "4XPlayer" and r["linked"] == ["a_360.mp4"] and r["missing"]
    assert (ext / "4XPlayer/a_360.mp4").stat().st_ino == video.stat().st_ino  # a hard link, not a copy
    assert a.cmd_link_media({"package": "cn.player", "files": [str(video)]})["existing"] == ["a_360.mp4"]
    with pytest.raises(a.AgentError):  # only files from the shared folders
        a.cmd_link_media({"package": "cn.player", "files": [str(base / "deployment.json")]})


def test_grid_files_match_exact_appids(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    grid = tmp_path / "grid"
    grid.mkdir()
    for n in ("123p.jpg", "123.png", "123_hero.jpg", "123_logo.png", "1234p.jpg", "12345_hero.jpg", "notes.txt"):
        (grid / n).write_text("x")
    assert sorted(p.rsplit("/", 1)[1] for p in a.grid_files(str(grid), 123)) == \
        ["123.png", "123_hero.jpg", "123_logo.png", "123p.jpg"]


def test_vdf_backups_are_capped(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    vdf = tmp_path / "shortcuts.vdf"
    vdf.write_bytes(b"x")
    for i in range(9):
        (tmp_path / f"shortcuts.vdf.backup-2026010{i}-000000").write_bytes(b"old")
    a.backup_vdf(str(vdf))
    assert len(list(tmp_path.glob("shortcuts.vdf.backup-*"))) == a.VDF_BACKUPS


def test_finalize_checks_the_data_before_replacing_the_game(monkeypatch, tmp_path):
    """A failed data check must leave the installed APK and data as they were."""
    a = load_agent(monkeypatch, tmp_path)
    base = tmp_path / "Applications/quest-frame/com.x.y"
    app = base / "lepton-app"
    (app / "obb").mkdir(parents=True)
    (app / "game.apk").write_bytes(b"OLD")
    inc = base / "incoming"
    (inc / "obb").mkdir(parents=True)
    (inc / "game.apk").write_bytes(b"NEW")
    monkeypatch.setattr(a, "cmd_prepare", lambda args: {"base": str(base), "anchor": str(base), "appid": 1,
                                                         "incoming": str(inc), "lepton": "/lepton"})
    with pytest.raises(a.AgentError):
        a.cmd_finalize({"package": "com.x.y", "title": "X", "obb_manifest": {"main.obb": 10}})
    assert (app / "game.apk").read_bytes() == b"OLD" and (inc / "game.apk").exists()


def test_uninstall_removes_the_shortcut_with_steam_closed(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    anchor = tmp_path / "Applications/quest-frame/com.x.y"
    anchor.mkdir(parents=True)
    (anchor / "deployment.json").write_text(json.dumps({"package": "com.x.y", "base": str(anchor), "appid": 7}))
    monkeypatch.setattr(a, "container_running", lambda appid: False)
    monkeypatch.setattr(a, "steam_users", lambda: ["1"])
    started = []
    monkeypatch.setattr(a, "run", lambda cmd, *k, **kw: started.append(cmd) or SimpleNamespace(returncode=0, stdout=""))
    r = a.cmd_uninstall({"package": "com.x.y", "remove_shortcut": True})
    assert r["shortcut_removed"] and started and started[0][0] == "systemd-run"  # the detached worker, not a live edit
    payload = json.loads(started[0][-1])
    assert payload["remove"] == [{"exe": f'"{anchor}/launch.sh"', "appid": 7}]


def test_prune_also_matches_the_earlier_tag_name(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    vdf = str(tmp_path / "shortcuts.vdf")
    old = a.upsert_shortcut(vdf, '"C:\\Revive\\ReviveInjector.exe"', "Vader", "/d", tag="Rift via Revive")
    assert a.prune_shortcuts(vdf, "Vader", '"C:\\game\\WKND.exe"', ("FramePort PC VR", "Rift via Revive")) == [old]


def test_flatscreen_marker_for_android_apps_without_vr(monkeypatch, tmp_path):
    a = load_agent(monkeypatch, tmp_path)
    a.set_flatscreen(str(tmp_path), True)
    assert (tmp_path / "lepton-show-flatscreen").exists()
    a.set_flatscreen(str(tmp_path), True)  # idempotent
    a.set_flatscreen(str(tmp_path), False)
    assert not (tmp_path / "lepton-show-flatscreen").exists()
    a.set_flatscreen(str(tmp_path), False)


def test_flat_apps_hide_androids_navigation_bar(monkeypatch, tmp_path):
    """Only apps with Lepton's flat-window marker get qemu.hw.mainkeys=1 (no back/home/recents bar over the app)."""
    import subprocess

    a = load_agent(monkeypatch, tmp_path)
    a.write_launcher(str(tmp_path), "/b", "p", "T", 1, "/l", {})
    script = (tmp_path / "launch.sh").read_text()
    block = script[script.index("if [[ -f \"$app_dir/lepton-app/lepton-show-flatscreen\" ]]"):]
    block = block[:block.index("fi\n") + 3]
    for marker, expected in ((True, "0\nqemu.hw.mainkeys=1"), (False, "")):
        app = tmp_path / ("flat" if marker else "vr")
        (app / "lepton-app").mkdir(parents=True)
        if marker:
            (app / "lepton-app" / "lepton-show-flatscreen").touch()
        out = subprocess.run(["bash", "-c", f'app_dir={app}\n{block}printf %s "${{LEPTON_GFXRECON_FP_PROPS:-}}"'],
                             capture_output=True, text=True, check=True).stdout
        assert out == expected
