import json

from frameport.core import steamvr_perf as sp

LOG = """Wed Sep 30 2026 18:10:53.396 [Info] - External connection from D:\\Games\\Other\\Other.exe 111
Wed Sep 30 2026 18:11:00.000 [Info] - Total..................  900 presents.  300 dropped.    0 reprojected
Wed Sep 30 2026 18:11:00.000 [Info] - Timed out. 50 total....   60 presents.   19 dropped.    0 reprojected
Wed Sep 30 2026 18:35:40.351 [Info] - External connection from D:\\Games\\Stormland\\Stormland.exe 53752
Wed Sep 30 2026 18:37:50.614 [Info] - Lost pipe connection from Stormland (53752)
Wed Sep 30 2026 18:37:50.614 [Info] - Total.................. 11864 presents.    0 dropped.    0 reprojected
Wed Sep 30 2026 18:37:50.614 [Info] - Startup................    26 presents.    0 dropped.    0 reprojected
Wed Sep 30 2026 18:37:50.614 [Info] - Timed out.313 total....   417 presents.    0 dropped.    0 reprojected
"""
RATES = (72.0, 80.0, 90.0, 96.0, 108.0, 120.0, 144.0)


def test_compositor_session_counts_timeouts():
    s = sp.compositor_session(LOG, "stormland.exe")
    assert (s.presents, s.dropped, s.timed_out) == (11864, 0, 313)
    assert abs(s.drop_ratio - 313 / 11864) < 1e-9
    assert sp.compositor_session(LOG, "Missing.exe") is None


def test_recommend_fits_frame_budget():
    s = sp.Session(11864, 0, 0, timed_out=313, hz=96.0, frame_ms_p99=10.7)
    r = sp.recommend(s, RATES, 96.0)
    assert r["refresh"] == 80.0 and r["smoothing"] == sp.SMOOTHING_FORCE_ON  # 90 Hz = 11.1 ms < 10.7 ms + 5 %
    s.frame_ms_p99 = 9.0
    assert sp.recommend(s, RATES, 96.0)["refresh"] == 90.0  # one step down at least, and 90 Hz fits 9.45 ms
    s.frame_ms_p99 = None
    assert sp.recommend(s, RATES, 96.0)["refresh"] == 90.0  # no frame times: one step down
    assert sp.recommend(sp.Session(1000, 5, 0, timed_out=4), RATES, 96.0) is None  # < 1 %: leave it alone
    assert sp.recommend(None, RATES, 96.0) is None


def test_fpsvr_session(tmp_path):
    hist = [0] * 120
    hist[55], hist[107] = 99, 1  # 0.1 ms bins
    (tmp_path / "0.json").write_text(json.dumps({"AppKey": "steam.app.1", "hz": 96.0, "gputimes": hist,
                                                 "cputimes": [0] * 10 + [100]}))
    (tmp_path / "1.json").write_text(json.dumps({"AppKey": "steam.app.2", "hz": 72.0}))
    f = sp.fpsvr_session("steam.app.1", tmp_path)
    assert f["hz"] == 96.0 and f["frame_ms_p99"] == 5.5
    assert sp.fpsvr_session("steam.app.3", tmp_path) is None
    (tmp_path / "2.json").write_text(json.dumps({"AppKey": "steam.app.9", "app": "Stormland", "hz": 90.0}))
    assert sp.fpsvr_session("steam.app.new", tmp_path, app_name="stormland")["hz"] == 90.0  # shortcut id changed


def test_available_rates(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/vrserver.txt").write_text(
        "x - vrlink: \t72.000000 Hz\nx - vrlink: \t90.000008 Hz\n"
        "x - vrlink: SendUpdatedFramerateRequest: Best client match 90.00 Hz (host preferred 90.00 Hz)\n")
    assert sp.available_rates(tmp_path) == (72.0, 90.0) and sp.current_rate(tmp_path) == 90.0
    assert sp.available_rates(tmp_path / "none") == sp.COMMON_RATES  # any headset: common rates


def test_current_rate_without_steam_link(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/steamvr.vrsettings").write_text('{"steamvr": {"preferredRefreshRate": 120}}')
    assert sp.current_rate(tmp_path) == 120.0
    assert sp.current_rate(tmp_path / "none") is None
    s = sp.Session(1000, 50, 0)  # no fpsVR: one common step below the current rate
    assert sp.recommend(s, sp.COMMON_RATES, 120.0)["refresh"] == 108.0
