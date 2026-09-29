"""Install targets. The pipeline only talks to this interface, so new targets (e.g. Revive for PC/Rift games) plug in
without touching the build, recommend or UI layers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..core.events import Reporter
from ..core.models import Recipe


class Target(ABC):
    kind: str = ""
    label: str = ""

    @abstractmethod
    def describe(self) -> dict: ...

    @abstractmethod
    def installed(self) -> list[dict]: ...

    @abstractmethod
    def install(self, package: str, title: str, apk: Path, data_dir: Path | None, recipe: Recipe,
                reporter: Reporter, apk_only: bool = False) -> dict: ...

    @abstractmethod
    def add_to_library(self, packages: list[str], reporter: Reporter) -> dict: ...

    @abstractmethod
    def launch_test(self, package: str, reporter: Reporter, seconds: int = 45): ...

    @abstractmethod
    def set_settings(self, package: str, settings: dict) -> dict: ...

    @abstractmethod
    def uninstall(self, package: str, keep_data: bool = True) -> dict: ...
