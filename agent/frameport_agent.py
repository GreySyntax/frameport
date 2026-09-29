#!/usr/bin/env python3
"""FramePort agent: runs ON the Steam Frame (SteamOS, python3 stdlib only).

The PC app uploads this file to ~/.local/share/frameport/agent/ and calls:
    python3 frameport_agent.py <command>        (JSON arguments on stdin, one JSON object on stdout)

Commands: info, prepare, finalize, shortcuts, shortcut_status, launch_test, stop, set_settings, uninstall,
          install_lepton, list_installed.

Install layout (one Lepton container per game; same as the manual installs from 2026-09):
    ~/Applications/quest-frame/<pkg>/            anchor: launch.sh, deployment.json, artwork/ (always internal storage)
    <dest>/<pkg>/lepton-app/{game.apk,obb/}      game files (dest defaults to ~/Applications/quest-frame)
    <dest>/<pkg>/lepton-data/                    container data + saves (kept across reinstalls)
    <dest>/<pkg>/lepton-shaders/, settings.conf, launch.log
"""
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
import zlib

AGENT_VERSION = 3
HOME = os.path.expanduser("~")
STEAM = os.path.join(HOME, ".local/share/Steam")
ANCHORS = os.path.join(HOME, "Applications/quest-frame")
LEPTON_APPID = "3029110"  # fallback when no appmanifest names Lepton
PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


class AgentError(Exception):
    pass


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pkg(pkg):
    if not pkg or not PKG_RE.match(pkg):
        raise AgentError(f"bad package name {pkg!r}")
    return pkg


# ------------------------------------------------------------------------------------------ Steam / Lepton discovery
def steam_libraries():
    libs = [os.path.join(STEAM, "steamapps")]
    vdf = os.path.join(STEAM, "steamapps/libraryfolders.vdf")
    try:
        for path in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8", errors="replace").read()):
            p = os.path.join(path, "steamapps")
            if p not in libs and os.path.isdir(p):
                libs.append(p)
    except OSError:
        pass
    return libs


