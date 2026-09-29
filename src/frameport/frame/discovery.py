"""Find Steam Frames on the local network (mDNS/zeroconf).

The bootstrap script publishes `_frameport._tcp` via avahi (with the pairing code in its TXT record). SteamOS also
advertises `_ssh._tcp` / `<hostname>.local` when sshd runs, so un-bootstrapped Frames with SSH on are found too.
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field


@dataclass
class Found:
    name: str
    host: str
    port: int = 22
    service: str = ""
    properties: dict = field(default_factory=dict)

    @property
    def is_frameport(self) -> bool:
        return self.service.startswith("_frameport")


def browse(seconds: float = 4.0) -> list[Found]:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    found: dict[str, Found] = {}

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if not info:
                return
            addrs = info.parsed_addresses()
            ipv4 = [a for a in addrs if ":" not in a]
            host = (ipv4 or addrs or [info.server])[0]
            props = {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in (info.properties or {}).items()}
            key = host
            if key in found and found[key].is_frameport:
                return
            found[key] = Found(name.split(".")[0], host, info.port or 22, type_.split(".")[0], props)

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    try:
        listener = Listener()
        ServiceBrowser(zc, ["_frameport._tcp.local.", "_ssh._tcp.local."], listener)
        time.sleep(seconds)
    finally:
        zc.close()
    # SteamOS devices only (the Frame's default hostname is "frame"; bootstrapped ones say so explicitly)
    out = [f for f in found.values() if f.is_frameport or "frame" in f.name.lower() or "steamdeck" in f.name.lower()]
    return sorted(out, key=lambda f: (not f.is_frameport, f.name))


def resolve_hostname(name: str = "frame.local") -> str | None:
    try:
        return socket.gethostbyname(name)
    except OSError:
        return None


def local_ip_towards(host: str = "8.8.8.8") -> str:
    """The PC's LAN address (used in the bootstrap one-liner)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
