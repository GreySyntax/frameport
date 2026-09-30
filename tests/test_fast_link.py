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
