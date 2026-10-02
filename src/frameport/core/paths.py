"""Filesystem locations: bundled data (catalog, artifacts, agent) and the per-user data directory."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent
# Installed wheel: data is force-included under frameport/_data. Source checkout: repo root.
_REPO = _PKG.parent.parent
DATA_ROOT = _PKG / "_data" if (_PKG / "_data").is_dir() else _REPO


def catalog_dir() -> Path:
    return DATA_ROOT / "catalog"


def artifacts_dir() -> Path:
    return DATA_ROOT / "artifacts"


def agent_dir() -> Path:
    return DATA_ROOT / "agent"


def bootstrap_dir() -> Path:
    return DATA_ROOT / "bootstrap"


def user_data_dir() -> Path:
    """Tools, keystores, work dirs and settings. Override with FRAMEPORT_HOME."""
    if os.environ.get("FRAMEPORT_HOME"):
        base = Path(os.environ["FRAMEPORT_HOME"])
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "FramePort"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/FramePort"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "frameport"
    if not _removed.is_set():
        base.mkdir(parents=True, exist_ok=True)
    return base


_removed = threading.Event()


def mark_removed() -> None:
    """FramePort is being uninstalled: from now on nothing may (re)create files in the data folder."""
    _removed.set()


def removed() -> bool:
    return _removed.is_set()


def tools_dir() -> Path:
    return _sub("tools")


def work_dir() -> Path:
    return _sub("work")


def output_dir() -> Path:
    return _sub("output")


def ssh_dir() -> Path:
    return _sub("ssh")


def _sub(name: str) -> Path:
    path = user_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_atomic(path: Path, text: str) -> None:
    """Replace a file in one step: a reader never sees it half written, and a crash leaves the old one."""
    path = Path(path)
    if removed():
        return
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
