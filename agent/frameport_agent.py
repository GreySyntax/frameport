#!/usr/bin/env python3
"""FramePort agent: runs ON the Steam Frame (SteamOS, python3 stdlib only).

The PC app uploads this file to ~/.local/share/frameport/agent/ and calls:
    python3 frameport_agent.py <command>        (JSON arguments on stdin, one JSON object on stdout)

Commands: info, prepare, finalize, shortcuts, shortcut_status, launch_test, stop, set_settings, uninstall,
          install_lepton, list_installed, proton_status, install_proton, prepare_pcvr, finalize_pcvr,
          controller_models.
Streaming: python3 frameport_agent.py _keyboard   (a virtual keyboard: JSON lines on stdin, see keyboard_session)

Install layout (one Lepton container per game; same as the manual installs from 2026-09):
    ~/Applications/quest-frame/<pkg>/            anchor: launch.sh, deployment.json, artwork/ (always internal storage)
    <dest>/<pkg>/lepton-app/{game.apk,obb/}      game files (dest defaults to ~/Applications/quest-frame)
    <dest>/<pkg>/lepton-data/                    container data + saves (kept across reinstalls)
    <dest>/<pkg>/lepton-shaders/, settings.conf, launch.log

PC VR (Oculus Rift) games packed for the Frame (id "rift.<slug>"), run by Proton (ARM64; x86 via FEX in Proton):
    ~/Applications/quest-frame/<id>/             anchor: launch.sh, deployment.json (kind "pcvr"), artwork/
    <dest>/<id>/game/                            the Windows game folder
    <dest>/<id>/revive/                          Revive (ReviveInjector.exe + DLLs)
    <dest>/<id>/compatdata/                      Proton prefix = saves (kept across reinstalls), launch.log
"""
import fcntl
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

AGENT_VERSION = 41
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
    candidates = (([os.path.join(app["dir"], "lepton")] if app else [])
                  + [os.path.join(STEAM, "steamapps/common/Lepton/lepton")])
    for c in candidates:
        if os.access(c, os.X_OK):
            return c, app
    return None, app


def find_app_id(appid):
    """{appid, name, dir} of an installed app by id (its appmanifest), or None."""
    for lib in steam_libraries():
        acf = os.path.join(lib, f"appmanifest_{appid}.acf")
        try:
            text = open(acf, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        installdir = re.search(r'"installdir"\s+"([^"]*)"', text)
        name = re.search(r'"name"\s+"([^"]*)"', text)
        state = re.search(r'"StateFlags"\s+"(\d+)"', text)
        d = os.path.join(lib, "common", installdir[1]) if installdir else None
        num = {k: int(m[1]) for k in ("BytesDownloaded", "BytesToDownload", "SizeOnDisk")
               for m in [re.search(rf'"{k}"\s+"(\d+)"', text)] if m}
        return {"appid": str(appid), "name": name[1] if name else "", "dir": d, "state": int(state[1]) if state else 0,
                "complete": bool(state and int(state[1]) & 4 and d and os.path.isdir(d)),
                "downloaded": num.get("BytesDownloaded", 0), "to_download": num.get("BytesToDownload", 0)}
    return None


# ------------------------------------------------------------------------------------------ Steam appinfo (binary)
APPINFO = os.path.join(STEAM, "appcache/appinfo.vdf")


def appinfo_entries(want=None, path=APPINFO):
    """Parse Steam's appinfo.vdf (v28/v29) into {appid: appinfo dict}; only the apps in `want` (or all)."""
    data = open(path, "rb").read()
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic >> 8 != 0x075644 or magic & 0xFF not in (0x28, 0x29):
        raise AgentError(f"unknown appinfo.vdf version {magic:#x}")
    v29 = magic & 0xFF == 0x29
    strings = []
    end = len(data)
    if v29:
        str_off = struct.unpack_from("<q", data, 8)[0]
        n = struct.unpack_from("<I", data, str_off)[0]
        p = str_off + 4
        for _ in range(n):
            e = data.index(b"\0", p)
            strings.append(data[p:e].decode("utf-8", "replace"))
            p = e + 1
        end = str_off

    def key(p):
        if v29:
            return strings[struct.unpack_from("<I", data, p)[0]], p + 4
        e = data.index(b"\0", p)
        return data[p:e].decode("utf-8", "replace"), e + 1

    def kv(p):
        obj = {}
        while True:
            t = data[p]
            p += 1
            if t == 8:
                return obj, p
            k, p = key(p)
            if t == 0:
                obj[k], p = kv(p)
            elif t == 1:
                e = data.index(b"\0", p)
                obj[k] = data[p:e].decode("utf-8", "replace")
                p = e + 1
            elif t in (2, 3, 4, 6):  # int32, float, pointer, color
                obj[k] = struct.unpack_from("<f" if t == 3 else "<i", data, p)[0]
                p += 4
            elif t in (7, 10):  # uint64 / int64
                obj[k] = struct.unpack_from("<Q", data, p)[0]
                p += 8
            else:
                raise AgentError(f"unsupported appinfo value type {t}")

    out, p = {}, 16 if v29 else 8
    while p + 8 <= end:
        appid, size = struct.unpack_from("<II", data, p)
        if appid == 0:
            break
        body = p + 8
        if want is None or appid in want:
            try:
                obj, _ = kv(body + 60)  # infostate, last updated, pics token, sha1, change number, binary sha1
                out[appid] = obj.get("appinfo", obj)
            except (AgentError, IndexError, struct.error, ValueError):
                pass
        p = body + size
    return out


def arm64_compat_tools():
    """Steam compat tools for this device from Valve's ARM64 compat list app (appinfo extended.compat_tools):
    {name: {appid, display_name, require_tool_appid, aliases, from_oslist}}. Found dynamically (no hardcoded ids)."""
    best = {}
    for info in appinfo_entries().values():
        tools = ((info.get("extended") or {}).get("compat_tools"))
        if isinstance(tools, dict) and any(k.endswith("-arm64") for k in tools):
            if len(tools) > len(best):
                best = tools
    return best


def proton_tools():
    """Proton builds for Windows games on this (ARM64) device, newest first, with install state."""
    out = []
    for name, t in arm64_compat_tools().items():
        if t.get("from_oslist") != "windows" or not isinstance(t, dict) or "appid" not in t:
            continue
        app = find_app_id(t["appid"])
        req = t.get("require_tool_appid")
        if app and app["complete"]:
            req = tool_manifest(app["dir"]).get("require_tool_appid") or req
        req_app = find_app_id(req) if req else None
        ver = re.findall(r"\d+", name)
        out.append({"name": name, "appid": int(t["appid"]), "display_name": t.get("display_name", name),
                    "aliases": t.get("aliases", ""), "experimental": "experimental" in name,
                    "installed": bool(app and app["complete"]), "dir": app["dir"] if app else None,
                    "require_tool_appid": int(req) if req else None,
                    "require_installed": (not req) or bool(req_app and req_app["complete"]),
                    "require_dir": req_app["dir"] if req_app else None,
                    "sort": (0 if "experimental" in name else 1, int(ver[0]) if ver else 0)})
    out.sort(key=lambda t: t.pop("sort"), reverse=True)
    return out


def tool_manifest(tool_dir):
    """commandline / require_tool_appid from a compat tool's toolmanifest.vdf (text KeyValues)."""
    try:
        text = open(os.path.join(tool_dir, "toolmanifest.vdf"), encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    out = {}
    for k in ("commandline", "require_tool_appid", "version"):
        m = re.search(rf'"{k}"\s+"((?:[^"\\]|\\.)*)"', text)
        if m:
            out[k] = m[1].replace('\\"', '"')
    if "require_tool_appid" in out:
        out["require_tool_appid"] = int(out["require_tool_appid"])
    return out


def compat_command(tool_dir, verb="waitforexitandrun", depth=0):
    """argv prefix Steam would run for a compat tool: its required runtime's command first, then the tool's own
    (e.g. SteamLinuxRuntime_4-arm64/_v2-entry-point --verb=... -- Proton/proton waitforexitandrun)."""
    man = tool_manifest(tool_dir)
    cmd = man.get("commandline")
    if not cmd:
        raise AgentError(f"no toolmanifest.vdf commandline in {tool_dir}")
    own = [os.path.join(tool_dir, a.lstrip("/")) if i == 0 else a
           for i, a in enumerate(shlex.split(cmd.replace("%verb%", verb)))]
    req = man.get("require_tool_appid")
    if req and depth < 3:
        app = find_app_id(req)
        if not app or not app["complete"]:
            raise AgentError(f"{os.path.basename(tool_dir)} needs Steam app {req} (runtime), which isn't installed")
        return compat_command(app["dir"], verb, depth + 1) + own
    return own


def pick_proton(tools, wanted=None):
    ready = [t for t in tools if t["installed"] and t["require_installed"]]
    if wanted:
        return next((t for t in tools if wanted in (t["name"], t["display_name"]) or wanted in t["aliases"].split(",")),
                    None)
    return ready[0] if ready else (tools[0] if tools else None)


def openxr_runtime():
    for d in (os.path.join(HOME, ".config/openxr/1"), "/etc/xdg/openxr/1", "/usr/share/openxr/1"):
        p = os.path.join(d, "active_runtime.json")
        if os.path.exists(p):
            try:
                return {"path": os.path.realpath(p), "name": json.load(open(p))["runtime"].get("name")}
            except (OSError, ValueError, KeyError):
                return {"path": os.path.realpath(p), "name": None}
    return None


def cmd_proton_status(args):
    try:
        tools = proton_tools()
    except (OSError, AgentError) as exc:
        return {"tools": [], "ready": None, "error": str(exc), "openxr": openxr_runtime()}
    ready = pick_proton(tools, args.get("tool"))
    ok = bool(ready and ready["installed"] and ready["require_installed"])
    download = {"done": 0, "total": 0}
    if ready and not ok:
        for a in (ready["appid"], ready.get("require_tool_appid")):
            app = find_app_id(a) if a else None
            if app and not app["complete"]:
                download["done"] += app["downloaded"]
                download["total"] += app["to_download"]
    return {"tools": tools, "ready": ready if ok else None, "suggested": ready, "openxr": openxr_runtime(),
            "download": download}


SELFTEST_DIR = os.path.join(HOME, ".local/share/frameport/proton-selftest")


SESSION_VARS = ("DISPLAY", "WAYLAND_DISPLAY", "GAMESCOPE_WAYLAND_DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE")


def session_env():
    """The display session variables of the running Steam client (gamescope's X/Wayland), for headless launches."""
    p = run(["pgrep", "-x", "steam"])
    for pid in p.stdout.split():
        try:
            raw = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
        except OSError:
            continue
        env = dict(kv.decode(errors="replace").split("=", 1) for kv in raw if b"=" in kv)
        return {k: env[k] for k in SESSION_VARS if k in env}
    return {}


def run_tree(cmd, env, cwd, log_path, timeout):
    """Run a command in its own process group with output to a file; on timeout kill the whole group (Wine leaves
    children that keep pipes open, so subprocess.run(timeout=...) would hang). Returns (output, exit code | None)."""
    import signal

    with open(log_path, "wb") as log:
        p = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = None
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(p.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    p.wait(timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    continue
    return open(log_path, errors="replace").read(), code


def stop_prefix(tool, prefix):
    """wineserver -k for a Proton prefix (ends every process of that prefix)."""
    wineserver = next(iter(glob.glob(os.path.join(tool["dir"], "files/bin*/wineserver"))), None)
    if wineserver and os.path.isdir(prefix):
        try:
            run([wineserver, "-k"], env=dict(os.environ, WINEPREFIX=prefix), timeout=20)
        except subprocess.TimeoutExpired:
            pass


def cmd_proton_selftest(args):
    """Run a Windows program (cmd.exe) under the Frame's Proton, the way FramePort launches PC VR games (SteamGameId
    set so Proton sets up VR), and report whether it ran and whether wineopenxr was registered as the OpenXR runtime."""
    tool = pick_proton(proton_tools(), args.get("tool"))
    if not tool or not (tool["installed"] and tool["require_installed"]):
        raise AgentError("Proton isn't installed on the Frame yet")
    os.makedirs(os.path.join(SELFTEST_DIR, "compatdata"), exist_ok=True)
    appid = str(shortcut_appid("frameport-proton-selftest", "FramePort"))
    env = dict(os.environ, SteamAppId=appid, STEAM_COMPAT_APP_ID=appid,
               STEAM_COMPAT_DATA_PATH=os.path.join(SELFTEST_DIR, "compatdata"),
               STEAM_COMPAT_CLIENT_INSTALL_PATH=STEAM, STEAM_COMPAT_INSTALL_PATH=SELFTEST_DIR,
               STEAM_COMPAT_LIBRARY_PATHS=SELFTEST_DIR, XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}",
               PROTON_LOG_DIR=SELFTEST_DIR)
    for k, v in session_env().items():
        env.setdefault(k, v)
    if args.get("vr", True):
        env["SteamGameId"] = appid  # Proton sets up vrclient/wineopenxr only for "game" processes
    if args.get("log"):
        env["PROTON_LOG"] = "1"
    for k, v in (args.get("env") or {}).items():
        env[str(k)] = str(v)
    marker = os.path.join(SELFTEST_DIR, "marker.txt")
    if os.path.exists(marker):
        os.remove(marker)
    cmd = compat_command(tool["dir"], args.get("verb", "waitforexitandrun")) + \
        ["c:\\windows\\system32\\cmd.exe", "/c", f"echo FRAMEPORT_PROTON_OK>{windows_path(marker)}"]
    start = time.time()
    out, code = run_tree(cmd, env, SELFTEST_DIR, os.path.join(SELFTEST_DIR, "selftest.log"),
                         int(args.get("timeout_s", 600)))
    stop_prefix(tool, os.path.join(SELFTEST_DIR, "compatdata/pfx"))
    reg = os.path.join(SELFTEST_DIR, "compatdata/pfx/system.reg")
    text = open(reg, errors="replace").read() if os.path.exists(reg) else ""
    m = re.search(r'\[Software\\\\Khronos\\\\OpenXR\\\\1\][^\[]*"ActiveRuntime"="([^"]*)"', text)
    plog = os.path.join(SELFTEST_DIR, f"steam-{appid}.log")
    ran = os.path.exists(marker) and "FRAMEPORT_PROTON_OK" in open(marker, errors="replace").read()
    return {"tool": tool["name"], "ran": ran, "exit_code": code, "proton_log": plog
            if os.path.exists(plog) else None,
            "seconds": round(time.time() - start), "openxr_runtime": m[1] if m else None,
            "prefix_created": bool(text), "log_tail": out[-3000:]}


def xr_probe(payload):
    """(child process) Create an OpenXR instance with XR_KHR_convert_timespec_time through Proton's loader and call
    xrConvertTimespecTimeToTimeKHR. Prints one JSON line. The layer (if any) is enabled via the environment."""
    import ctypes as C

    args = json.loads(payload)
    out = {"loader": args["loader"]}
    try:
        xr = C.CDLL(args["loader"])
        xr.xrGetInstanceProcAddr.argtypes = [C.c_uint64, C.c_char_p, C.POINTER(C.c_void_p)]
        xr.xrDestroyInstance.argtypes = [C.c_uint64]

        class AppInfo(C.Structure):
            _fields_ = [("applicationName", C.c_char * 128), ("applicationVersion", C.c_uint32),
                        ("engineName", C.c_char * 128), ("engineVersion", C.c_uint32), ("apiVersion", C.c_uint64)]

        class CreateInfo(C.Structure):
            _fields_ = [("type", C.c_int), ("next", C.c_void_p), ("createFlags", C.c_uint64), ("app", AppInfo),
                        ("layerCount", C.c_uint32), ("layers", C.c_void_p), ("extCount", C.c_uint32),
                        ("exts", C.POINTER(C.c_char_p))]

        class LayerProps(C.Structure):
            _fields_ = [("type", C.c_int), ("next", C.c_void_p), ("layerName", C.c_char * 256),
                        ("specVersion", C.c_uint64), ("layerVersion", C.c_uint32), ("description", C.c_char * 256)]

        n = C.c_uint32()
        xr.xrEnumerateApiLayerProperties(0, C.byref(n), None)
        props = (LayerProps * max(n.value, 1))()
        for p in props:
            p.type = 1  # XR_TYPE_API_LAYER_PROPERTIES
        xr.xrEnumerateApiLayerProperties(n.value, C.byref(n), props)
        out["layers"] = [props[i].layerName.decode() for i in range(n.value)]
        exts = (C.c_char_p * 1)(b"XR_KHR_convert_timespec_time")
        ci = CreateInfo(3, None, 0, AppInfo(b"FramePort timefix probe", 1, b"FramePort", 1, 1 << 48), 0, None, 1, exts)
        inst = C.c_uint64()
        out["create"] = xr.xrCreateInstance(C.byref(ci), C.byref(inst))
        if out["create"] == -4:  # the runtime sometimes fails the first attempt (as Proton's own probe sees)
            out["create"] = xr.xrCreateInstance(C.byref(ci), C.byref(inst))
        if out["create"] == 0:
            fn = C.c_void_p()
            out["proc"] = xr.xrGetInstanceProcAddr(inst.value, b"xrConvertTimespecTimeToTimeKHR", C.byref(fn))
            if out["proc"] == 0 and fn.value:
                class Ts(C.Structure):
                    _fields_ = [("tv_sec", C.c_long), ("tv_nsec", C.c_long)]
                now = Ts()
                C.CDLL(None).clock_gettime(1, C.byref(now))
                t = C.c_int64()
                conv = C.CFUNCTYPE(C.c_int, C.c_uint64, C.POINTER(Ts), C.POINTER(C.c_int64))(fn.value)
                out["convert"] = conv(inst.value, C.byref(now), C.byref(t))
                out["time"] = t.value
            xr.xrDestroyInstance(inst.value)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(out))


def cmd_xr_layer_test(args):
    """Does the timefix layer fix time conversion on this runtime? Runs xr_probe with Proton's OpenXR loader, without
    and with the layer (layer_dir = folder with XR_APILAYER_FRAMEPORT_timefix.json)."""
    tool = pick_proton(proton_tools(), args.get("tool"))
    loader = next(iter(glob.glob(os.path.join(tool["dir"], "files/lib/aarch64-linux-gnu/libopenxr_loader.so.1")))
                  if tool and tool.get("dir") else [], None) or "/opt/steamvr/bin/linuxarm64/libopenxr_loader.so"
    results = {}
    for name, layer in (("without_layer", None), ("with_layer", args.get("layer_dir"))):
        if name == "with_layer" and not layer:
            continue
        env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}", XR_LOADER_DEBUG="error")
        env.pop("XR_API_LAYER_PATH", None)
        env.pop("XR_ENABLE_API_LAYERS", None)
        for k, v in session_env().items():
            env.setdefault(k, v)
        if layer:
            env.update(XR_API_LAYER_PATH=os.path.expanduser(layer), XR_ENABLE_API_LAYERS=XR_LAYER)
        try:
            p = run([sys.executable, os.path.abspath(__file__), "_xr_probe", json.dumps({"loader": loader})],
                    env=env, timeout=60)
            line = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("{")), None)
            results[name] = json.loads(line) if line else {"error": (p.stderr or p.stdout)[-800:]}
        except subprocess.TimeoutExpired:
            results[name] = {"error": "timed out"}
    return results


