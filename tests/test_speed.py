from frameport.install.installer import Speed


def test_speed_window_and_eta():
    t = [0.0]
    s = Speed(total=1000 * 10**6, window=5.0, clock=lambda: t[0])
    assert s.text(0) == ""
    t[0] = 0.5
    assert s.text(10 * 10**6) == ""  # under a second of samples
    t[0] = 2.0
    assert s.text(100 * 10**6) == "50.0 MB/s · ~18 s left"
    for i in range(3, 20):  # the rate follows the last few seconds, not the average since the start
        t[0] = float(i)
        s.text(100 * 10**6 + (i - 2) * 10 * 10**6)
    assert s.text(100 * 10**6 + 17 * 10 * 10**6).startswith("10.0 MB/s · ~")
    assert Speed(10**12, clock=lambda: 0.0).text(5) == ""
