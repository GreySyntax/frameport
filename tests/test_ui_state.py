from frameport.ui.app import install_state


def game(sha=None, alt=None):
    return {"package": "com.x", "build": {"sha256": sha, "alt_sha256": alt} if sha else {}}


def frame(*deps):
    return {"installed": list(deps)}


def test_not_connected():
    assert install_state(game("a"), None) is None


def test_missing():
    assert install_state(game("a"), frame({"package": "com.other", "sha256": "a"})) == "missing"


def test_installed_same_build_or_alt():
    assert install_state(game("a", "b"), frame({"package": "com.x", "sha256": "a"})) == "installed"
    assert install_state(game("a", "b"), frame({"package": "com.x", "sha256": "b"})) == "installed"


def test_installed_without_local_build():
    assert install_state(game(), frame({"package": "com.x", "sha256": "z"})) == "installed"


def test_outdated():
    assert install_state(game("a"), frame({"package": "com.x", "sha256": "old"})) == "outdated"


# ------------------------------------------------------------------------------------------ library filters / tags
def lib_games():
    return [
        {"package": "com.a", "title": "Alpha", "added": 1, "recipe": {"status": "works", "patches": {}},
         "analysis": {"engine": "Unity", "xr": "OpenXR"}, "tags": ["Favorite"], "data_bytes": 5},
        {"package": "rift.b", "kind": "rift", "title": "Bravo", "added": 3, "recipe": {"status": "unknown"},
         "analysis": {"engine": "Unreal", "xr": "LibOVR", "extra": {"data_bytes": 50}},
         "last_test": {"time": 100}},
        {"package": "com.c", "title": "charlie", "added": 2, "recipe": {"status": "issues",
                                                                        "patches": {"patch_force_passthrough": {}}},
         "analysis": {"engine": "Unreal", "xr": "VrApi"}, "installs": {"frame": {"time": 200}}, "data_bytes": 20},
    ]


def test_filters_and_sort():
    from frameport.ui.views.library import DEFAULT_FILTERS, filter_games

    games = lib_games()
    f = dict(DEFAULT_FILTERS)
    names = lambda fl, **kw: [g["title"] for g in filter_games(games, {**f, **fl}, **kw)]  # noqa: E731
    assert names({}) == ["Alpha", "Bravo", "charlie"]
    assert names({"platform": "pcvr"}) == ["Bravo"] and names({"platform": "quest"}) == ["Alpha", "charlie"]
    assert names({"status": "issues"}) == ["charlie"]
    assert names({"q": "unreal"}) == ["Bravo", "charlie"]  # search covers tags
    assert names({"tags": ["favorite"]}) == ["Alpha"] and names({"tags": ["Unreal", "Mixed reality"]}) == ["charlie"]
    assert names({"sort": "recent"}) == ["Bravo", "charlie", "Alpha"]
    assert names({"sort": "played"}) == ["charlie", "Bravo", "Alpha"]
    assert names({"sort": "size"}) == ["Bravo", "charlie", "Alpha"]
    assert names({"sort": "status"}) == ["Alpha", "charlie", "Bravo"]
    frame = {"installed": [{"package": "com.c"}]}
    assert names({"where": "frame"}, frame_info=frame) == ["charlie"]
    assert names({"where": "pc"}, pc_installs={"rift.b"}) == ["Bravo"]
    assert names({"where": "none"}, frame_info=frame, pc_installs={"rift.b"}) == ["Alpha"]


def test_tags():
    from frameport.ui.views.library import all_tags, auto_tags, game_tags, normalize_tag

    a, b, c = lib_games()
    assert auto_tags(b) == ["PC VR", "Unreal", "LibOVR"]
    assert "Mixed reality" in auto_tags(c)
    assert game_tags(a)[0] == "Favorite"
    assert all_tags([a, b, c])[0] == "Favorite"  # the user's own tags first
    assert normalize_tag("  my,  tag ") == "my tag"


# ------------------------------------------------------------------------------------------ job queue
def test_jobs_run_in_order_one_at_a_time():
    import threading
    import time

    from frameport.ui.jobs import Job, JobManager

    events, running = [], []
    lock = threading.Lock()

    def work(name):
        def run(job):
            with lock:
                running.append(name)
                assert len(running) == 1, "two jobs ran at once"
            job.reporter.stage(f"{name} stage")
            job.reporter.check("thing", True, "ok")
            time.sleep(0.05)
            with lock:
                running.remove(name)
            events.append(name)
            return name
        return run
    m = JobManager(throttle=0)
    jobs = [m.submit(Job(n, work(n), package="p" if n == "a" else None)) for n in "abc"]
    assert m.wait_idle(5)
    assert events == ["a", "b", "c"] and [j.state for j in jobs] == ["done"] * 3
    assert jobs[0].stages == ["a stage"] and jobs[0].checks[0]["ok"] is True and jobs[0].result == "a"
    assert m.busy_with("p") is None


def test_job_cancel_and_failure():
    import time

    from frameport.ui.jobs import Job, JobManager

    def slow(job):
        for _ in range(100):
            job.reporter.check_cancel()
            time.sleep(0.02)

    def boom(job):
        raise RuntimeError("no Frame")
    m = JobManager(throttle=0)
    first = m.submit(Job("slow", slow, package="x"))
    queued = m.submit(Job("later", slow))
    failing = m.submit(Job("boom", boom))
    time.sleep(0.1)
    assert m.busy_with("x") is first and first.state == "running"
    m.cancel(queued)  # still queued: never runs
    m.cancel(first)
    assert m.wait_idle(5)
    assert (first.state, queued.state, failing.state) == ("cancelled", "cancelled", "failed")
    assert failing.error == "no Frame"
    m.clear_finished()
    assert m.jobs == []