def write_stub_manifest(appid, name, installdir, lib=None):
    """An appmanifest with StateFlags 'update required': Steam downloads the app on its next start."""
    lib = lib or os.path.join(STEAM, "steamapps")
    path = os.path.join(lib, f"appmanifest_{appid}.acf")
    if os.path.exists(path):
        return False
    text = ('"AppState"\n{\n' + "".join(f'\t"{k}"\t\t"{v}"\n' for k, v in (
        ("appid", appid), ("Universe", 1), ("name", name), ("StateFlags", 1026), ("installdir", installdir),
        ("AutoUpdateBehavior", 0))) + "}\n")
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)
    return True


def cmd_install_proton(args):
    """Install Proton for Windows games (plus the Steam Linux Runtime it needs). mode "request" asks Steam
    (steam://install; the user confirms in the headset); mode "unattended" writes appmanifest stubs and restarts
    Steam so it downloads them by itself."""
    tools = proton_tools()
    tool = pick_proton(tools, args.get("tool"))
    if not tool:
        raise AgentError("this Steam has no ARM64 Proton in its compat list (update SteamOS/Steam)")
    need = [a for a, ok in ((tool["appid"], tool["installed"]),
                            (tool["require_tool_appid"], tool["require_installed"])) if a and not ok]
    if not need:
        return {"tool": tool["name"], "installed": True, "requested": []}
    if args.get("mode") == "unattended":
        info = appinfo_entries(set(need))
        stubs = []
        for a in need:
            common = (info.get(a) or {}).get("common") or {}
            installdir = ((info.get(a) or {}).get("config") or {}).get("installdir")
            if not installdir:
                raise AgentError(f"Steam's app cache has no install folder for app {a}")
            stubs.append({"appid": a, "name": common.get("name", str(a)), "installdir": installdir})
        payload = json.dumps({"stubs": stubs})
        unit = f"frameport-tools-{int(time.time())}"
        run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", "--setenv=HOME=" + HOME,
             sys.executable, os.path.abspath(__file__), "_tools_worker", payload])
        return {"tool": tool["name"], "installed": False, "requested": need, "mode": "unattended", "unit": unit,
                "hint": "Steam restarts once and downloads Proton in the background (a few hundred MB)."}
    for a in need:
        subprocess.Popen(["steam", "-ifrunning", f"steam://install/{a}"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return {"tool": tool["name"], "installed": False, "requested": need, "mode": "request",
            "hint": "Put the headset on and confirm the install in Steam (one dialog per item)."}


def stop_steam():
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
        raise AgentError("Steam did not close")
    for _ in range(15):  # Steam's helpers can still be writing its config (shortcuts.vdf) for a moment
        if run(["pgrep", "-f", "steamwebhelper|steam.sh|reaper SteamLaunch"]).returncode:
            break
        time.sleep(1)
    time.sleep(2)
    return service


def start_steam(service):
    if service:
        run(["systemctl", "--user", "start", "steam.service"])
    else:
        subprocess.Popen(["systemd-run", "--user", "--collect", f"--unit=frameport-steam-{int(time.time())}",
                          "/usr/bin/steam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tools_worker(payload):
    args = json.loads(payload)
    service = True
    try:
        service = stop_steam()
        for st in args["stubs"]:
            write_stub_manifest(st["appid"], st["name"], st["installdir"])
    finally:
        start_steam(service)


def steam_users():
    return sorted(d for d in os.listdir(os.path.join(STEAM, "userdata")) if d.isdigit() and d != "0") \
        if os.path.isdir(os.path.join(STEAM, "userdata")) else []


STEAMID64_BASE = 76561197960265728


def active_steam_user():
    """userdata folder (account id) of the most recent Steam login (config/loginusers.vdf), if it has one."""
    try:
        text = open(os.path.join(STEAM, "config/loginusers.vdf"), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    users = steam_users()
    for sid, body in re.findall(r'"(\d{17})"\s*\{([^}]*)\}', text):
        if re.search(r'"MostRecent"\s+"1"', body) and str(int(sid) - STEAMID64_BASE) in users:
            return str(int(sid) - STEAMID64_BASE)
    return None


def library_users():
    """Steam accounts whose library gets FramePort's shortcuts: every account on the Frame, the signed-in one first
    (loginusers.vdf's MostRecent isn't always the account signed in on the Frame: GitHub #4/#21 got shortcuts in the
    other account; an extra shortcut in an unused account does no harm)."""
    users = steam_users()
    if not users:
        raise AgentError("Steam has no signed-in account on this Frame yet; sign in to Steam on the Frame first")
    active = active_steam_user()
    return [active] + [u for u in users if u != active] if active else users


def shortcut_appid_for(exe):
    """appid of the shortcut whose Exe is `exe` in the signed-in account's library (any account if unknown); None
    when it isn't in the library."""
    for u in library_users():
        vdf = os.path.join(STEAM, "userdata", u, "config/shortcuts.vdf")
        try:
            root = vdf_decode(open(vdf, "rb").read()) if os.path.exists(vdf) else {}
        except (OSError, AgentError):
            continue
        for v in (root.get("shortcuts") or {}).values():
            if isinstance(v, dict) and v.get("Exe") == exe and v.get("appid"):
                return v["appid"] & 0xFFFFFFFF
    return None


def container_running(appid):
    p = run(["podman", "ps", "--format", "{{.Names}}"])
    return f"lepton-steamlaunch-{appid}" in p.stdout.split()


CONTAINERS_CONF = os.path.join(HOME, ".config/containers/containers.conf")


def ensure_host_fixes():
    """Rootless podman (used by Lepton) leaks one kernel session keyring per container start; after ~200 launches
    since boot every game fails with 'crun: create keyring ...: Disk quota exceeded'. keyring=false stops that."""
    changed = []
    text = open(CONTAINERS_CONF).read() if os.path.exists(CONTAINERS_CONF) else ""
    if not re.search(r"^\s*keyring\s*=", text, re.M):
        note = "# FramePort: stop rootless podman leaking a kernel keyring per container start (Lepton launches)\n"
        if re.search(r"^\[containers\]\s*$", text, re.M):
            text = re.sub(r"^\[containers\]\s*$", "[containers]\n" + note + "keyring = false", text, count=1,
                          flags=re.M)
        else:
            sep = "\n" if text and not text.endswith("\n") else ""
            text = text + sep + "[containers]\n" + note + "keyring = false\n"
        os.makedirs(os.path.dirname(CONTAINERS_CONF), exist_ok=True)
        with open(CONTAINERS_CONF, "w") as f:
            f.write(text)
        changed.append("podman keyring=false")
    return changed


def key_usage():
    try:
        for line in open("/proc/key-users"):
            f = line.split()
            if f[0].rstrip(":") == str(os.getuid()):
                used, limit = f[3].split("/")
                return {"keys": int(used), "max_keys": int(limit)}
    except (OSError, ValueError, IndexError):
        pass
    return {}


POWER_SUPPLY = "/sys/class/power_supply"
CHARGER_TYPES = ("Mains", "USB", "USB_C", "USB_PD", "USB_PD_DRP", "USB_DCP", "USB_CDP", "USB_ACA", "Wireless")


def battery_state():
    """The Frame's battery: {"percent", "status" (Charging/Discharging/Full/Not charging), "plugged", "draining"};
    None without one. "plugged" = a charger reports online (or the battery says it's charging/full); "draining" = the
    battery's own gauge says Discharging (with a charger: it supplies less than the Frame uses, or just booted).
    On the Frame (2026-10-03): max1720x_bat (Battery), pm8550b-charger (Unknown), tcpm …typec (USB, online=1)."""
    def read(path):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return ""
    battery, plugged = None, False
    try:
        names = sorted(os.listdir(POWER_SUPPLY))
    except OSError:
        return None
    for name in names:
        d = os.path.join(POWER_SUPPLY, name)
        kind = read(os.path.join(d, "type"))
        if kind == "Battery" and battery is None and read(os.path.join(d, "capacity")).isdigit():
            battery = {"percent": int(read(os.path.join(d, "capacity"))), "status": read(os.path.join(d, "status"))}
        elif kind in CHARGER_TYPES and read(os.path.join(d, "online")) == "1":
            plugged = True
    if battery is not None:
        battery["plugged"] = plugged or battery["status"] in ("Charging", "Full")
        battery["draining"] = battery["status"] == "Discharging"
    return battery


BOOT_STATE = os.path.join(HOME, ".cache/frameport-boot.json")
# what the last lines of a boot's journal say when it was shut down or rebooted on purpose (a crash, a GPU hang that
# reset the Frame or a pulled battery leaves none of these)
CLEAN_SHUTDOWN = ("Reached target System Power Off", "Reached target System Reboot", "Reached target Shutdown",
                  "System is powering down", "System is rebooting", "systemd-shutdown", "Power-Off", "Rebooting.")


def boot_state():
    """This boot and how the previous one ended: {"boot_id", "boot_time", "prev_clean" (True/False/None = unknown),
    "last_launch" ({"package", "title", "time"}: the FramePort game started last before this boot)}. Worked out once
    per boot (cached), so the app can say "your Frame restarted while <game> was running"."""
    try:
        boot_id = open("/proc/sys/kernel/random/boot_id").read().strip()
    except OSError:
        return None
    try:
        cached = json.load(open(BOOT_STATE))
        if cached.get("boot_id") == boot_id:
            return cached
    except (OSError, ValueError):
        pass
    boot_time = None
    try:
        for line in open("/proc/stat"):
            if line.startswith("btime "):
                boot_time = int(line.split()[1])
    except OSError:
        pass
    try:
        tail = run(["journalctl", "-b", "-1", "-n", "120", "-q", "--no-pager", "-o", "cat"]).stdout
    except OSError:
        tail = ""
    prev_clean = any(m in tail for m in CLEAN_SHUTDOWN) if tail.strip() else None
    last = None
    for d in cmd_list_installed({})["games"]:
        log = os.path.join(d["base"], "launch.log")
        try:
            t = os.path.getmtime(log)
        except OSError:
            continue
        if (boot_time is None or t < boot_time) and (last is None or t > last["time"]):
            last = {"package": d["package"], "title": d.get("title") or d["package"], "time": t}
    state = {"boot_id": boot_id, "boot_time": boot_time, "prev_clean": prev_clean, "last_launch": last}
    try:
        os.makedirs(os.path.dirname(BOOT_STATE), exist_ok=True)
        with open(BOOT_STATE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass
    return state


def cmd_battery(args):
    return {"battery": battery_state()}


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
        "host_fixes": ensure_host_fixes(), "kernel_keys": key_usage(),
        "proton": cmd_proton_status({}) if args.get("proton", True) else None,
        "boot": boot_state(),
        "battery": battery_state(),
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


# ------------------------------------------------------------------------------------- Steam shortcuts (binary VDF)
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


def upsert_shortcut(vdf_path, exe, title, start_dir, icon="", tag="Quest on Frame", launch_options="", tags=None,
                    openvr=True, write=True):
    """Add/update a non-Steam shortcut (matched by Exe, so its appid never changes). `tags` (genres, the user's tags)
    are merged with tags already on the shortcut, so ones set in Steam are kept. write=False: change nothing, return
    (appid, whether shortcuts.vdf would change)."""
    data = open(vdf_path, "rb").read() if os.path.exists(vdf_path) else b""
    root = vdf_decode(data) if data else {"shortcuts": {}}
    shortcuts = root.setdefault("shortcuts", {})
    entry = next((v for v in shortcuts.values() if isinstance(v, dict) and v.get("Exe") == exe), None)
    ident = entry["appid"] if entry else shortcut_appid(exe, title)
    if entry is None:
        entry = {"appid": ident, "LastPlayTime": 0, "tags": {"0": tag}}
        shortcuts[str(max([int(k) for k in shortcuts if k.isdigit()] + [-1]) + 1)] = entry
    if tags:
        have = [v for v in (entry.get("tags") or {}).values() if isinstance(v, str)]
        merged = list(dict.fromkeys(have + [t for t in [tag] + list(tags) if t]))
        entry["tags"] = {str(i): t for i, t in enumerate(merged)}
    entry.update(appname=title, Exe=exe, StartDir=start_dir, icon=icon or entry.get("icon", ""), ShortcutPath="",
                 LaunchOptions=launch_options, IsHidden=0, AllowDesktopConfig=1, AllowOverlay=1,
                 OpenVR=1 if openvr else 0, Devkit=0, DevkitGameID="", DevkitOverrideAppID=0, FlatpakAppID="")
    if not write:
        return ident, vdf_encode(root) != data
    if data:
        backup_vdf(vdf_path)
    os.makedirs(os.path.dirname(vdf_path), exist_ok=True)
    tmp = vdf_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(vdf_encode(root))
    os.replace(tmp, vdf_path)
    return ident


def prune_shortcuts(vdf_path, title, keep_exe, tag):
    """Remove FramePort-tagged shortcuts for `title` whose Exe differs from keep_exe (a reinstall that changed the
    launch command, e.g. Revive -> direct, would otherwise leave the old, crashing shortcut behind). Returns the
    removed appids."""
    if not os.path.exists(vdf_path):
        return []
    tags = (tag,) if isinstance(tag, str) else tuple(tag)  # one tag, or several (current + earlier names)
    root = vdf_decode(open(vdf_path, "rb").read())
    sc = root.get("shortcuts", {})

    def stale(v):
        return (isinstance(v, dict) and v.get("appname") == title and v.get("Exe") != keep_exe
                and any(t in (v.get("tags") or {}).values() for t in tags))
    removed = [v.get("appid") for v in sc.values() if stale(v)]
    if not removed:
        return []
    keep = [v for v in sc.values() if not stale(v)]
    root["shortcuts"] = {str(i): v for i, v in enumerate(keep)}
    backup_vdf(vdf_path)
    with open(vdf_path + ".tmp", "wb") as f:
        f.write(vdf_encode(root))
    os.replace(vdf_path + ".tmp", vdf_path)
    return removed


VDF_BACKUPS = 5


def backup_vdf(vdf_path):
    """Keep a copy before changing shortcuts.vdf (the last VDF_BACKUPS are kept)."""
    shutil.copy2(vdf_path, f"{vdf_path}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    for old in sorted(glob.glob(f"{vdf_path}.backup-*"))[:-VDF_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass


def remove_tree(path):
    """Remove a file or folder tree, including what Lepton's containers leave in a game's data: overlayfs work dirs
    with mode 000 (and whiteout device files in them), which shutil.rmtree(ignore_errors=True) silently skipped, so
    "remove saves" left lepton-data behind. Last resort: podman unshare (files owned by the container's user ids)."""
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.remove(path)
        return
    for root, dirs, _files in os.walk(path):  # top-down: fix a folder's mode before walking into it
        for d in dirs:
            p = os.path.join(root, d)
            if not os.path.islink(p):
                try:
                    os.chmod(p, 0o700)
                except OSError:
                    pass
    shutil.rmtree(path, ignore_errors=True)
    if os.path.lexists(path) and shutil.which("podman"):
        run(["podman", "unshare", "rm", "-rf", path])


def grid_files(grid, appid):
    """A shortcut's grid artwork (<appid>p.jpg, <appid>_hero.png, …): exact names, never another appid that merely
    starts with the same digits."""
    pat = re.compile(rf"^{re.escape(str(appid))}(p|_hero|_logo|_icon)?\.[A-Za-z0-9]+$")
    try:
        return [os.path.join(grid, n) for n in os.listdir(grid) if pat.match(n)]
    except OSError:
        return []


def remove_shortcut(vdf_path, exe):
    if not os.path.exists(vdf_path):
        return False
    root = vdf_decode(open(vdf_path, "rb").read())
    sc = root.get("shortcuts", {})
    keep = [v for v in sc.values() if not (isinstance(v, dict) and v.get("Exe") == exe)]
    if len(keep) == len(sc):
        return False
    root["shortcuts"] = {str(i): v for i, v in enumerate(keep)}
    backup_vdf(vdf_path)
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
    remove = [r for r in args.get("remove", [])
              if isinstance(r, dict) and str(r.get("exe", "")).startswith('"' + ANCHORS)]
    if not packages and not remove:
        return {"started": False}
    current = cmd_shortcut_status({})
    alive = time.time() - float(current.get("started") or 0) < 60  # a waiting worker rewrites its status every 10 s
    if current.get("state") == "waiting" and alive and set(packages) <= set(current.get("packages") or []) \
            and not remove:
        return {"started": False, "waiting": True}  # the update for these games already waits (Gaming Mode / game)
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump({"state": "running", "packages": packages, "started": time.time()}, f)
    unit = f"frameport-shortcuts-{int(time.time())}"
    payload = json.dumps({"packages": packages, "remove": remove, "restart": args.get("restart", True)})
    run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", "--setenv=HOME=" + HOME,
         sys.executable, os.path.abspath(__file__), "_shortcuts_worker", payload])
    return {"started": True, "unit": unit}


def shortcut_args(pkg):
    """(exe, title, start dir, icon, tag, tags, openvr) of an installed game's shortcut."""
    anchor = os.path.join(ANCHORS, pkg)
    dep = json.load(open(os.path.join(anchor, "deployment.json")))
    icon = next(iter(glob.glob(os.path.join(anchor, "artwork/icon.*"))), "")
    flat = dep.get("vr") is False
    tag = ("Windows game on Frame" if flat else "PC VR on Frame") if dep.get("kind") == "pcvr" else "Quest on Frame"
    return f'"{anchor}/launch.sh"', dep["title"], anchor, icon, tag, dep.get("tags") or [], not flat


def library_changes(users, packages):
    """Whether shortcuts.vdf would change for any of these games (a reinstall usually changes nothing)."""
    for user in users:
        vdf = os.path.join(STEAM, "userdata", user, "config/shortcuts.vdf")
        for pkg in packages:
            exe, title, start, icon, tag, tags, openvr = shortcut_args(pkg)
            if upsert_shortcut(vdf, exe, title, start, icon, tag, tags=tags, openvr=openvr, write=False)[1]:
                return True
    return False


def shortcuts_lost(users, packages, wait=25):
    """Packages whose shortcut isn't in any account's shortcuts.vdf once Steam has started again (Steam saving its own
    copy over ours on the way out looked like a successful install with no game in the library, GitHub #27)."""
    for _ in range(wait):  # until Steam runs again (it rewrites shortcuts.vdf while starting, too)
        if run(["pgrep", "-x", "steam"]).returncode == 0:
            break
        time.sleep(1)
    time.sleep(8)
    lost = []
    for pkg in packages:
        exe = shortcut_args(pkg)[0]
        found = False
        for user in users:
            vdf = os.path.join(STEAM, "userdata", user, "config/shortcuts.vdf")
            try:
                root = vdf_decode(open(vdf, "rb").read()) if os.path.exists(vdf) else {}
            except (OSError, AgentError):
                continue
            entries = (root.get("shortcuts") or {}).values()
            found = found or any(isinstance(v, dict) and v.get("Exe") == exe for v in entries)
        if not found:
            lost.append(pkg)
    return lost


def desktop_mode():
    """Desktop Mode is open: it runs inside Steam's session, so a Steam restart would end it (and FramePort itself when
    it runs on the Frame); library changes wait until the user is back in Gaming Mode."""
    return run(["pgrep", "-x", "plasmashell"]).returncode == 0


def game_running():
    """A game is being played on the Frame (any Lepton game container, or a FramePort PC VR game)."""
    names = run(["podman", "ps", "--format", "{{.Names}}"]).stdout.split()
    if any(n.startswith("lepton-steamlaunch-") for n in names):
        return True
    return any(pcvr_pids(d["base"]) for d in cmd_list_installed({})["games"] if d.get("kind") == "pcvr")


def shortcuts_worker(payload):
    """One library update at a time: two at once each read shortcuts.vdf, changed it and wrote it back, so the second
    write brought back a shortcut the first had removed (uninstalling two games quickly left Roblox in Steam)."""
    import fcntl

    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _shortcuts_worker(payload)


def _shortcuts_worker(payload):
    args = json.loads(payload)
    result = {"state": "done", "added": [], "errors": [], "finished": None}
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    try:
        users = library_users()
        if not args.get("remove") and not library_changes(users, args["packages"]):
            # nothing to change in shortcuts.vdf (e.g. a reinstall): only the artwork, no Steam restart
            for user in users:
                update_library(user, args["packages"], result, shortcuts=False)
            result["unchanged"] = True
            result["finished"] = time.time()
            with open(STATUS_FILE, "w") as f:
                json.dump(result, f)
            return
        deadline = time.time() + 12 * 3600
        while time.time() < deadline:  # restarting Steam would end the game being played, or Desktop Mode
            reason = "game" if game_running() else "desktop" if desktop_mode() else None
            if not reason:
                break
            with open(STATUS_FILE, "w") as f:
                json.dump({"state": "waiting", "reason": reason, "packages": args["packages"],
                           "started": time.time()}, f)
            time.sleep(10)
        try:
            service = stop_steam()
        except AgentError:
            raise AgentError("Steam did not close; library not modified") from None
        for user in steam_users():  # uninstalled games leave every account's library
            remove_from_library(user, args.get("remove", []), result)
        for user in users:
            update_library(user, args["packages"], result)
        if service or args.get("restart", True):
            start_steam(service)
            lost = shortcuts_lost(users, args["packages"])
            if lost:  # Steam wrote its old copy back over ours (it was still saving): once more, then report
                result["retried"] = lost
                service = stop_steam()
                for user in users:
                    update_library(user, lost, {"added": [], "errors": result["errors"]})
                start_steam(service)
                for pkg in shortcuts_lost(users, lost):
                    result["added"] = [a for a in result["added"] if a["package"] != pkg]
                    result["errors"].append(f"{pkg}: Steam removed the new library entry again after restarting")
    except Exception as exc:  # noqa: BLE001
        result["state"] = "failed"
        result["errors"].append(str(exc))
        run(["systemctl", "--user", "start", "steam.service"])
    result["finished"] = time.time()
    with open(STATUS_FILE, "w") as f:
        json.dump(result, f)


def remove_from_library(user, remove, result):
    """Remove uninstalled games' shortcuts + grid art from one Steam account (Steam is closed)."""
    vdf = os.path.join(STEAM, "userdata", user, "config/shortcuts.vdf")
    grid = os.path.join(os.path.dirname(vdf), "grid")
    for r in remove:
        try:
            if remove_shortcut(vdf, r["exe"]):
                result.setdefault("removed", []).append(r["exe"])
            for art in grid_files(grid, r.get("appid") or ""):
                os.remove(art)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{r.get('exe')}: {exc}")


def update_library(user, packages, result, shortcuts=True):
    """Add/update installed games' shortcuts + grid art in one Steam account (Steam is closed; shortcuts=False: only
    the grid art of shortcuts that are already right, while Steam runs)."""
    vdf = os.path.join(STEAM, "userdata", user, "config/shortcuts.vdf")
    grid = os.path.join(os.path.dirname(vdf), "grid")
    os.makedirs(grid, exist_ok=True)
    for pkg in packages:
        try:
            anchor = os.path.join(ANCHORS, pkg)
            dep = json.load(open(os.path.join(anchor, "deployment.json")))
            exe, title, start, icon, tag, tags, openvr = shortcut_args(pkg)
            got = upsert_shortcut(vdf, exe, title, start, icon, tag, tags=tags, openvr=openvr, write=shortcuts)
            got = got[0] if not shortcuts else got
            for kind, suffix in (("portrait", "p"), ("landscape", ""), ("hero", "_hero"), ("logo", "_logo")):
                img = next(iter(glob.glob(os.path.join(anchor, f"artwork/{kind}.*"))), None)
                if not img:
                    continue
                for old in glob.glob(os.path.join(grid, f"{got}{suffix}.*")):
                    os.remove(old)
                shutil.copy(img, os.path.join(grid, f"{got}{suffix}{os.path.splitext(img)[1]}"))
            if not any(a["package"] == pkg for a in result["added"]):
                result["added"].append({"package": pkg, "appid": got, "expected": dep["appid"]})
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{pkg}: {exc}")


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
        dep.setdefault("kind", "quest")
        dep["anchor"] = os.path.dirname(dep_path)  # its artwork/ feeds the Steam grid (shortcuts)
        if dep["kind"] == "pcvr":
            exe = os.path.join(dep["base"], "game", dep.get("exe", ""))
            dep["apk_present"] = os.path.isfile(exe)
            dep["apk_size"] = sum((dep.get("files") or {}).get("game", {}).values())
            dep.pop("files", None)  # large; not needed by the PC
        else:
            apk = os.path.join(dep["base"], "lepton-app/game.apk")
            dep["apk_present"] = os.path.exists(apk)
            dep["apk_size"] = os.path.getsize(apk) if dep["apk_present"] else 0
        games.append(dep)
    return {"games": games}


def steam_gameid(appid):
    """steam://rungameid/ id of a non-Steam shortcut: the 32-bit shortcut appid in the high word, type 0x02000000."""
    return (int(appid) << 32) | 0x02000000


NOT_IN_LIBRARY = "not in the Frame's Steam library"


def cmd_launch(args):
    """Start an installed game the way the headset's library does: ask the running Steam to launch its shortcut, so
    it gets Steam's VR session, overlay and controller setup (unlike launch_test's direct, headless start)."""
    pkg = check_pkg(args["package"])
    dep = deployment(pkg)
    if not dep or not dep.get("appid"):
        raise AgentError(f"{pkg} is not installed")
    if run(["pgrep", "-x", "steam"]).returncode != 0:
        raise AgentError("Steam isn't running on the Frame")
    appid = shortcut_appid_for(f'"{os.path.join(ANCHORS, pkg)}/launch.sh"')
    if appid is None:  # Steam would only say "Game configuration unavailable"
        raise AgentError(f"{NOT_IN_LIBRARY}: {dep.get('title') or pkg}")
    gid = steam_gameid(appid)  # the shortcut's own id (it keeps its first one when the title changes)
    # systemd-run: the launch request must outlive this SSH session
    run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=frameport-launch-{int(time.time())}",
         "steam", "-ifrunning", f"steam://rungameid/{gid}"])
    return {"package": pkg, "gameid": gid, "title": dep.get("title")}


LEPTON_LINK = re.compile(r'ln -s "\$\{HOME\}/([^"/]+)" "\$\{TARGET_PATH\}/([^"/]+)"')
SHARED_DEFAULT = (("Documents", "Documents"), ("Downloads", "Download"), ("Videos", "Movies"))


def lepton_shared_folders():
    """(home folder, Android folder) pairs Lepton links into every app's storage (read from Lepton's mounting.sh;
    it only links folders that exist when an app starts)."""
    lepton, _app = lepton_path()
    text = ""
    if lepton:
        try:
            text = open(os.path.join(os.path.dirname(lepton), "liblepton", "mounting.sh"), errors="replace").read()
        except OSError:
            pass
    pairs = LEPTON_LINK.findall(text)
    return pairs or list(SHARED_DEFAULT)


# Folders Android itself creates in every app's /sdcard; anything else at the top level was made by the app.
ANDROID_STORAGE_DIRS = {"alarms", "android", "audiobooks", "dcim", "documents", "download", "movies", "music",
                        "notifications", "pictures", "podcasts", "recordings", "ringtones", "screenshots"}


def app_media_dirs(ext):
    """The app's own top-level folders in its /sdcard (e.g. 4XVR's 4XPlayer, which its "Internal Storage" list shows
    instead of /sdcard/Movies): real folders only (not Lepton's links to the shared folders), not hidden."""
    try:
        names = sorted(os.listdir(ext))
    except OSError:
        return []
    return [n for n in names if not n.startswith(".") and n.lower() not in ANDROID_STORAGE_DIRS
            and os.path.isdir(os.path.join(ext, n)) and not os.path.islink(os.path.join(ext, n))]


def lepton_external(pkg):
    dep = deployment(pkg)
    if not dep or dep.get("kind") == "pcvr":
        raise AgentError(f"{pkg} is not an installed Quest (Lepton) game")
    return os.path.join(dep["base"], "lepton-data", "external")


def cmd_storage_targets(args):
    """Where files for Lepton apps go: shared folders (seen by every app, e.g. ~/Videos = /sdcard/Movies) and, with a
    package, that app's own storage (/sdcard) and its files folder. Creates missing shared folders (Lepton links only
    existing ones). Android's media index doesn't work in Lepton, so apps must browse folders to find files."""
    out = []
    for home_name, android in lepton_shared_folders():
        path = os.path.join(HOME, home_name)
        os.makedirs(path, exist_ok=True)
        out.append({"id": home_name.lower(), "path": path, "android": "/sdcard/" + android, "shared": True})
    pkg = args.get("package")
    if pkg:
        pkg = check_pkg(pkg)
        ext = lepton_external(pkg)
        files = os.path.join(ext, "Android", "data", pkg, "files")
        os.makedirs(files, exist_ok=True)
        out.append({"id": "app", "path": ext, "android": "/sdcard", "shared": False})
        out.append({"id": "app-files", "path": files, "android": f"/sdcard/Android/data/{pkg}/files", "shared": False})
        for i, name in enumerate(app_media_dirs(ext)):  # the app's own folders (e.g. a video player's library)
            out.append({"id": "app-media" if i == 0 else f"app-media:{name}", "path": os.path.join(ext, name),
                        "android": f"/sdcard/{name}", "shared": False, "folder": name})
    return {"targets": out}


def cmd_link_media(args):
    """Make files from the shared folders (e.g. ~/Videos) appear in an app's own folder too, as hard links (no copy,
    no extra space). For players that list their own folder rather than /sdcard/Movies (4XVR: 4XPlayer). args:
    package, files (paths under ~/Videos, ~/Downloads, ~/Documents), folder (optional; default: the app's first own
    folder, see app_media_dirs). Returns {folder, android, linked, existing, missing}; folder None when the app has no
    own folder (it then finds the files in the shared folders)."""
    pkg = check_pkg(args["package"])
    ext = lepton_external(pkg)
    folder = args.get("folder") or next(iter(app_media_dirs(ext)), None)
    if not folder:
        return {"folder": None, "android": None, "linked": [], "existing": [], "missing": []}
    if "/" in folder or folder in (".", ".."):
        raise AgentError(f"bad folder {folder!r}")
    dest = os.path.join(ext, folder)
    os.makedirs(dest, exist_ok=True)
    shared = [os.path.realpath(os.path.join(HOME, h)) for h, _a in lepton_shared_folders()]
    linked, existing, missing = [], [], []
    for path in args.get("files") or []:
        real = os.path.realpath(path)
        if not any(real == s or real.startswith(s + os.sep) for s in shared):
            raise AgentError(f"{path} is not in a shared folder ({', '.join(shared)})")
        if not os.path.isfile(real):
            missing.append(path)
            continue
        target = os.path.join(dest, os.path.basename(real))
        if os.path.exists(target):
            if os.path.samefile(target, real):
                existing.append(os.path.basename(real))
                continue
            os.remove(target)  # an older file of the same name: show the one just sent
        try:
            os.link(real, target)
        except OSError:
            shutil.copy2(real, target)  # different file system (not the case with Lepton's layout)
        linked.append(os.path.basename(real))
    return {"folder": folder, "android": f"/sdcard/{folder}", "linked": linked, "existing": existing,
            "missing": missing}


def cmd_list_files(args):
    """Every file of an installed game on the Frame, for the PC's file browser: {roots: [{name, path, files:
    [[rel, size], ...]}], missing: [[rel, expected size, actual size or None], ...], truncated}. Roots are the install
    folder (game files, Proton prefix / Lepton data) and, when separate, the launcher folder (launch.sh, artwork).
    `missing` compares against the file list recorded at install time (PC VR games)."""
    pkg = check_pkg(args["package"])
    dep = deployment(pkg)
    if not dep:
        raise AgentError(f"{pkg} is not installed")
    limit = int(args.get("limit", 200000))
    anchor = os.path.join(ANCHORS, pkg)
    roots, count, truncated = [], 0, False
    for name, root in (("Install folder", dep["base"]), ("Launcher", anchor)):
        if not os.path.isdir(root) or any(os.path.realpath(root) == os.path.realpath(r["path"]) for r in roots):
            continue
        files = []
        for r, dirs, names in os.walk(root):
            dirs.sort()
            for n in sorted(names):
                if count >= limit:
                    truncated = True
                    break
                p = os.path.join(r, n)
                try:
                    files.append([os.path.relpath(p, root), os.lstat(p).st_size])
                except OSError:
                    continue
                count += 1
        roots.append({"name": name, "path": root, "files": files})
    missing = []
    for t, manifest in (dep.get("files") or {}).items():
        for rel, size in manifest.items():
            p = os.path.join(dep["base"], t, rel)
            actual = os.path.getsize(p) if os.path.isfile(p) else None
            if actual is None and rel.lower().endswith("crashreportclient.exe") and os.path.isfile(p + ".disabled"):
                continue  # renamed by the no-crash-reporter patch
            if actual != size:
                missing.append([f"{t}/{rel}", size, actual])
    return {"roots": roots, "missing": missing[:1000], "truncated": truncated, "kind": dep.get("kind", "quest")}


def cmd_prepare(args):
    """Where to upload, and what the Frame already has (so unchanged data is not re-sent)."""
    pkg = check_pkg(args["package"])
    title = args["title"]
    ensure_host_fixes()
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
    existing.update(tree_manifest(os.path.join(incoming, "obb")))  # uploaded before an interruption
    apk = os.path.join(app, "game.apk")
    want_sha = args.get("apk_sha256")
    same_apk = bool(want_sha and os.path.exists(apk) and os.path.getsize(apk) == args.get("apk_size")
                    and sha256_file(apk) == want_sha)
    st = os.statvfs(base if os.path.exists(base)
                    else os.path.dirname(base) if os.path.exists(os.path.dirname(base)) else HOME)
    lepton, _ = lepton_path()
    return {"package": pkg, "base": base, "anchor": anchor, "appid": appid, "incoming": incoming,
            "installed": bool(dep), "same_apk": same_apk, "existing_obb": existing,
            "free_bytes": st.f_bavail * st.f_frsize, "lepton": lepton}


LAUNCH_SH = (r"""#!/usr/bin/env bash
# Steam Frame launcher for {title} ({pkg}). Generated by FramePort.
set -euo pipefail
app_dir={base_q}
[[ -d "$app_dir/lepton-app" ]] || {{ echo "Game files missing at $app_dir (storage not mounted?)" >&2; exit 1; }}
# Some games (Unreal cloud saves, SUPERHOT's cloud/data: mode 1700) create folders without write/search permission
# for the app inside Lepton (it writes through the folder's group), which breaks saving or makes the game quit.
# Repair them before and during every launch.
fix_perms() {{ find "$app_dir/lepton-data/external" -type d \( ! -perm -u+rwx -o ! -perm -g+rwx \) """
r"""-exec chmod u+rwx,g+rwx {{}} + 2>/dev/null ||"""
r""" true; }}
# Android can't create an app's external files/cache folders inside Lepton ("Invalid mkdirs path ... not a known app
# path"): getExternalCacheDir() then returns nothing, which breaks e.g. Whirligig's video player cache. Create them"""
r""" here.
mkdir -p "$app_dir/lepton-data/external/Android/data/{pkg}/files" """
r""""$app_dir/lepton-data/external/Android/data/{pkg}/cache" 2>/dev/null || true
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
""")


def write_launcher(anchor, base, pkg, title, appid, lepton, env):
    extra = "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in (env or {}).items()
                    if re.fullmatch(r"[A-Z_][A-Z0-9_]*", k))
    text = LAUNCH_SH.format(title=title.replace("\n", " "), pkg=pkg, base_q=shlex.quote(base), appid=appid,
                            lepton_q=shlex.quote(lepton), extra_env=extra)
    path = os.path.join(anchor, "launch.sh")
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.chmod(path + ".tmp", 0o755)
    os.replace(path + ".tmp", path)


def data_files_dir(base, pkg):
    return os.path.join(base, "lepton-data/external/Android/data", pkg, "files")


def set_flatscreen(app_dir, on):
    """Lepton shows an app as a flat (2D) window only when its app folder holds this marker
    (liblepton/app_metadata.sh); otherwise the app runs headless and only OpenXR output reaches the headset."""
    marker = os.path.join(app_dir, "lepton-show-flatscreen")
    if on:
        open(marker, "a").close()
    elif os.path.exists(marker):
        os.remove(marker)


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
    inc_obb = os.path.join(incoming, "obb")
    expected = args.get("obb_manifest")  # rel path -> size
    if expected:  # check the data as it will be after the move, before replacing anything (a failure keeps the old)
        have = {}
        for top in (os.path.join(app, "obb"), inc_obb):
            for root, _, files in os.walk(top):
                for name in files:
                    if not name.endswith(".part"):
                        p = os.path.join(root, name)
                        have[os.path.relpath(p, top)] = os.path.getsize(p)
        bad = [k for k, v in expected.items() if have.get(k) != v]
        if bad:
            raise AgentError(f"{len(bad)} data file(s) missing or incomplete, e.g. {bad[0]}")
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
    for root, _, files in os.walk(inc_obb):
        for name in files:
            if name.endswith(".part"):
                continue
            src = os.path.join(root, name)
            dst = os.path.join(app, "obb", os.path.relpath(src, inc_obb))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            moved += 1
    shutil.rmtree(incoming, ignore_errors=True)
    if "flatscreen" in args:  # older clients don't send it: leave the marker as it is
        set_flatscreen(app, args["flatscreen"])
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
    models = install_controller_models(files_dir, str(settings.get("controller_models", 0)) not in ("0", "0.0"))
    write_launcher(anchor, base, pkg, title, appid, lepton, args.get("env"))
    art_in = os.path.join(base, "incoming-artwork")
    if os.path.isdir(art_in):
        shutil.rmtree(os.path.join(anchor, "artwork"), ignore_errors=True)
        shutil.move(art_in, os.path.join(anchor, "artwork"))
    dep = {"package": pkg, "appid": int(appid), "base": base, "title": title, "tags": args.get("tags") or [],
           "apk": args.get("apk_name", "game.apk"),
           "sha256": args.get("apk_sha256"), "recipe": args.get("recipe"), "installed_by": "frameport",
           "agent_version": AGENT_VERSION, "time": time.time()}
    with open(os.path.join(anchor, "deployment.json"), "w") as f:
        json.dump(dep, f, indent=2)
    return {"ok": True, "base": base, "appid": appid, "moved_data_files": moved, "controller_models": models}


# --------------------------------------------------------------- Steam Frame controller models (XR_FB_render_model)
# The FrameBridge adapter (setting controller_models=1) serves these to games that ask the runtime for controller
# models.
# They are converted here, on the Frame, from the SteamVR render models the Frame already has (OBJ + PNG) into glTF
# binaries, so Valve's models never leave the device.
MODELS_CACHE = os.path.join(HOME, ".local/share/frameport/controller-models")
FRAME_MODEL_RE = re.compile(r"frame", re.I)
SKIP_COMPONENTS = ("status", "led", "scroll_wheel_touch")


def steamvr_roots():
    roots = []
    rt = openxr_runtime()
    if rt and rt.get("path"):
        roots.append(os.path.dirname(rt["path"]))
    roots += [os.path.join(lib, "steamapps/common/SteamVR") for lib in steam_libraries()]
    roots += ["/opt/steamvr", os.path.join(STEAM, "steamapps/common/SteamVR")]
    out = []
    for r in roots:
        r = os.path.realpath(r)
        if os.path.isdir(r) and r not in out:
            out.append(r)
    return out


def render_model_dirs(roots=None):
    """name -> directory of every SteamVR render model (a folder with .obj files)."""
    found = {}
    for root in roots if roots is not None else steamvr_roots():
        for pattern in ("resources/rendermodels/*", "drivers/*/resources/rendermodels/*"):
            for d in sorted(glob.glob(os.path.join(root, pattern))):
                if os.path.isdir(d) and glob.glob(os.path.join(d, "*.obj")):
                    found.setdefault(os.path.basename(d), d)
    return found


def model_side(name):
    n = name.lower()
    for side in ("left", "right"):
        if side in n:
            return side
    for side in ("left", "right"):  # e.g. controller_l / controller-r
        if re.search(rf"(^|[_\-. ]){side[0]}([_\-. ]|$)", n):
            return side
    return None


def pick_controller_models(dirs, pattern=FRAME_MODEL_RE):
    """{'left': dir, 'right': dir} for the Steam Frame controllers (names matching `pattern`), or {}."""
    best = {}
    for name, d in dirs.items():
        side = model_side(name)
        if not side or not pattern.search(name):
            continue
        score = ("controller" in name.lower()) * 10 - len(name)
        if side not in best or score > best[side][0]:
            best[side] = (score, d)
    return {s: d for s, (_, d) in best.items()} if len(best) == 2 else {}


def _rotation_xyz(deg):
    """3x3 rotation for SteamVR's rotate_xyz (degrees; applied about X, then Y, then Z)."""
    import math
    rx, ry, rz = (math.radians(float(v)) for v in (list(deg) + [0, 0, 0])[:3])
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    X = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    Y = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Z = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    mul = lambda a, b: [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return mul(Z, mul(Y, X))


def model_description(model_dir):
    """(obj files, grip transform or None) from the model's JSON (components) or the folder's .obj files."""
    name = os.path.basename(model_dir)
    desc = {}
    for cand in (os.path.join(model_dir, name + ".json"), *sorted(glob.glob(os.path.join(model_dir, "*.json")))):
        try:
            desc = json.load(open(cand))
            if isinstance(desc, dict) and "components" in desc:
                break
        except (OSError, ValueError):
            desc = {}
    objs, grip = [], None
    for cname, comp in (desc.get("components") or {}).items():
        if not isinstance(comp, dict):
            continue
        if cname == "openxr_grip" and isinstance(comp.get("component_local"), dict):
            local = comp["component_local"]
            grip = (list(local.get("origin") or [0, 0, 0]), _rotation_xyz(local.get("rotate_xyz") or [0, 0, 0]))
        fn = comp.get("filename")
        hidden = (comp.get("visibility") or {}).get("default") is False  # e.g. status LEDs
        if fn and not hidden and not any(s in cname.lower() for s in SKIP_COMPONENTS):
            p = os.path.join(model_dir, fn)
            if os.path.exists(p):
                objs.append(p)
    if not objs:
        whole = os.path.join(model_dir, name + ".obj")
        objs = [whole] if os.path.exists(whole) else sorted(glob.glob(os.path.join(model_dir, "*.obj")))
    return objs, grip


def parse_mtl(path):
    """material name -> texture file (map_Kd)."""
    out, cur = {}, None
    try:
        for line in open(path, errors="replace"):
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            if parts[0] == "newmtl" and len(parts) > 1:
                cur = parts[1].strip()
            elif parts[0] == "map_Kd" and cur and len(parts) > 1:
                out[cur] = os.path.join(os.path.dirname(path), parts[1].strip().split()[-1])
    except OSError:
        pass
    return out


def parse_obj(path):
    """-> list of (texture path or None, positions, normals, uvs, triangles as (v, vt, vn) index triples)."""
    pos, nrm, uv = [], [], []
    groups = {}  # texture -> list of corner triples
    mats, tex = {}, None
    for line in open(path, errors="replace"):
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == "v":
            pos.append(tuple(float(x) for x in parts[1:4]))
        elif tag == "vn":
            nrm.append(tuple(float(x) for x in parts[1:4]))
        elif tag == "vt":
            uv.append((float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0))
        elif tag == "mtllib":
            mats.update(parse_mtl(os.path.join(os.path.dirname(path), " ".join(parts[1:]))))
        elif tag == "usemtl":
            tex = mats.get(" ".join(parts[1:]))
        elif tag == "f":
            corners = []
            for c in parts[1:]:
                idx = (c.split("/") + ["", ""])[:3]
                ref = []
                for i, n in zip(idx, (len(pos), len(uv), len(nrm)), strict=True):
                    ref.append(None if not i else (int(i) - 1 if int(i) > 0 else n + int(i)))
                corners.append(tuple(ref))
            for i in range(1, len(corners) - 1):  # fan triangulation
                groups.setdefault(tex, []).extend((corners[0], corners[i], corners[i + 1]))
    return pos, nrm, uv, groups


def controller_glb(model_dir):
    """glTF binary (one mesh, one primitive per texture, PNG/JPEG textures, in the controller's OpenXR grip space
    when the model defines openxr_grip) for a SteamVR render model folder."""
    objs, grip = model_description(model_dir)
    if not objs:
        raise AgentError(f"no .obj files in {model_dir}")
    # texture -> (positions, normals, uvs, indices), vertices de-duplicated per corner triple
    prims = {}
    for obj in objs:
        pos, nrm, uv, groups = parse_obj(obj)
        for tex, corners in groups.items():
            if tex and os.path.splitext(tex)[1].lower() not in (".png", ".jpg", ".jpeg"):
                tex = None
            p = prims.setdefault(tex, {"pos": [], "nrm": [], "uv": [], "idx": [], "map": {}})
            for c in corners:
                key = (obj, c)
                if key not in p["map"]:
                    p["map"][key] = len(p["pos"])
                    v = pos[c[0]]
                    n = nrm[c[2]] if c[2] is not None and c[2] < len(nrm) else (0.0, 0.0, 1.0)
                    t = uv[c[1]] if c[1] is not None and c[1] < len(uv) else (0.0, 0.0)
                    if grip:  # raw device space -> grip space: R^T (v - origin)
                        o, r = grip
                        d = [v[k] - float(o[k]) for k in range(3)]
                        v = tuple(sum(r[k][j] * d[k] for k in range(3)) for j in range(3))
                        n = tuple(sum(r[k][j] * n[k] for k in range(3)) for j in range(3))
                    p["pos"].append(v)
                    p["nrm"].append(n)
                    p["uv"].append((t[0], 1.0 - t[1]))  # OBJ origin bottom-left, glTF top-left
                p["idx"].append(p["map"][key])
    blob = bytearray()
    views, accessors, images, textures, materials, primitives = [], [], [], [], [], []

    def add_view(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        view = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target:
            view["target"] = target
        blob.extend(data)
        views.append(view)
        return len(views) - 1

    def add_accessor(values, width, ctype, kind, target, minmax=False):
        fmt = {5126: "f", 5125: "I"}[ctype]
        flat = [x for v in values for x in (v if width > 1 else (v,))]
        acc = {"bufferView": add_view(struct.pack(f"<{len(flat)}{fmt}", *flat), target), "componentType": ctype,
               "count": len(values), "type": kind}
        if minmax:
            acc["min"] = [min(v[k] for v in values) for k in range(width)]
            acc["max"] = [max(v[k] for v in values) for k in range(width)]
        accessors.append(acc)
        return len(accessors) - 1

    for tex, p in prims.items():
        if not p["idx"]:
            continue
        attrs = {"POSITION": add_accessor(p["pos"], 3, 5126, "VEC3", 34962, minmax=True),
                 "NORMAL": add_accessor(p["nrm"], 3, 5126, "VEC3", 34962),
                 "TEXCOORD_0": add_accessor(p["uv"], 2, 5126, "VEC2", 34962)}
        indices = add_accessor(p["idx"], 1, 5125, "SCALAR", 34963)
        mat = {"pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.8}}
        if tex and os.path.exists(tex):
            mime = "image/png" if tex.lower().endswith(".png") else "image/jpeg"
            images.append({"bufferView": add_view(open(tex, "rb").read()), "mimeType": mime})
            textures.append({"source": len(images) - 1})
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": len(textures) - 1}
        else:
            mat["pbrMetallicRoughness"]["baseColorFactor"] = [0.1, 0.1, 0.1, 1.0]
        materials.append(mat)
        primitives.append({"attributes": attrs, "indices": indices, "material": len(materials) - 1})
    if not primitives:
        raise AgentError(f"no triangles in {model_dir}")
    while len(blob) % 4:
        blob.append(0)
    gltf = {"asset": {"version": "2.0", "generator": "FramePort agent"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"name": os.path.basename(model_dir), "mesh": 0}],
            "meshes": [{"primitives": primitives}], "materials": materials, "accessors": accessors,
            "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    if images:
        gltf.update(images=images, textures=textures, samplers=[{}])
        for t in textures:
            t["sampler"] = 0
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))


def _tree_stamp(d):
    h = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(d, "*"))):
        st = os.stat(p)
        h.update(f"{os.path.basename(p)}:{st.st_size}:{int(st.st_mtime)}\n".encode())
    return h.hexdigest()[:16]


def frame_controller_models():
    """Converted (cached) glb paths for the Frame controllers: {'left': path, 'right': path, 'sources': {...}}."""
    picked = pick_controller_models(render_model_dirs())
    if not picked:
        raise AgentError("no Steam Frame controller render models found in SteamVR (looked in: "
                         + ", ".join(steamvr_roots() or ["no SteamVR install"]) + ")")
    os.makedirs(MODELS_CACHE, exist_ok=True)
    out = {"sources": picked}
    for side, d in picked.items():
        cached = os.path.join(MODELS_CACHE, f"{os.path.basename(d)}-{_tree_stamp(d)}.glb")
        if not os.path.exists(cached):
            data = controller_glb(d)
            with open(cached + ".tmp", "wb") as f:
                f.write(data)
            os.replace(cached + ".tmp", cached)
        out[side] = cached
    return out


def install_controller_models(files_dir, enabled):
    """Put (or remove) framebridge/controller_{left,right}.glb in a game's files dir. Never fails the install."""
    target = os.path.join(files_dir, "framebridge")
    if not enabled:
        for side in ("left", "right"):
            p = os.path.join(target, f"controller_{side}.glb")
            if os.path.exists(p):
                os.remove(p)
        return None
    try:
        models = frame_controller_models()
    except (AgentError, OSError, ValueError, IndexError) as e:
        return {"ok": False, "error": str(e)}
    os.makedirs(target, exist_ok=True)
    for side in ("left", "right"):
        shutil.copyfile(models[side], os.path.join(target, f"controller_{side}.glb"))
    return {"ok": True, "sources": models["sources"]}


def cmd_controller_models(args):
    """Diagnostics: SteamVR roots, every render model found, and which ones are used as the Frame controllers."""
    dirs = render_model_dirs()
    result = {"roots": steamvr_roots(), "render_models": dirs, "picked": pick_controller_models(dirs)}
    if args.get("convert"):
        try:
            result["converted"] = frame_controller_models()
        except (AgentError, OSError, ValueError, IndexError) as e:
            result["error"] = str(e)
    return result


# ------------------------------------------------------------------------------------------ PC VR (Rift) under Proton
PCVR_TREES = ("game", "revive", "xrlayer", "helpers")


def tree_manifest(root):
    out = {}
    for r, _, files in os.walk(root):
        for name in files:
            if name.endswith(".part"):
                continue
            p = os.path.join(r, name)
            rel = os.path.relpath(p, root)
            if name.lower() == "crashreportclient.exe.disabled":  # renamed by set_crash_reporter: same file
                rel = rel[:-len(".disabled")]
            out[rel] = os.path.getsize(p)
    return out


def pcvr_pids(base):
    """Processes of a PC VR game (the Proton/Wine command lines contain its install folder)."""
    p = run(["pgrep", "-f", re.escape(base.rstrip("/") + "/")])
    return [x for x in p.stdout.split() if x != str(os.getpid())]


def cmd_prepare_pcvr(args):
    """Where to upload a Windows game + Revive, and what the Frame already has (unchanged files aren't re-sent)."""
    pkg = check_pkg(args["package"])
    title = args["title"]
    dest = os.path.expanduser(args.get("dest") or ANCHORS)
    anchor = os.path.join(ANCHORS, pkg)
    dep = deployment(pkg)
    base = dep["base"] if dep else os.path.join(dest, pkg)
    appid = dep["appid"] if dep else shortcut_appid(f'"{anchor}/launch.sh"', title)
    if dep and pcvr_pids(base):
        raise AgentError(f"{title} is running on the Frame. Close it first.")
    incoming = os.path.join(base, "incoming")
    for t in PCVR_TREES:
        os.makedirs(os.path.join(incoming, t), exist_ok=True)
    # files already in place plus files uploaded by an interrupted install (still in incoming/): not sent again
    existing = {t: {**tree_manifest(os.path.join(base, t)), **tree_manifest(os.path.join(incoming, t))}
                for t in PCVR_TREES}
    st = os.statvfs(base if os.path.exists(base) else HOME)
    status = cmd_proton_status({"tool": args.get("tool")})
    return {"package": pkg, "base": base, "anchor": anchor, "appid": appid, "incoming": incoming,
            "installed": bool(dep), "existing": existing, "free_bytes": st.f_bavail * st.f_frsize,
            "proton": status.get("ready"), "proton_suggested": status.get("suggested"), "openxr": status.get("openxr")}


LAUNCH_PROTON_SH = r"""#!/usr/bin/env bash
# Steam Frame launcher for {title} ({pkg}): Windows PC VR game under Proton ({tool}). Generated by FramePort.
set -euo pipefail
base={base_q}
[[ -d "$base/game" ]] || {{ echo "Game files missing at $base (storage not mounted?)" >&2; exit 1; }}
export SteamAppId={appid}
{steam_game_id}export STEAM_COMPAT_APP_ID={appid}
export STEAM_COMPAT_DATA_PATH="$base/compatdata"
export STEAM_COMPAT_CLIENT_INSTALL_PATH={steam_q}
export STEAM_COMPAT_INSTALL_PATH="$base/game"
export STEAM_COMPAT_LIBRARY_PATHS="$base"
export STEAM_COMPAT_SHADER_PATH="$base/shadercache"
export PROTON_LOG_DIR="$base"
{xr_layer}export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
# Wine needs the display session (gamescope's X/Wayland). Steam passes it; headless launches take it from Steam.
if [[ -z "${{DISPLAY:-}}" ]]; then
    steam_pid=$(pgrep -x steam | head -n1 || true)
    if [[ -n "$steam_pid" && -r "/proc/$steam_pid/environ" ]]; then
        while IFS= read -r -d '' kv; do
            case "$kv" in DISPLAY=*|WAYLAND_DISPLAY=*|GAMESCOPE_WAYLAND_DISPLAY=*|XAUTHORITY=*|XDG_SESSION_TYPE=*)
                export "$kv";; esac
        done < "/proc/$steam_pid/environ"
    fi
fi
{extra_env}
mkdir -p "$STEAM_COMPAT_DATA_PATH" "$STEAM_COMPAT_SHADER_PATH"
cd "$base/game"{workdir}
echo "FramePort: launching {pkg} with {tool}" >"$base/launch.log"
exec {command} >>"$base/launch.log" 2>&1
"""


def windows_path(path):
    """Unix path as Wine sees it through drive Z: (the root filesystem)."""
    return "Z:" + os.path.abspath(path).replace("/", "\\")


XR_LAYER = "XR_APILAYER_FRAMEPORT_timefix"
XR_LAYER_ENV = (f'# FramePort OpenXR layer: OpenXR 1.1 -> 1.0 fallback for the Frame runtime'
                f' (+ timespec time emulation)\n'
                f'export XR_API_LAYER_PATH="$base/xrlayer${{XR_API_LAYER_PATH:+:$XR_API_LAYER_PATH}}"\n'
                f'export XR_ENABLE_API_LAYERS="{XR_LAYER}${{XR_ENABLE_API_LAYERS:+:$XR_ENABLE_API_LAYERS}}"\n')


XR_LAYER_MANIFEST = os.path.join(HOME, ".local/share/openxr/1/api_layers/explicit.d", XR_LAYER + ".json")


def install_xr_layer(base):
    """Proton's Steam Linux Runtime container drops XR_API_LAYER_PATH, so the layer is registered where the OpenXR
    loader also looks for explicit layers ($XDG_DATA_HOME/openxr/1/api_layers/explicit.d, shared into the container
    with the home dir). Explicit layers only load when XR_ENABLE_API_LAYERS names them (launch.sh does), so other apps
    are unaffected. The library is a shared copy under the agent's folder."""
    src = os.path.join(base, "xrlayer")
    with open(os.path.join(src, XR_LAYER + ".json")) as f:
        manifest = json.load(f)
    lib_name = os.path.basename(manifest["api_layer"]["library_path"])
    dest = os.path.join(AGENT_HOME, "xrlayer")
    os.makedirs(dest, exist_ok=True)
    tmp = os.path.join(dest, lib_name + ".tmp")
    shutil.copyfile(os.path.join(src, lib_name), tmp)
    os.replace(tmp, os.path.join(dest, lib_name))
    manifest["api_layer"]["library_path"] = os.path.join(dest, lib_name)
    os.makedirs(os.path.dirname(XR_LAYER_MANIFEST), exist_ok=True)
    with open(XR_LAYER_MANIFEST + ".tmp", "w") as f:
        json.dump(manifest, f, indent=4)
    os.replace(XR_LAYER_MANIFEST + ".tmp", XR_LAYER_MANIFEST)


OCULUS_HMD_HELPER = "fp_oculushmd.exe"


def write_proton_launcher(anchor, base, pkg, title, appid, tool, exe_rel, revive, env, xr_layer=False, game_args=(),
                          oculus_hmd=False, vr=True):
    exe = os.path.join(base, "game", exe_rel)
    prefix = compat_command(tool["dir"])
    injector = os.path.join(base, "revive", "ReviveInjector.exe")
    if oculus_hmd:
        # FramePort's helper provides the OculusHMDConnected event (Unreal's Oculus plugin checks for it) and runs the
        # rest of its command line, staying alive until the game has exited
        argv = prefix + [os.path.join(base, "helpers", OCULUS_HMD_HELPER)]
        argv += [windows_path(injector), "/openxr", windows_path(exe)] if revive else [windows_path(exe)]
    elif revive:
        # ReviveInjector joins its arguments into one command line; /openxr = LibReviveXR (OpenXR -> wineopenxr)
        argv = prefix + [injector, "/openxr", windows_path(exe)]
    else:
        argv = prefix + [exe]
    argv += [a for a in game_args or () if re.fullmatch(r"[A-Za-z0-9_=.:/+-]+", a)]  # e.g. -hmd=OpenXR, -vrmode OpenVR
    extra = "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in (env or {}).items()
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k))
    workdir = os.path.dirname(exe_rel)
    text = LAUNCH_PROTON_SH.format(
        title=title.replace("\n", " "), pkg=pkg, base_q=shlex.quote(base), appid=appid, steam_q=shlex.quote(STEAM),
        tool=tool["name"], extra_env=extra, workdir=("/" + shlex.quote(workdir)) if workdir else "",
        # Proton sets up VR (vrclient, wineopenxr) only when SteamGameId is set: a flat Windows game goes without
        steam_game_id=f"export SteamGameId={appid}\n" if vr else "",
        xr_layer=XR_LAYER_ENV if xr_layer else "",
        command=" ".join(shlex.quote(a) for a in argv))
    path = os.path.join(anchor, "launch.sh")
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.chmod(path + ".tmp", 0o755)
    os.replace(path + ".tmp", path)