def find_app(name_regex):
    for lib in steam_libraries():
        for acf in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            try:
                text = open(acf, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            name = re.search(r'"name"\s+"([^"]*)"', text)
            if name and re.fullmatch(name_regex, name[1]):
                appid = re.search(r'"appid"\s+"(\d+)"', text)[1]
                installdir = re.search(r'"installdir"\s+"([^"]*)"', text)[1]
                return {"appid": appid, "name": name[1], "dir": os.path.join(lib, "common", installdir)}
    return None


def lepton_path():
    app = find_app(r"Lepton")
    candidates = ([os.path.join(app["dir"], "lepton")] if app else []) + [os.path.join(STEAM, "steamapps/common/Lepton/lepton")]
    for c in candidates:
        if os.access(c, os.X_OK):
            return c, app
    return None, app


def steam_users():
    return sorted(d for d in os.listdir(os.path.join(STEAM, "userdata")) if d.isdigit() and d != "0") \
        if os.path.isdir(os.path.join(STEAM, "userdata")) else []


def container_running(appid):
    p = run(["podman", "ps", "--format", "{{.Names}}"])
    return f"lepton-steamlaunch-{appid}" in p.stdout.split()


def cmd_info(args):
    lepton, app = lepton_path()
    osr = {}
    try:
        for line in open("/etc/os-release"):
            k, _, v = line.strip().partition("=")
            osr[k] = v.strip('"')
    except OSError:
        pass
    st = os.statvfs(HOME)
    return {
        "agent_version": AGENT_VERSION, "hostname": os.uname().nodename, "arch": os.uname().machine,
        "os": osr.get("NAME"), "os_version": osr.get("VERSION_ID"), "build_id": osr.get("BUILD_ID"),
        "lepton": lepton, "lepton_app": app, "lepton_dev": find_app(r"Lepton Development"),
        "steam_users": steam_users(), "free_bytes": st.f_bavail * st.f_frsize,
        "installed": cmd_list_installed({})["games"],
        "steam_running": run(["pgrep", "-x", "steam"]).returncode == 0,
    }


def cmd_install_lepton(args):
    """Ask Steam to install Lepton (requires Developer Mode)."""
    lepton, app = lepton_path()
    if lepton:
        return {"installed": True, "path": lepton}
    appid = (app or {}).get("appid") or args.get("appid") or LEPTON_APPID
    subprocess.Popen(["steam", f"steam://install/{appid}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return {"installed": False, "requested": appid,
            "hint": "Enable Developer Mode, then confirm the install in Steam (or launch 'Lepton Development' once)."}


# ------------------------------------------------------------------------------------------ Steam shortcuts (binary VDF)
TYPE_MAP, TYPE_STRING, TYPE_INT, TYPE_END = 0, 1, 2, 8


def shortcut_appid(exe, title):
    return zlib.crc32((exe + title).encode("utf-8")) | 0x80000000


def vdf_decode(data):
    pos = 0

    def cstring():
        nonlocal pos
        end = data.index(b"\0", pos)
        value = data[pos:end].decode("utf-8", "surrogateescape")
        pos = end + 1
        return value

    def node():
        nonlocal pos
        out = {}
        while True:
            kind = data[pos]
            pos += 1
            if kind == TYPE_END:
                return out
            key = cstring()
            if kind == TYPE_MAP:
                value = node()
            elif kind == TYPE_STRING:
                value = cstring()
            elif kind == TYPE_INT:
                value = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                raise AgentError(f"unsupported VDF value type {kind}; not modifying shortcuts.vdf")
            if key in out:
                raise AgentError("duplicate VDF key; not modifying shortcuts.vdf")
            out[key] = value

    root = node()
    if any(b != TYPE_END for b in data[pos:]):
        raise AgentError("unexpected trailing data in shortcuts.vdf; not modifying it")
    return root


def vdf_encode(obj):
    out = bytearray()
    for key, value in obj.items():
        name = key.encode("utf-8", "surrogateescape") + b"\0"
        if isinstance(value, dict):
            out += bytes([TYPE_MAP]) + name + vdf_encode(value)
        elif isinstance(value, str):
            out += bytes([TYPE_STRING]) + name + value.encode("utf-8", "surrogateescape") + b"\0"
        else:
            out += bytes([TYPE_INT]) + name + struct.pack("<I", value & 0xFFFFFFFF)
    return bytes(out) + bytes([TYPE_END])


def upsert_shortcut(vdf_path, exe, title, start_dir, icon=""):
    data = open(vdf_path, "rb").read() if os.path.exists(vdf_path) else b""
    root = vdf_decode(data) if data else {"shortcuts": {}}
    shortcuts = root.setdefault("shortcuts", {})
    entry = next((v for v in shortcuts.values() if isinstance(v, dict) and v.get("Exe") == exe), None)
    ident = entry["appid"] if entry else shortcut_appid(exe, title)
    if entry is None:
        entry = {"appid": ident, "LastPlayTime": 0, "tags": {"0": "Quest on Frame"}}
        shortcuts[str(max([int(k) for k in shortcuts if k.isdigit()] + [-1]) + 1)] = entry
    entry.update(appname=title, Exe=exe, StartDir=start_dir, icon=icon or entry.get("icon", ""), ShortcutPath="",
                 LaunchOptions="", IsHidden=0, AllowDesktopConfig=1, AllowOverlay=1, OpenVR=1, Devkit=0,
                 DevkitGameID="", DevkitOverrideAppID=0, FlatpakAppID="")
    if data:
        shutil.copy2(vdf_path, f"{vdf_path}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(os.path.dirname(vdf_path), exist_ok=True)
    tmp = vdf_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(vdf_encode(root))
    os.replace(tmp, vdf_path)
    return ident


def remove_shortcut(vdf_path, exe):
    if not os.path.exists(vdf_path):
        return False
    root = vdf_decode(open(vdf_path, "rb").read())
    sc = root.get("shortcuts", {})
    keep = [v for v in sc.values() if not (isinstance(v, dict) and v.get("Exe") == exe)]
    if len(keep) == len(sc):
        return False
    root["shortcuts"] = {str(i): v for i, v in enumerate(keep)}
    shutil.copy2(vdf_path, f"{vdf_path}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    with open(vdf_path + ".tmp", "wb") as f:
        f.write(vdf_encode(root))
    os.replace(vdf_path + ".tmp", vdf_path)
    return True


STATUS_FILE = os.path.join(HOME, ".local/share/frameport/shortcuts-status.json")


def cmd_shortcuts(args):
    """Add library entries (+ grid artwork) for installed games. Steam must be closed while shortcuts.vdf is
    rewritten, so the work runs in a detached systemd unit (terminals/SSH sessions started from Steam live in
    steam.service's cgroup and would be killed with it). Poll shortcut_status for the result."""
    packages = [check_pkg(p) for p in args.get("packages", [])]
    if not packages:
        return {"started": False}
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump({"state": "running", "packages": packages, "started": time.time()}, f)
    unit = f"frameport-shortcuts-{int(time.time())}"
    payload = json.dumps({"packages": packages, "restart": args.get("restart", True)})
    run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", "--setenv=HOME=" + HOME,
         sys.executable, os.path.abspath(__file__), "_shortcuts_worker", payload])
    return {"started": True, "unit": unit}


def shortcuts_worker(payload):
    args = json.loads(payload)
    result = {"state": "done", "added": [], "errors": [], "finished": None}
    try:
        users = steam_users()
        if len(users) != 1:
            raise AgentError(f"found {len(users)} Steam users; not guessing which library to edit")
        vdf = os.path.join(STEAM, "userdata", users[0], "config/shortcuts.vdf")
        service = run(["systemctl", "--user", "is-active", "--quiet", "steam.service"]).returncode == 0
        if service:
            run(["systemctl", "--user", "stop", "steam.service"])
        else:
            run(["steam", "-shutdown"])
        for _ in range(40):
            if run(["pgrep", "-x", "steam"]).returncode:
                break
            time.sleep(1)
        if run(["pgrep", "-x", "steam"]).returncode == 0:
            raise AgentError("Steam did not close; library not modified")
        grid = os.path.join(os.path.dirname(vdf), "grid")
        os.makedirs(grid, exist_ok=True)
        for pkg in args["packages"]:
            try:
                anchor = os.path.join(ANCHORS, pkg)
                dep = json.load(open(os.path.join(anchor, "deployment.json")))
                icon = next(iter(glob.glob(os.path.join(anchor, "artwork/icon.*"))), "")
                got = upsert_shortcut(vdf, f'"{anchor}/launch.sh"', dep["title"], anchor, icon)
                for kind, suffix in (("portrait", "p"), ("landscape", ""), ("hero", "_hero"), ("logo", "_logo")):
                    img = next(iter(glob.glob(os.path.join(anchor, f"artwork/{kind}.*"))), None)
                    if not img:
                        continue
                    for old in glob.glob(os.path.join(grid, f"{got}{suffix}.*")):
                        os.remove(old)
                    shutil.copy(img, os.path.join(grid, f"{got}{suffix}{os.path.splitext(img)[1]}"))
                result["added"].append({"package": pkg, "appid": got, "expected": dep["appid"]})
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"{pkg}: {exc}")
        if service:
            run(["systemctl", "--user", "start", "steam.service"])
        elif args.get("restart", True):
            subprocess.Popen(["systemd-run", "--user", "--collect", f"--unit=frameport-steam-{int(time.time())}",
                              "/usr/bin/steam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        result["state"] = "failed"
        result["errors"].append(str(exc))
        run(["systemctl", "--user", "start", "steam.service"])
    result["finished"] = time.time()
    with open(STATUS_FILE, "w") as f:
        json.dump(result, f)


def cmd_shortcut_status(args):
    try:
        return json.load(open(STATUS_FILE))
    except (OSError, ValueError):
        return {"state": "none"}


# ------------------------------------------------------------------------------------------ install
def deployment(pkg):
    path = os.path.join(ANCHORS, pkg, "deployment.json")
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def cmd_list_installed(args):
    games = []
    for dep_path in sorted(glob.glob(os.path.join(ANCHORS, "*/deployment.json"))):
        try:
            dep = json.load(open(dep_path))
        except (OSError, ValueError):
            continue
        apk = os.path.join(dep["base"], "lepton-app/game.apk")
        dep["apk_present"] = os.path.exists(apk)
        dep["apk_size"] = os.path.getsize(apk) if dep["apk_present"] else 0
        games.append(dep)
    return {"games": games}


def cmd_prepare(args):
    """Where to upload, and what the Frame already has (so unchanged data is not re-sent)."""
    pkg = check_pkg(args["package"])
    title = args["title"]
    dest = os.path.expanduser(args.get("dest") or ANCHORS)
    anchor = os.path.join(ANCHORS, pkg)
    dep = deployment(pkg)
    base = dep["base"] if dep else os.path.join(dest, pkg)
    appid = dep["appid"] if dep else shortcut_appid(f'"{anchor}/launch.sh"', title)
    if container_running(appid):
        raise AgentError(f"{title} is running on the Frame. Close it first.")
    incoming = os.path.join(base, "incoming")
    os.makedirs(os.path.join(incoming, "obb"), exist_ok=True)
    app = os.path.join(base, "lepton-app")
    existing = {}
    obb = os.path.join(app, "obb")
    for root, _, files in os.walk(obb):
        for name in files:
            p = os.path.join(root, name)
            existing[os.path.relpath(p, obb)] = os.path.getsize(p)
    apk = os.path.join(app, "game.apk")
    want_sha = args.get("apk_sha256")
    same_apk = bool(want_sha and os.path.exists(apk) and os.path.getsize(apk) == args.get("apk_size")
                    and sha256_file(apk) == want_sha)
    st = os.statvfs(base if os.path.exists(base) else os.path.dirname(base) if os.path.exists(os.path.dirname(base)) else HOME)
    lepton, _ = lepton_path()
    return {"package": pkg, "base": base, "anchor": anchor, "appid": appid, "incoming": incoming,
            "installed": bool(dep), "same_apk": same_apk, "existing_obb": existing,
            "free_bytes": st.f_bavail * st.f_frsize, "lepton": lepton}


LAUNCH_SH = r"""#!/usr/bin/env bash
# Steam Frame launcher for {title} ({pkg}). Generated by FramePort.
set -euo pipefail
app_dir={base_q}
[[ -d "$app_dir/lepton-app" ]] || {{ echo "Game files missing at $app_dir (storage not mounted?)" >&2; exit 1; }}
# Some games (Unreal cloud saves) create folders without write/search permission inside Lepton,
# which silently breaks saving. Repair them before and during every launch.
fix_perms() {{ find "$app_dir/lepton-data/external" -type d ! -perm -u+rwx -exec chmod u+rwx {{}} + 2>/dev/null || true; }}
fix_perms
( while sleep 2 && kill -0 $$ 2>/dev/null; do fix_perms; done ) & permfix=$!
export SteamAppId={appid}
export STEAM_COMPAT_INSTALL_PATH="$app_dir/lepton-app"
export STEAM_COMPAT_DATA_PATH="$app_dir/lepton-data"
export STEAM_COMPAT_SHADER_PATH="$app_dir/lepton-shaders"
export STEAM_COMPAT_LIBRARY_PATHS="$app_dir"
export LEPTON_ENV_FRAMEBRIDGE_CONFIG="$app_dir/settings.conf"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
export IS_PARENT=true
{extra_env}
child=''
stop() {{
    trap - EXIT INT TERM
    kill $permfix 2>/dev/null || true
    [[ -n "$child" ]] && kill -TERM -- "-$child" 2>/dev/null || true
    podman kill "lepton-steamlaunch-$SteamAppId" >/dev/null 2>&1 || true
}}
trap stop EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
setsid {lepton_q} start >"$app_dir/launch.log" 2>&1 &
child=$!
wait "$child"
"""


def write_launcher(anchor, base, pkg, title, appid, lepton, env):
    extra = "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in (env or {}).items() if re.fullmatch(r"[A-Z_][A-Z0-9_]*", k))
    text = LAUNCH_SH.format(title=title.replace("\n", " "), pkg=pkg, base_q=shlex.quote(base), appid=appid,
                            lepton_q=shlex.quote(lepton), extra_env=extra)
    path = os.path.join(anchor, "launch.sh")
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.chmod(path + ".tmp", 0o755)
    os.replace(path + ".tmp", path)


def data_files_dir(base, pkg):
    return os.path.join(base, "lepton-data/external/Android/data", pkg, "files")


def cmd_finalize(args):
    """Move uploaded files into place, write launcher/settings/config files/deployment.json. Keeps saves."""
    pkg = check_pkg(args["package"])
    title = args["title"]
    prep = cmd_prepare({"package": pkg, "title": title, "dest": args.get("dest")})
    base, anchor, appid, incoming = prep["base"], prep["anchor"], prep["appid"], prep["incoming"]
    lepton = prep["lepton"]
    if not lepton:
        raise AgentError("Lepton is not installed (Developer Mode → install/launch 'Lepton Development' once)")
    app = os.path.join(base, "lepton-app")
    os.makedirs(os.path.join(app, "obb"), exist_ok=True)
    os.makedirs(anchor, exist_ok=True)
    new_apk = os.path.join(incoming, "game.apk")
    if os.path.exists(new_apk):
        if args.get("apk_sha256") and sha256_file(new_apk) != args["apk_sha256"]:
            raise AgentError("uploaded APK checksum mismatch (transfer corrupted?)")
        cur = os.path.join(app, "game.apk")
        if os.path.exists(cur):
            os.replace(cur, os.path.join(base, "previous-game.apk"))  # one generation for rollback
        os.replace(new_apk, cur)
    elif not os.path.exists(os.path.join(app, "game.apk")):
        raise AgentError("no APK uploaded and none installed")
    moved = 0
    inc_obb = os.path.join(incoming, "obb")
    for root, _, files in os.walk(inc_obb):
        for name in files:
            if name.endswith(".part"):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(app, "obb", os.path.relpath(src, inc_obb))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            moved += 1
    expected = args.get("obb_manifest")  # rel path -> size
    if expected:
        have = {}
        for root, _, files in os.walk(os.path.join(app, "obb")):
            for name in files:
                p = os.path.join(root, name)
                have[os.path.relpath(p, os.path.join(app, "obb"))] = os.path.getsize(p)
        bad = [k for k, v in expected.items() if have.get(k) != v]
        if bad:
            raise AgentError(f"{len(bad)} data file(s) missing or incomplete, e.g. {bad[0]}")
    shutil.rmtree(incoming, ignore_errors=True)
    # settings: settings.conf (read by the adapter via LEPTON_ENV_FRAMEBRIDGE_CONFIG) + framebridge.conf copy
    files_dir = data_files_dir(base, pkg)
    os.makedirs(files_dir, exist_ok=True)
    os.makedirs(os.path.join(base, "lepton-shaders"), exist_ok=True)
    settings = args.get("settings") or {}
    conf = os.path.join(base, "settings.conf")
    if settings:
        if os.path.exists(conf):
            shutil.copy2(conf, conf + ".previous")
        text = "".join(f"{k}={v}\n" for k, v in settings.items())
        with open(conf, "w") as f:
            f.write(text)
        with open(os.path.join(files_dir, "framebridge.conf"), "w") as f:
            f.write(text)
    for rel, content in (args.get("files") or {}).items():
        target = os.path.normpath(os.path.join(files_dir, rel))
        if not target.startswith(files_dir + os.sep):
            raise AgentError(f"refusing to write outside the game's files dir: {rel}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write(content)
    write_launcher(anchor, base, pkg, title, appid, lepton, args.get("env"))
    art_in = os.path.join(base, "incoming-artwork")
    if os.path.isdir(art_in):
        shutil.rmtree(os.path.join(anchor, "artwork"), ignore_errors=True)
        shutil.move(art_in, os.path.join(anchor, "artwork"))
    dep = {"package": pkg, "appid": int(appid), "base": base, "title": title, "apk": args.get("apk_name", "game.apk"),
           "sha256": args.get("apk_sha256"), "recipe": args.get("recipe"), "installed_by": "frameport",
           "agent_version": AGENT_VERSION, "time": time.time()}
    with open(os.path.join(anchor, "deployment.json"), "w") as f:
        json.dump(dep, f, indent=2)
    return {"ok": True, "base": base, "appid": appid, "moved_data_files": moved}


def cmd_set_settings(args):
    pkg = check_pkg(args["package"])
    dep = deployment(pkg) or {}
    if not dep:
        raise AgentError(f"{pkg} is not installed")
    base = dep["base"]
    conf = os.path.join(base, "settings.conf")
    current = {}
    if os.path.exists(conf):
        for line in open(conf):
            k, _, v = line.strip().partition("=")
            if k:
                current[k] = v
    for k, v in (args.get("settings") or {}).items():
        if not re.fullmatch(r"[a-z_]+", k) or not re.fullmatch(r"-?[0-9.]+", str(v)):
            raise AgentError(f"bad setting {k}={v}")
        current[k] = str(v)
    text = "".join(f"{k}={v}\n" for k, v in current.items())
    with open(conf, "w") as f:
        f.write(text)
    files_dir = data_files_dir(base, pkg)
    os.makedirs(files_dir, exist_ok=True)
    with open(os.path.join(files_dir, "framebridge.conf"), "w") as f:
        f.write(text)
    return {"settings": current}


def cmd_uninstall(args):
    pkg = check_pkg(args["package"])
    dep = deployment(pkg)
    if not dep:
        return {"removed": False}
    if container_running(dep["appid"]):
        raise AgentError("the game is running")
    base = dep["base"]
    keep_data = args.get("keep_data", True)
    for name in ("lepton-app", "lepton-shaders", "incoming", "previous-game.apk"):
        p = os.path.join(base, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    if not keep_data:
        shutil.rmtree(base, ignore_errors=True)
    anchor = os.path.join(ANCHORS, pkg)
    users = steam_users()
    removed_sc = False
    if len(users) == 1 and args.get("remove_shortcut"):
        removed_sc = remove_shortcut(os.path.join(STEAM, "userdata", users[0], "config/shortcuts.vdf"),
                                     f'"{anchor}/launch.sh"')  # takes effect after the next Steam restart
    if not keep_data or base != anchor:
        shutil.rmtree(anchor, ignore_errors=True)
    return {"removed": True, "kept_saves": keep_data, "shortcut_removed": removed_sc}


# ------------------------------------------------------------------------------------------ launch tests
def cmd_stop(args):
    dep = deployment(check_pkg(args["package"]))
    if dep:
        run(["systemctl", "--user", "stop", f"frameport-test-{dep['appid']}"])
        run(["podman", "kill", f"lepton-steamlaunch-{dep['appid']}"])
    return {"stopped": bool(dep)}


def cmd_launch_test(args):
    """Start the game headless (as Steam would), wait, classify, stop. Without the headset worn the OpenXR session
    never reaches FOCUSED, so this proves startup, not visuals."""
    pkg = check_pkg(args["package"])
    seconds = int(args.get("seconds", 45))
    dep = deployment(pkg)
    if not dep:
        raise AgentError(f"{pkg} is not installed")
    anchor = os.path.join(ANCHORS, pkg)
    log = os.path.join(dep["base"], "launch.log")
    appid = dep["appid"]
    if container_running(appid):
        raise AgentError("the game is already running")
    unit = f"frameport-test-{appid}"
    run(["systemctl", "--user", "reset-failed", unit])
    p = run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", os.path.join(anchor, "launch.sh")])
    if p.returncode:
        raise AgentError("could not start the launcher: " + p.stderr[-300:])
    start = time.time()
    state = "RUNNING"
    while time.time() - start < seconds:
        time.sleep(3)
        text = open(log, errors="replace").read() if os.path.exists(log) else ""
        if "Exited!" in text:
            state = "EXITED"
            break
        if "Early-exit" in text:
            state = "NEVER_STARTED"
            break
    elapsed = round(time.time() - start)
    run(["systemctl", "--user", "stop", unit])
    run(["podman", "kill", f"lepton-steamlaunch-{appid}"])
    time.sleep(3)
    return {"state": state, "elapsed": elapsed, "log": log, "log_size": os.path.getsize(log) if os.path.exists(log) else 0}


COMMANDS = {n[4:]: f for n, f in globals().items() if n.startswith("cmd_")}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "_shortcuts_worker":
        shortcuts_worker(sys.argv[2])
        return 0
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"usage: frameport_agent.py <{'|'.join(sorted(COMMANDS))}>"}))
        return 2
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        args = json.loads(raw) if raw.strip() else {}
        result = COMMANDS[sys.argv[1]](args)
        print(json.dumps({"ok": True, "result": result}))
        return 0
    except AgentError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