def test_files_tree():
    from frameport.ui.views import files_dialog as fd

    files = [["game/Bin/Game.exe", 4], ["game/Bin/Data.pak", 10], ["launch.sh", 1], ["game/Readme.txt", 2]]
    root = fd.build_tree("Install folder", files)
    assert (root.size, root.files) == (17, 4)
    game = root.children["game"]
    assert (game.size, game.files) == (16, 3)
    # folders first, then files; only expanded folders show their children
    assert [(d, n.name) for d, n in fd.visible_rows(root, set())] == [(0, "game"), (0, "launch.sh")]
    rows = fd.visible_rows(root, {"game", "game/Bin"})
    assert [(d, n.name) for d, n in rows] == [(0, "game"), (1, "Bin"), (2, "Data.pak"), (2, "Game.exe"),
                                              (1, "Readme.txt"), (0, "launch.sh")]
    assert fd.matches(files, ".PAK") == [["game/Bin/Data.pak", 10]]
    assert fd.human(3 * 2**30) == "3.0 GiB" and fd.human(512) == "512 B"


def test_files_tree_caps_children():
    from frameport.ui.views import files_dialog as fd

    root = fd.build_tree("x", [[f"f{i:04}", 1] for i in range(fd.MAX_CHILDREN + 5)])
    rows = fd.visible_rows(root, set())
    assert len(rows) == fd.MAX_CHILDREN + 1 and rows[-1] == (0, 5)


def test_install_state_tracks_patch_settings():
    from frameport.ui import components as C

    g = {"package": "rift.g", "kind": "rift", "recipe": {"patches": {"pcvr.revive": {}, "pcvr.xr_timefix": {}}}}
    fi = {"installed": [{"package": "rift.g", "recipe": {"patches": ["pcvr.revive", "pcvr.xr_timefix"]}}]}
    assert C.install_state(g, fi) == "installed" and C.settings_diff(g, fi) is None
    g["recipe"]["patches"].pop("pcvr.revive")
    g["recipe"]["patches"]["pcvr.no_crash_reporter"] = {}
    assert C.install_state(g, fi) == "outdated"
    assert C.settings_diff(g, fi) == (["pcvr.no_crash_reporter"], ["pcvr.revive"])
    old = {"installed": [{"package": "rift.g"}]}  # installed before recipes were recorded: no false alarm
    assert C.install_state(g, old) == "installed"
    assert C.install_state(g, {"installed": []}) == "missing"


def test_library_writes_from_many_threads_are_not_lost():
    """Every thread's change survives (library.json read-modify-write used to race between jobs and the UI)."""
    import threading

    from frameport.core import library

    def work(i):
        for j in range(20):
            library.upsert_game(f"pkg.{i}", n=j)
            library.update_setting("counter", lambda v: (v or 0) + 1)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {g["package"] for g in library.games() if g["package"].startswith("pkg.")} == {f"pkg.{i}" for i in range(6)}
    assert library.setting("counter") == 120


def test_refresh_keeps_a_working_connection_when_a_status_query_fails():
    """An agent error during the background refresh must not close the connection a running job is using."""
    from types import SimpleNamespace

    from frameport.frame.connection import AgentFailed
    from frameport.ui.app import FramePortApp

    closed = []

    class Target:
        def __init__(self, alive):
            self.frame = SimpleNamespace(alive=lambda: alive)

        def describe(self):
            raise AgentFailed("agent info: boom")

        def close(self):
            closed.append(self)

    def app_with(target):
        app = object.__new__(FramePortApp)
        app.target, app.frame_state, app.frame_info = target, "connected", {"installed": []}
        app.route = ("settings",)
        app._refresh_sidebar = lambda *a, **k: None
        app.toast = lambda *a, **k: None
        return app

    up = app_with(Target(alive=True))
    up.refresh_frame(quiet=True, background=False)
    assert up.frame_state == "connected" and not closed
    down = app_with(Target(alive=False))
    down.refresh_frame(quiet=True, background=False)
    assert down.frame_state == "offline" and closed == [down.target]


def test_pairing_server_needs_the_code_and_stops_after_pairing_or_guessing(monkeypatch):
    import time
    import urllib.error
    import urllib.request

    from frameport.frame import pairing

    monkeypatch.setattr(pairing, "local_ip_towards", lambda *a: "127.0.0.1")
    monkeypatch.setattr(pairing, "MAX_FAILURES", 3)

    def get(server, path):
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code
        except OSError:
            return None

    seen = []
    s = pairing.PairingServer(on_paired=seen.append).start()
    assert len(s.code) == 16
    assert get(s, "/key?code=000000") == 403
    assert get(s, f"/paired?code={s.code}&user=steamos&host=frame") == 200 and seen
    for _ in range(50):
        if not s.running:
            break
        time.sleep(0.05)
    assert not s.running
    g = pairing.PairingServer().start()
    for _ in range(3):
        get(g, "/key?code=guess")
    for _ in range(50):
        if not g.running:
            break
        time.sleep(0.05)
    assert not g.running