def set_crash_reporter(base, enabled):
    """Unreal's CrashReportClient.exe in the Frame copy of the game: renamed to .disabled so a crash just closes the
    game (and back when enabled)."""
    game = os.path.join(base, "game")
    for root, _, files in os.walk(game):
        for name in files:
            low = name.lower()
            p = os.path.join(root, name)
            if not enabled and low == "crashreportclient.exe":
                os.replace(p, p + ".disabled")
            elif enabled and low == "crashreportclient.exe.disabled":
                os.replace(p, p[:-len(".disabled")])


def set_libovr_redirect(base, exe_rel, enabled, bundled=False):
    """LoadLibrary redirect (pure runtime substitution): put LibOVRRT{64,32}_1.dll in the game's own DLL search dir as
    a symlink to Revive's LibReviveXR runtime, so the game's Oculus SDK finds a runtime to load. The symlink keeps the
    DLL in the revive/ folder, so its sibling dependencies still resolve. This does NOT touch the game's runtime
    signature check — a build that verifies the Oculus signature of LibOVRRT will still reject Revive's (unsigned)
    runtime; this only helps builds that don't verify it. We only ever create/remove our own symlink, never a real
    DLL the game shipped. Removed when disabled."""
    game = os.path.join(base, "game")
    exe_dir = os.path.dirname(os.path.join(game, exe_rel))
    # the runtime: FramePort's Revive (revive/LibReviveXR*), or with bundled=True the repack's own LibRevive*.dll
    # next to the exe (a repack set up for SteamVR, whose Windows loader hook doesn't take effect under Proton)
    runtimes = {bits: (os.path.join(exe_dir, f"LibRevive{bits}.dll") if bundled else
                       os.path.join(base, "revive", f"LibReviveXR{bits}.dll")) for bits in ("64", "32")}
    # where the Oculus SDK looks for LibOVRRT: the game exe's dir (monolithic engines carry the shim in the exe) AND
    # next to every OVRPlugin.dll (Unreal's shim searches its own module dir).
    dirs = {os.path.dirname(os.path.join(game, exe_rel))}
    for root, _, files in os.walk(game):
        for n in files:
            if n.lower() == "ovrplugin.dll":
                dirs.add(root)
    for d in dirs:
        for bits, target in runtimes.items():
            link = os.path.join(d, f"LibOVRRT{bits}_1.dll")
            ours = os.path.islink(link) and os.path.basename(os.path.realpath(link)).lower().startswith("librevive")
            if ours and os.path.realpath(link) != os.path.realpath(target):
                os.remove(link)  # switched between FramePort's and the bundled Revive
            if enabled and os.path.isfile(target):
                if os.path.islink(link) or not os.path.exists(link):  # never clobber a real game-shipped DLL
                    if os.path.lexists(link):
                        os.remove(link)
                    os.symlink(target, link)
            elif ours:
                os.remove(link)


