from frameport.ui.restart import crashed_game

LAUNCH = {"package": "com.x", "title": "X", "time": 1000.0}


def boot(**kw):
    return {"boot_id": "b2", "boot_time": 2000, "prev_clean": False, "last_launch": LAUNCH, **kw}


def test_unclean_restart_soon_after_a_launch_blames_the_game():
    assert crashed_game(boot(), "b1") == LAUNCH


def test_no_blame_for_clean_shutdowns_first_sight_old_launches_or_same_boot():
    assert crashed_game(boot(prev_clean=True), "b1") is None  # powered off / battery shutdown
    assert crashed_game(boot(prev_clean=None), "b1") is None  # journal unknown
    assert crashed_game(boot(), None) is None  # never seen this Frame boot before: nothing to compare
    assert crashed_game(boot(), "b2") is None  # same boot
    assert crashed_game(boot(boot_time=1000 + 4 * 3600), "b1") is None  # the game ran hours before
    assert crashed_game(boot(last_launch=None), "b1") is None
