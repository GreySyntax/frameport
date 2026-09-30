"""The Windows PC FramePort runs on: native Windows, or WSL (Windows programs via interop, C: at /mnt/c).

Used by the PC VR (Revive) target: finding Windows Steam, its active user, SteamVR, and starting/stopping Windows
processes. Everything is discovered (registry, loginusers.vdf, libraryfolders.vdf); nothing about the user is stored.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

STEAMID64_BASE = 76561197960265728
STEAMVR_APPID = "250820"


def is_windows() -> bool:
    return sys.platform == "win32"


@lru_cache(maxsize=1)
def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def available() -> bool:
    return is_windows() or is_wsl()


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if is_windows() else {}  # CREATE_NO_WINDOW


def system32(exe: str) -> str:
    """Path to a Windows system tool (tasklist.exe, reg.exe, ...) callable from here."""
    if is_windows():
        return exe
    for root in ("/mnt/c/Windows/System32", "/mnt/c/WINDOWS/system32"):
        if Path(root, exe).exists():
            return str(Path(root, exe))
    return shutil.which(exe) or exe


def run_win(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, **_no_window())


def to_windows(path: Path | str) -> str:
    """Local path -> Windows path (D:\\Games\\X). Identity on Windows."""
    if is_windows():
        return str(Path(path).resolve())
    p = str(Path(path).resolve())
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", p)
    if m:
        return f"{m[1].upper()}:" + (m[2] or "/").replace("/", "\\")
    r = subprocess.run(["wslpath", "-w", p], capture_output=True, text=True)
    if r.returncode:
        raise ValueError(f"{p} is not reachable from Windows")
    return r.stdout.strip()


def to_local(winpath: str) -> Path:
    """Windows path -> local path (/mnt/d/... on WSL)."""
    if is_windows():
        return Path(winpath)
    m = re.match(r"^([a-zA-Z]):[\\/](.*)$", winpath.strip())
    if m:
        return Path(f"/mnt/{m[1].lower()}") / m[2].replace("\\", "/")
    r = subprocess.run(["wslpath", "-u", winpath], capture_output=True, text=True)
    return Path(r.stdout.strip())


def reg_query(key: str, value: str) -> str | None:
    """A registry value ("" = the key's default value). 64-bit view first, then the 32-bit one."""
    if is_windows():
        import winreg

        hive, _, sub = key.partition("\\")
        root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as k:
                    return str(winreg.QueryValueEx(k, value)[0])
            except OSError:
                continue
        return None
    for view in ("/reg:64", "/reg:32"):
        try:
            r = run_win([system32("reg.exe"), "query", key, *(["/ve"] if value == "" else ["/v", value]), view])
        except (OSError, subprocess.TimeoutExpired):
            return None
        name = r"\(Default\)|\(Standard\)|\S*" if value == "" else re.escape(value)  # "(Default)" is localized
        m = re.search(rf"^\s*(?:{name})\s+REG_\w+\s+(.+?)\s*$", r.stdout, re.M)
        if m:
            return m[1]
    return None


def env_path(name: str) -> Path | None:
    """%LOCALAPPDATA% etc. of the Windows user, as a local path."""
    if is_windows():
        v = os.environ.get(name)
        return Path(v) if v else None
    try:
        r = run_win([system32("cmd.exe"), "/c", f"echo %{name}%"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    v = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return to_local(v) if v and "%" not in v else None


# ------------------------------------------------------------------------------------------ Steam
def steam_root() -> Path | None:
    candidates = []
    v = reg_query(r"HKCU\Software\Valve\Steam", "SteamPath")
    if v:
        candidates.append(to_local(v.replace("/", "\\")))
    candidates += [to_local(r"C:\Program Files (x86)\Steam"), to_local(r"C:\Program Files\Steam")]
    return next((c for c in candidates if (c / "steam.exe").exists()), None)


def steam_libraries(root: Path) -> list[Path]:
    libs = [root / "steamapps"]
    try:
        text = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        for p in re.findall(r'"path"\s+"([^"]+)"', text):
            lib = to_local(p.replace("\\\\", "\\")) / "steamapps"
            if lib not in libs and lib.is_dir():
                libs.append(lib)
    except OSError:
        pass
    return libs


def app_installed(root: Path, appid: str) -> bool:
    return any((lib / f"appmanifest_{appid}.acf").exists() for lib in steam_libraries(root))


def steam_user(root: Path) -> str | None:
    """userdata folder name (account id) of the most recent Steam login; the only one if there's just one."""
    users = sorted(p.name for p in (root / "userdata").glob("*") if p.name.isdigit() and p.name != "0")
    try:
        text = (root / "config/loginusers.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for sid, body in re.findall(r'"(\d{17})"\s*\{([^}]*)\}', text):
        if re.search(r'"MostRecent"\s+"1"', body):
            acct = str(int(sid) - STEAMID64_BASE)
            if acct in users:
                return acct
    return users[0] if len(users) == 1 else None


def process_running(image: str) -> bool:
    try:
        r = run_win([system32("tasklist.exe"), "/FI", f"IMAGENAME eq {image}", "/NH"], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return image.lower() in r.stdout.lower()


def kill(image: str) -> None:
    try:
        run_win([system32("taskkill.exe"), "/IM", image, "/F", "/T"], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


def start_detached(exe: Path, args: list[str] = (), cwd: Path | None = None) -> None:
    """Start a Windows program without waiting (Steam, the Revive injector)."""
    if is_windows():
        subprocess.Popen([str(exe), *args], cwd=cwd, close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        return
    subprocess.Popen([str(exe), *args], cwd=str(cwd) if cwd else None, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)


def stop_steam(root: Path, wait: float = 40) -> bool:
    """Ask Steam to exit; True if it was running."""
    if not process_running("steam.exe"):
        return False
    start_detached(root / "steam.exe", ["-shutdown"])
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1)
        if not process_running("steam.exe"):
            time.sleep(2)  # let it finish writing its files
            return True
    raise RuntimeError("Steam did not close; the library was not changed")


def start_steam(root: Path) -> None:
    start_detached(root / "steam.exe")
