"""Frame.fast_link: bulk uploads go over a direct link (USB / the Frame's hotspot) only when it is reachable and
presents the same SSH host key as the paired connection."""
import socket
from types import SimpleNamespace

from frameport.frame import connection as conn


def fake_frame(host, key):
    f = conn.Frame(conn.FrameTarget(host, "steamos", 22))
    f.client = SimpleNamespace(get_transport=lambda: SimpleNamespace(get_remote_server_key=lambda: key))
    return f


def test_fast_link_prefers_usb_then_hotspot_and_checks_host_key(monkeypatch):
    main = fake_frame("192.168.1.5", "KEY")
    monkeypatch.setattr(main, "addresses", lambda: [("lo", "127.0.0.1"), ("wlan0", "192.168.1.5"),
                                                    ("wlanap", "10.35.78.1"), ("usb0", "10.86.200.233")])
    keys = {"10.86.200.233": "OTHER", "10.35.78.1": "KEY"}  # something else answers on the USB address
    reachable = set(keys)
    monkeypatch.setattr(socket, "create_connection",
                        lambda addr, timeout: SimpleNamespace(close=lambda: None) if addr[0] in reachable
                        else (_ for _ in ()).throw(OSError("unreachable")))

    def connect(self, timeout=10):
        self.client = SimpleNamespace(get_transport=lambda: SimpleNamespace(
            get_remote_server_key=lambda: keys[self.target.host]), close=lambda: None)
        return self
    monkeypatch.setattr(conn.Frame, "connect", connect)
    link, label = main.fast_link()
    assert link.target.host == "10.35.78.1" and "hotspot" in label  # the USB impostor was rejected

    reachable.clear()  # PC not on any direct link: the normal connection
    link, label = main.fast_link()
    assert link is main and label == ""


def test_sftp_channels_are_shared_and_bounded():
    """Many threads share at most SFTP_CHANNELS channels (one per thread, never closed, ran the SSH connection out of
    channels: ChannelException(2, 'Connect failed')); nested calls reuse the thread's channel; open files hold one."""
    import threading
    import time
    from types import SimpleNamespace

    from frameport.frame.connection import SFTP_CHANNELS, SftpPool, SftpProxy

    opened, busy, peak = [], set(), [0]
    lock = threading.Lock()

    class Client:
        def __init__(self):
            self.sock = SimpleNamespace(closed=False)

        def stat(self, path):
            with lock:
                assert self not in busy, "one channel used by two threads at once"
                busy.add(self)
                peak[0] = max(peak[0], len(busy))
            time.sleep(0.005)
            with lock:
                busy.discard(self)
            return path

        def open(self, path, mode="rb"):
            return SimpleNamespace(read=lambda: b"x", close=lambda: None)

        def close(self):
            self.sock.closed = True

    pool = SftpPool(lambda: opened.append(Client()) or opened[-1])
    sftp = SftpProxy(pool)
    threads = [threading.Thread(target=lambda: [sftp.stat("p") for _ in range(5)]) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(opened) <= SFTP_CHANNELS and peak[0] <= SFTP_CHANNELS
    with sftp.open("f") as f:  # holds a channel; a nested call on this thread reuses it
        before = len(pool._free)
        assert sftp.stat("q") == "q" and f.read() == b"x" and len(pool._free) == before
    assert len(pool._free) == len(pool._all)  # everything returned
    pool.close()
    assert all(c.sock.closed for c in opened)