def cmd_finalize_pcvr(args):
    """Move uploaded game/Revive files into place, delete files the new version no longer has, write the Proton
    launcher and deployment.json. The Proton prefix (saves) is kept."""
    pkg = check_pkg(args["package"])
    title = args["title"]
    prep = cmd_prepare_pcvr({"package": pkg, "title": title, "dest": args.get("dest"), "tool": args.get("tool")})
    base, anchor, appid, incoming = prep["base"], prep["anchor"], prep["appid"], prep["incoming"]
    tool = prep["proton"]
    if not tool:
        raise AgentError("Proton isn't installed on the Frame yet (FramePort: Frame → Install Proton)")
    exe_rel = os.path.normpath(args["exe"])
    if exe_rel.startswith("..") or os.path.isabs(exe_rel):
        raise AgentError(f"bad exe path {args['exe']!r}")
    old = (deployment(pkg) or {}).get("files") or {}
    manifests = args.get("manifests") or {}
    moved = 0
    for t in PCVR_TREES:
        src_root, dst_root = os.path.join(incoming, t), os.path.join(base, t)
        for rel in tree_manifest(src_root):
            dst = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(os.path.join(src_root, rel), dst)
            moved += 1
        want = manifests.get(t)
        if want is None:
            continue
        for rel in set(old.get(t) or {}) - set(want):  # files of the previous version that are gone now
            p = os.path.normpath(os.path.join(dst_root, rel))
            if p.startswith(dst_root + os.sep) and os.path.isfile(p):
                os.remove(p)
        have = tree_manifest(dst_root)
        bad = [k for k, v in want.items() if have.get(k) != v]
        if bad:
            raise AgentError(f"{len(bad)} {t} file(s) missing or incomplete, e.g. {bad[0]}")
    if not os.path.isfile(os.path.join(base, "game", exe_rel)):
        raise AgentError(f"game executable {exe_rel} missing after upload")
    shutil.rmtree(incoming, ignore_errors=True)
    os.makedirs(anchor, exist_ok=True)
    revive = bool(args.get("revive", True))
    if revive and not os.path.isfile(os.path.join(base, "revive", "ReviveInjector.exe")):
        raise AgentError("Revive files missing")
    xr_layer = bool(args.get("xr_layer")) and os.path.isfile(os.path.join(base, "xrlayer", XR_LAYER + ".json"))
    if args.get("xr_layer") and not xr_layer:
        raise AgentError("timefix layer files missing")
    if xr_layer:
        install_xr_layer(base)
    oculus_hmd = bool(args.get("oculus_hmd"))
    if oculus_hmd and not os.path.isfile(os.path.join(base, "helpers", OCULUS_HMD_HELPER)):
        raise AgentError(f"{OCULUS_HMD_HELPER} missing")
    set_crash_reporter(base, enabled=not args.get("no_crash_reporter"))
    set_libovr_redirect(base, exe_rel, enabled=bool(args.get("libovr_redirect")), bundled=not revive)
    vr = args.get("vr", True) is not False  # False: a flat Windows game (no VR at all)
    write_proton_launcher(anchor, base, pkg, title, appid, tool, exe_rel, revive, args.get("env"), xr_layer,
                          args.get("game_args") or [], oculus_hmd, vr=vr)
    art_in = os.path.join(base, "incoming-artwork")
    if os.path.isdir(art_in):
        shutil.rmtree(os.path.join(anchor, "artwork"), ignore_errors=True)
        shutil.move(art_in, os.path.join(anchor, "artwork"))
    dep = {"package": pkg, "kind": "pcvr", "appid": int(appid), "base": base, "title": title, "exe": exe_rel,
           "tags": args.get("tags") or [],
           "sha256": args.get("exe_sha256"), "revive": revive, "revive_version": args.get("revive_version"),
           "proton": tool["name"], "xr_layer": xr_layer, "oculus_hmd": oculus_hmd, "vr": vr,
           "libovr_redirect": bool(args.get("libovr_redirect")),
           "recipe": args.get("recipe"),
           "installed_by": "frameport",
           "files": {t: manifests.get(t) or {} for t in PCVR_TREES},
           "agent_version": AGENT_VERSION, "time": time.time()}
    with open(os.path.join(anchor, "deployment.json"), "w") as f:
        json.dump(dep, f, indent=2)
    return {"ok": True, "base": base, "appid": appid, "moved_files": moved, "proton": tool["name"]}


