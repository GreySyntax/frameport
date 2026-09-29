"""The Frame-side agent (stdlib only) — pieces that don't need a Frame."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

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
