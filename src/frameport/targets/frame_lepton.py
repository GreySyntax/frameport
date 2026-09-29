"""Steam Frame target: one Lepton container per game + a Steam library shortcut."""
from __future__ import annotations

from pathlib import Path

from ..core.events import Reporter
from ..core.models import Recipe
from ..frame.connection import Frame, FrameTarget
from ..install import installer
from ..validate import device
from .base import Target


class FrameLeptonTarget(Target):
    kind = "frame"

    def __init__(self, target: FrameTarget, password: str | None = None):
        self.target = target
        self.frame = Frame(target, password)
        self.label = target.label

    def connect(self) -> "FrameLeptonTarget":
        if self.frame.client is None:
            self.frame.connect()
        return self

    def describe(self) -> dict:
        return self.connect().frame.agent("info")

    def installed(self) -> list[dict]:
        return self.connect().frame.agent("list_installed")["games"]

    def install(self, package, title, apk: Path, data_dir, recipe: Recipe, reporter: Reporter, apk_only=False):
        plan = installer.InstallPlan(package, title, apk, data_dir, recipe, apk_only)
        return installer.install(self.connect().frame, plan, reporter)

    def add_to_library(self, packages, reporter):
        return installer.add_to_steam(self.connect().frame, packages, reporter)

    def launch_test(self, package, reporter, seconds=45):
        return device.launch_test(self.connect().frame, package, reporter, seconds)

    def set_settings(self, package, settings):
        return self.connect().frame.agent("set_settings", package=package, settings=settings)

    def uninstall(self, package, keep_data=True):
        return self.connect().frame.agent("uninstall", package=package, keep_data=keep_data, remove_shortcut=True)

    def install_lepton(self) -> dict:
        return self.connect().frame.agent("install_lepton")

    def close(self):
        self.frame.close()