AWAKE_UNIT = "frameport-awake"


def cmd_keep_awake(args):
    """Keep the Frame from going idle/asleep while FramePort installs games (on=False ends it). An inhibitor lock held
    by a `sleep` in its own user unit, so it outlives this SSH command and ends by itself after `minutes` if FramePort
    disappears. Blocking sleep needs a local session (polkit inhibit-block-sleep: auth_admin_keep for others), so
    when logind refuses it, idle alone is blocked (allowed for any user)."""
    run(["systemctl", "--user", "stop", f"{AWAKE_UNIT}.service"])
    run(["systemctl", "--user", "reset-failed", f"{AWAKE_UNIT}.service"])
    if not args.get("on", True):
        return {"awake": False}
    seconds = int(min(max(float(args.get("minutes", 60)), 1), 240) * 60)
    for what in ("idle:sleep", "idle"):
        run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={AWAKE_UNIT}", "systemd-inhibit",
             f"--what={what}", "--who=FramePort", "--why=Installing games", "--mode=block", "sleep", str(seconds)])
        time.sleep(0.7)  # a refused lock ends the unit right away
        if run(["systemctl", "--user", "is-active", f"{AWAKE_UNIT}.service"]).stdout.strip() == "active":
            return {"awake": True, "what": what, "seconds": seconds}
        run(["systemctl", "--user", "reset-failed", f"{AWAKE_UNIT}.service"])
    return {"awake": False}


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
    models = install_controller_models(files_dir, current.get("controller_models", "0") not in ("0", "0.0"))
    return {"settings": current, "controller_models": models}


