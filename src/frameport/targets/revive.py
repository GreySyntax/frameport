"""Planned: PC VR target via Revive (Rift/Meta PC store games → SteamVR, streamed to the Frame).

Not implemented yet. It will implement targets.base.Target: detect a Revive install on the PC, register the game
with SteamVR (vrmanifest), and use Steam's streaming to the Frame. Catalog entries already carry
`pcvr_alternative` notes for games that can't run standalone (32-bit titles, GPU driver crashes).
"""
from __future__ import annotations

from .base import Target


class ReviveTarget(Target):  # pragma: no cover - placeholder
    kind = "revive"
    label = "PC VR (Revive)"

    def _todo(self, *a, **k):
        raise NotImplementedError("The Revive target is planned but not implemented yet.")

    describe = installed = install = add_to_library = launch_test = set_settings = uninstall = _todo
