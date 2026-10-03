"""Did the Frame crash because of a game? (no Flet) From the agent's boot state: a new boot whose predecessor didn't
shut down cleanly, with a FramePort game started shortly before it."""
from __future__ import annotations

SOON = 3 * 3600  # a game started up to 3 h before the restart may have caused it


def crashed_game(boot: dict, previous_boot_id: str | None) -> dict | None:
    """The game to blame ({"package", "title", "time"}) or None. Only for a boot FramePort hasn't seen before whose
    previous boot ended without a clean shutdown (a battery shutdown or a normal power-off is clean)."""
    if not previous_boot_id or boot.get("boot_id") == previous_boot_id or boot.get("prev_clean") is not False:
        return None
    last, boot_time = boot.get("last_launch"), boot.get("boot_time")
    if not last or not boot_time or not 0 <= boot_time - last.get("time", 0) <= SOON:
        return None
    return last