def cmd_uninstall(args):
    pkg = check_pkg(args["package"])
    dep = deployment(pkg)
    if not dep:
        return {"removed": False}
    base = dep["base"]
    pcvr = dep.get("kind") == "pcvr"
    if (pcvr_pids(base) if pcvr else container_running(dep["appid"])):
        raise AgentError("the game is running")
    keep_data = args.get("keep_data", True)
    names = ("game", "revive", "xrlayer", "shadercache", "incoming", "incoming-artwork") if pcvr else \
        ("lepton-app", "lepton-shaders", "incoming", "previous-game.apk")
    for name in names:
        p = os.path.join(base, name)
        if os.path.isdir(p):
            remove_tree(p)
        elif os.path.exists(p):
            os.remove(p)
    if not keep_data:
        remove_tree(base)
    anchor = os.path.join(ANCHORS, pkg)
    removed_sc = False
    if args.get("remove_shortcut") and steam_users():
        # Steam keeps its own copy of shortcuts.vdf and writes it back: change it only with Steam closed (worker)
        removed_sc = cmd_shortcuts({"remove": [{"exe": f'"{anchor}/launch.sh"', "appid": dep.get("appid")}]})["started"]
    if not keep_data or base != anchor:
        remove_tree(anchor)
    else:  # saves live next to the launcher (Quest games): keep them, drop what marks the game as installed
        for name in ("deployment.json", "launch.sh", "artwork", "launch.log", "launch-test.log"):
            p = os.path.join(anchor, name)
            remove_tree(p)
    return {"removed": True, "kept_saves": keep_data, "shortcut_removed": removed_sc}


# ------------------------------------------------------------------------------------------ launch tests
def cmd_stop(args):
    dep = deployment(check_pkg(args["package"]))
    if dep:
        run(["systemctl", "--user", "stop", f"frameport-test-{dep['appid']}"])
        if dep.get("kind") == "pcvr":
            stop_pcvr(dep)
        else:
            run(["podman", "kill", f"lepton-steamlaunch-{dep['appid']}"])
    return {"stopped": bool(dep)}


def stop_pcvr(dep):
    """Stop a Proton game: wineserver -k in its prefix, then anything still using its folder."""
    tool = next((t for t in proton_tools() if t["name"] == dep.get("proton")), None) if dep.get("proton") else None
    if tool and tool.get("dir"):
        stop_prefix(tool, os.path.join(dep["base"], "compatdata/pfx"))
    for pid in pcvr_pids(dep["base"]):
        run(["kill", "-TERM", pid])


PCVR_LOGS = ("compatdata/pfx/drive_c/users/steamuser/AppData/Local/Revive/ReviveInjector.txt",)
LOCAL_APPDATA = "compatdata/pfx/drive_c/users/steamuser/AppData/Local"


def game_logs(base, since=0.0):
    """The game's own logs from the Proton prefix, newest first: Unreal Saved/Logs/*.log (tail) and crash summaries
    (Saved/Crashes/*/CrashContext.runtime-xml → error message + call stack), Revive's logs."""
    out = []
    local = os.path.join(base, LOCAL_APPDATA)
    for log in sorted(glob.glob(os.path.join(local, "*", "Saved", "Logs", "*.log")), key=os.path.getmtime,
                      reverse=True)[:1]:
        if os.path.getmtime(log) < since:  # left over from an earlier run
            continue
        text = open(log, errors="replace").read()
        out.append(f"===== game log {os.path.relpath(log, base)}\n" + "\n".join(text.splitlines()[-1500:]))
    for ctx in sorted(glob.glob(os.path.join(local, "*", "Saved", "Crashes", "*", "CrashContext.runtime-xml")),
                      key=os.path.getmtime, reverse=True)[:2]:
        if os.path.getmtime(ctx) < since:
            continue
        raw = open(ctx, "rb").read().decode("utf-8", "replace")
        fields = []
        for tag in ("ErrorMessage", "CrashType", "EngineVersion", "CallStack", "SourceContext"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", raw, re.S)
            if m and m.group(1).strip():
                fields.append(f"{tag}: {m.group(1).strip()[:3000]}")
        out.append(f"===== crash {os.path.relpath(os.path.dirname(ctx), base)} (UE4CC)\n" + "\n".join(fields))
    for rv in glob.glob(os.path.join(local, "Revive", "*.txt")):
        if not rv.endswith("ReviveInjector.txt") and os.path.getmtime(rv) >= since:
            out.append(f"===== {os.path.relpath(rv, base)}\n" + open(rv, errors="replace").read()[-100000:])
    return out


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
    if dep.get("kind") == "pcvr":
        return launch_test_pcvr(dep, anchor, log, seconds)
    if container_running(appid):
        raise AgentError("the game is already running")
    ensure_host_fixes()
    keys = key_usage()
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
        if "Early-exit" in text or "is not a running context" in text:
            state = "NEVER_STARTED"
            break
    elapsed = round(time.time() - start)
    if state == "EXITED":  # Lepton dumps the container's logcat buffers (crash backtraces) after "Exited!"
        until = time.time() + 15
        while time.time() < until and "Dumping logcat" not in (open(log, errors="replace").read()
                                                                if os.path.exists(log) else ""):
            time.sleep(1)
        time.sleep(2)
    run(["systemctl", "--user", "stop", unit])
    run(["podman", "kill", f"lepton-steamlaunch-{appid}"])
    time.sleep(3)
    crash = os.path.join(STEAM, "logs", "lepton-logcats", f"steamlaunch-{appid}", "logcat-crash.log")
    fresh = os.path.exists(crash) and os.path.getmtime(crash) >= start - 1 and os.path.getsize(crash) > 0
    return {"state": state, "elapsed": elapsed, "log": log,
            "log_size": os.path.getsize(log) if os.path.exists(log) else 0,
            "crash_log": crash if fresh else None,
            "kernel_keys_before": keys, "kernel_keys_after": key_usage()}


def launch_test_pcvr(dep, anchor, log, seconds):
    base, appid = dep["base"], dep["appid"]
    if pcvr_pids(base):
        raise AgentError("the game is already running")
    unit = f"frameport-test-{appid}"
    run(["systemctl", "--user", "reset-failed", unit])
    p = run(["systemd-run", "--user", "--quiet", f"--unit={unit}", "--property=RemainAfterExit=no",
             os.path.join(anchor, "launch.sh")])
    if p.returncode:
        raise AgentError("could not start the launcher: " + p.stderr[-300:])
    start = time.time()
    state = "RUNNING"
    game_seen = False
    exe_name = os.path.basename(dep.get("exe", ""))
    while time.time() - start < seconds:
        time.sleep(3)
        # Wine names game processes by their Windows path (X:\...\Game.exe); the Proton command line doesn't start so
        game_seen = game_seen or bool(exe_name and run(
            ["pgrep", "-if", r"^[a-z]:\\.*" + re.escape(exe_name)]).returncode == 0)
        active = run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0
        if not active:
            state = "EXITED" if game_seen else "NEVER_STARTED"
            break
    elapsed = round(time.time() - start)
    if state == "RUNNING" and not game_seen:
        state = "NEVER_STARTED"  # the launcher is up but the game process never appeared
    run(["systemctl", "--user", "stop", unit])
    stop_pcvr(dep)
    time.sleep(3)
    # one log for triage: launcher/Proton output + Revive's logs + the game's own (Unreal) log and crash summaries +
    # Proton's own log when enabled
    parts = []
    for rel in ("launch.log",) + PCVR_LOGS + (f"steam-{appid}.log",):
        path = os.path.join(base, rel)
        if os.path.exists(path) and os.path.getmtime(path) >= start - 5:  # not left over from an earlier run
            parts.append(f"===== {rel}\n" + open(path, errors="replace").read()[-400000:])
    parts += game_logs(base, since=start - 5)
    combined = os.path.join(base, "launch-test.log")
    with open(combined, "w") as f:
        f.write("\n".join(parts))
    return {"state": state, "elapsed": elapsed, "log": combined, "log_size": os.path.getsize(combined),
            "kind": "pcvr", "game_process": bool(game_seen)}


def _tail(path, max_bytes):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        text = data.decode("utf-8", "replace")
        return (f"[... first {size - max_bytes} bytes cut ...]\n" if size > max_bytes else "") + text
    except OSError:
        return None


def cmd_collect_diag(args):
    """Everything useful for debugging without the game or the PC app: host runtime facts and, with a package, the
    game's launcher, settings, deployment, logs (launch, Lepton logcat, Proton/Revive/Unreal) and its file listing.
    Returns {"host": {...}, "files": {name: text}, "listing": ...}; the PC redacts and zips it."""
    max_bytes = int(args.get("max_bytes", 2 << 20))
    files, host = {}, {}
    host["openxr_runtime"] = openxr_runtime()
    layers = os.path.join(HOME, ".local/share/openxr/1/api_layers/explicit.d")
    host["openxr_layers"] = sorted(os.listdir(layers)) if os.path.isdir(layers) else []
    host["kernel_keys"] = key_usage()
    host["containers_conf"] = _tail(CONTAINERS_CONF, 20000)
    host["podman"] = run(["podman", "ps", "-a", "--filter", "name=lepton", "--format",
                          "{{.Names}} {{.Status}}"]).stdout[-20000:]
    host["steam_running"] = run(["pgrep", "-x", "steam"]).returncode == 0
    host["uptime"] = _tail("/proc/uptime", 200)
    host["boot"] = boot_state()
    # how the previous boot ended: errors and kernel (GPU/msm/kgsl, OOM, panic) warnings before a crash or reset; and
    # this boot's kernel warnings (a GPU hang the Frame recovered from only ends the game: no reboot, no tombstone)
    for name, boot, extra in (("previous-boot-errors.txt", "-1", ["-p", "err"]),
                              ("previous-boot-kernel.txt", "-1", ["-k", "-p", "warning"]),
                              ("this-boot-kernel.txt", "0", ["-k", "-p", "warning"])):
        try:
            text = run(["journalctl", "-b", boot, *extra, "-n", "400", "-q", "--no-pager"]).stdout[-max_bytes:]
        except OSError:
            text = ""
        if text.strip():
            files[name] = text
    host["installed"] = [{k: g.get(k) for k in ("package", "title", "kind", "appid", "version", "agent_version")}
                         for g in cmd_list_installed({})["games"]]
    out = {"agent_version": AGENT_VERSION, "host": host, "files": files}
    pkg = args.get("package")
    if not pkg:
        # the OpenXR runtime's own log (XRService-<date>_<time>.log): the newest one
        xr = [p for p in glob.glob(os.path.join(STEAM, "logs/XRService-*.log")) +
              glob.glob(os.path.join(STEAM, "logs/XRService-*/XRService-*.log")) if os.path.isfile(p)]
        for p in sorted(xr, key=os.path.getmtime, reverse=True)[:1]:
            files[os.path.basename(p)] = _tail(p, max_bytes)
        return out
    pkg = check_pkg(pkg)
    dep = deployment(pkg)
    anchor = os.path.join(ANCHORS, pkg)
    if not dep:
        out["installed"] = False
        return out
    out["installed"] = True
    base, appid = dep["base"], dep.get("appid")
    dep = dict(dep)
    dep.pop("files", None)  # the install manifest is large; `listing` below has the real files
    files["deployment.json"] = json.dumps(dep, indent=1)
    for name, path in (("launch.sh", os.path.join(anchor, "launch.sh")),
                       ("settings.conf", os.path.join(base, "settings.conf")),
                       ("launch.log", os.path.join(base, "launch.log")),
                       ("launch-test.log", os.path.join(base, "launch-test.log"))):
        text = _tail(path, max_bytes)
        if text is not None:
            files[name] = text
    for rel in PCVR_LOGS + (f"steam-{appid}.log",):
        text = _tail(os.path.join(base, rel), max_bytes)
        if text is not None:
            files[os.path.basename(rel)] = text
    if dep.get("kind") == "pcvr":
        for i, part in enumerate(game_logs(base)):
            files[f"game-log-{i}.txt"] = part[-max_bytes:]
    logs = os.path.join(STEAM, "logs")
    text = _tail(os.path.join(logs, f"lepton-steamlaunch-{appid}.log"), max_bytes)  # Lepton's launcher log
    if text is not None:
        files[f"lepton-steamlaunch-{appid}.log"] = text
    logcats = os.path.join(logs, "lepton-logcats")
    # the container's logcat buffers: lepton-logcats/steamlaunch-<appid>/logcat-{main,crash,system,kernel,radio}.log
    order = ("main", "crash", "system", "kernel", "radio")
    cands = [p for p in glob.glob(os.path.join(logcats, f"steamlaunch-{appid}", "*")) if os.path.isfile(p)]
    for p in sorted(cands, key=lambda p: next((i for i, k in enumerate(order) if k in os.path.basename(p)), 9)):
        name = os.path.basename(p)
        files[name if name.startswith("logcat") else "logcat-" + name] = _tail(p, max_bytes)
    try:
        listing = cmd_list_files({"package": pkg, "limit": 20000})
        out["listing"] = {"missing": listing["missing"], "truncated": listing["truncated"],
                          "roots": [{"name": r["name"], "files": r["files"]} for r in listing["roots"]]}
    except AgentError:
        pass
    return out


AGENT_HOME = os.path.join(HOME, ".local/share/frameport")


def cmd_purge(args):
    """Remove everything FramePort put on this Frame: its games (keep_saves: leave Quest save data and PC VR Proton
    prefixes), their Steam shortcuts + grid art, ~/Applications/quest-frame, and ~/.local/share/frameport (this agent,
    Proton self-test prefix, timefix layer). Runs detached (Steam is closed while shortcuts.vdf changes); poll
    purge_status. Proton/Lepton stay installed (they're Steam apps) and the podman keyring fix stays (harmless)."""
    games = cmd_list_installed({})["games"]
    if any((pcvr_pids(d["base"]) if d.get("kind") == "pcvr" else container_running(d["appid"])) for d in games):
        raise AgentError("a FramePort game is running on the Frame; close it first")
    status = os.path.join(HOME, ".cache/frameport-purge.json")
    os.makedirs(os.path.dirname(status), exist_ok=True)
    with open(status, "w") as f:
        json.dump({"state": "running", "started": time.time()}, f)
    payload = json.dumps({"keep_saves": bool(args.get("keep_saves", True)), "status": status})
    run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=frameport-purge-{int(time.time())}",
         "--setenv=HOME=" + HOME, sys.executable, os.path.abspath(__file__), "_purge_worker", payload])
    return {"started": True, "games": len(games), "status": status}


def purge_worker(payload):
    args = json.loads(payload)
    keep = args["keep_saves"]
    result = {"state": "done", "removed": [], "kept": [], "errors": []}
    service = True
    try:
        games = cmd_list_installed({})["games"]
        users = steam_users()
        try:
            service = stop_steam()
        except AgentError as exc:
            result["errors"].append(str(exc))
            service = True
        for u in users:
            vdf = os.path.join(STEAM, "userdata", u, "config/shortcuts.vdf")
            grid = os.path.join(STEAM, "userdata", u, "config/grid")
            for d in games:
                try:
                    if remove_shortcut(vdf, f'"{os.path.join(ANCHORS, d["package"])}/launch.sh"'):
                        result["removed"].append(f"Steam shortcut: {d.get('title')}")
                    for art in grid_files(grid, d["appid"]):
                        os.remove(art)
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"{d.get('title')}: {exc}")
            try:  # shortcuts left from games without an install record (older versions, interrupted removals)
                root = vdf_decode(open(vdf, "rb").read()) if os.path.exists(vdf) else {}
                for sc in list(root.get("shortcuts", {}).values()):
                    exe = sc.get("Exe", "") if isinstance(sc, dict) else ""
                    if exe.startswith(f'"{ANCHORS}/') and remove_shortcut(vdf, exe):
                        result["removed"].append(f"Steam shortcut: {sc.get('AppName') or sc.get('appname')}")
                        for art in grid_files(grid, sc.get("appid", 0) & 0xFFFFFFFF):
                            os.remove(art)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"shortcuts: {exc}")
        for d in games:
            base = d["base"]
            saves = [os.path.join(base, n) for n in ("lepton-data", "compatdata")]
            if keep and any(os.path.exists(p) for p in saves):
                for name in os.listdir(base) if os.path.isdir(base) else []:
                    p = os.path.join(base, name)
                    if p not in saves:
                        remove_tree(p)
                result["kept"].append(base)
            else:
                remove_tree(base)
            if not keep and os.path.lexists(base):
                result["errors"].append(f"couldn't remove {base}")
            result["removed"].append(d.get("title") or d["package"])
        if not keep or not result["kept"]:
            remove_tree(ANCHORS)
        else:  # anchors hold only launchers/artwork; saves live in the bases
            for d in games:
                anchor = os.path.join(ANCHORS, d["package"])
                if os.path.realpath(anchor) not in [os.path.realpath(k) for k in result["kept"]]:
                    remove_tree(anchor)
        remove_tree(AGENT_HOME)
        result["removed"].append(AGENT_HOME)
        if os.path.exists(XR_LAYER_MANIFEST):
            os.remove(XR_LAYER_MANIFEST)
            result["removed"].append(XR_LAYER_MANIFEST)
        for name in ("frameport-setup.sh", "frameport-setup.log"):  # left by bootstrap.sh
            p = os.path.join(HOME, ".cache", name)
            if os.path.exists(p):
                os.remove(p)
                result["removed"].append(p)
    except Exception as exc:  # noqa: BLE001
        result["state"] = "failed"
        result["errors"].append(str(exc))
    finally:
        start_steam(service)
    result["finished"] = time.time()
    with open(args["status"], "w") as f:
        json.dump(result, f)


def cmd_purge_status(args):
    try:
        return json.load(open(os.path.join(HOME, ".cache/frameport-purge.json")))
    except (OSError, ValueError):
        return {"state": "none"}


def cmd_cleanup(args):
    """Remove rollback copies (previous-game.apk, settings.conf.previous) and leftover uploads; optional extra paths
    under HOME (e.g. an old manual-install folder). Saves and installed games are never touched."""
    freed, removed = 0, []
    for dep in cmd_list_installed({})["games"]:
        base = dep["base"]
        for name in (["previous-game.apk", "settings.conf.previous"] if args.get("rollback", True) else []) + \
                ["incoming", "incoming-artwork"]:
            p = os.path.join(base, name)
            if os.path.isdir(p):
                freed += sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)
                remove_tree(p)
                removed.append(p)
            elif os.path.exists(p):
                freed += os.path.getsize(p)
                os.remove(p)
                removed.append(p)
    for extra in args.get("paths", []):
        p = os.path.realpath(os.path.expanduser(extra))
        first = os.path.relpath(p, HOME).split(os.sep)[0] if p.startswith(HOME + os.sep) else ""
        if not first or first.startswith(".") or p == ANCHORS or p.startswith(ANCHORS + os.sep):
            # only ordinary folders in the home folder: never dot folders (.ssh, .steam, .local, .config…)
            raise AgentError(f"refusing to remove {extra}")
        if os.path.exists(p):
            freed += (sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)
                      if os.path.isdir(p) else os.path.getsize(p))
            remove_tree(p)
            removed.append(p)
    return {"removed": removed, "freed_bytes": freed}


# ------------------------------------------------------------------------------------------ virtual keyboard
# "Type on Frame": the PC's key presses become a real keyboard on the Frame (Linux uinput; the steamos user may open
# /dev/uinput, an ACL entry made for Steam Input, no root). A real input device reaches everything that has focus:
# Android windows (Lepton's wayland_keyboard), the Steam UI, the desktop, Proton games.
UINPUT = "/dev/uinput"
UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = (0x40045564, 0x40045565, 0x405C5503,
                                                                         0x5501, 0x5502)
EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
KEY_LAST = 248  # KEY_ESC (1) .. KEY_MICMUTE (248): every key a PC keyboard sends
KEY_LEFTSHIFT = 42


def text_keys():
    """US layout: character -> (Linux key code, shift)."""
    keys = {"\n": (28, False), "\t": (15, False), " ": (57, False), "\b": (14, False)}
    for plain, shifted, first in (("1234567890-=", "!@#$%^&*()_+", 2), ("qwertyuiop[]", "QWERTYUIOP{}", 16),
                                  ("asdfghjkl;'`", 'ASDFGHJKL:"~', 30), ("\\zxcvbnm,./", "|ZXCVBNM<>?", 43)):
        for i, (a, b) in enumerate(zip(plain, shifted, strict=True)):
            keys[a] = (first + i, False)
            keys[b] = (first + i, True)
    return keys


TEXT_KEYS = text_keys()


class VirtualKeyboard:
    """A uinput keyboard. `fd`/`write` can be replaced in tests; the device goes away with close()."""

    def __init__(self, fd=None, write=os.write, settle=0.8):
        self.write, self.held = write, set()
        self.fd = fd
        if fd is None:
            self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for code in range(1, KEY_LAST + 1):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x03, 0x1209, 0x4650, 1,
                                                            b"FramePort keyboard", 0))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
            time.sleep(settle)  # let gamescope/libinput pick the new keyboard up before the first key

    def _event(self, kind, code, value):
        self.write(self.fd, struct.pack("llHHi", 0, 0, kind, code, value))

    def key(self, code, value):
        """value 1 = down, 0 = up, 2 = autorepeat."""
        if not 0 < int(code) <= KEY_LAST or value not in (0, 1, 2):
            return
        self._event(EV_KEY, int(code), value)
        self._event(EV_SYN, SYN_REPORT, 0)
        (self.held.add if value else self.held.discard)(int(code))

    def type_text(self, text, delay=0.008):
        """Type characters of the US layout; returns the characters it couldn't type."""
        skipped = ""
        for ch in text.replace("\r\n", "\n"):
            if ch not in TEXT_KEYS:
                skipped += ch
                continue
            code, shift = TEXT_KEYS[ch]
            if shift:
                self.key(KEY_LEFTSHIFT, 1)
            self.key(code, 1)
            self.key(code, 0)
            if shift:
                self.key(KEY_LEFTSHIFT, 0)
            time.sleep(delay)
        return skipped

    def close(self):
        for code in list(self.held):  # never leave a key stuck when the PC goes away mid-press
            self.key(code, 0)
        if self.fd is not None and isinstance(self.fd, int):
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
        self.fd = None


def keyboard_session(stdin, stdout, keyboard=None):
    """Long-lived: prints {"ready": true} once the virtual keyboard exists, then reads one JSON object per line:
    {"k": code, "v": 1|0|2} (key down/up/repeat) or {"text": "..."}; ends (keyboard removed) at EOF."""
    try:
        kb = keyboard or VirtualKeyboard()
    except OSError as exc:
        stdout.write(json.dumps({"ready": False, "error": f"can't create a virtual keyboard: {exc}"}) + "\n")
        stdout.flush()
        return 1
    stdout.write(json.dumps({"ready": True}) + "\n")
    stdout.flush()
    try:
        for line in stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "k" in msg:
                kb.key(msg["k"], msg.get("v", 1))
            elif "text" in msg:
                skipped = kb.type_text(str(msg["text"]))
                stdout.write(json.dumps({"typed": True, "skipped": skipped}) + "\n")
                stdout.flush()
    except (OSError, ValueError):
        pass
    finally:
        kb.close()
    return 0


COMMANDS = {n[4:]: f for n, f in globals().items() if n.startswith("cmd_")}


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "_keyboard":
        return keyboard_session(sys.stdin, sys.stdout)
    if len(sys.argv) >= 3 and sys.argv[1] == "_shortcuts_worker":
        shortcuts_worker(sys.argv[2])
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "_purge_worker":
        purge_worker(sys.argv[2])
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "_xr_probe":
        xr_probe(sys.argv[2])
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "_tools_worker":
        tools_worker(sys.argv[2])
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
