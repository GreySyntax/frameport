"""SSH connection to a Steam Frame and the remote agent protocol.

Authentication: FramePort's own key (<user data>/ssh/id_ed25519, installed by the bootstrap script) first, then
the user's SSH agent/keys, then a password if given.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paramiko

from ..core.paths import agent_dir, ssh_dir, user_data_dir, write_atomic

REMOTE_AGENT_DIR = ".local/share/frameport/agent"


def app_key() -> paramiko.Ed25519Key:
    path = ssh_dir() / "id_ed25519"
    if not path.exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                           serialization.NoEncryption()))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        (ssh_dir() / "id_ed25519.pub").write_text(pub.decode() + " frameport\n")
    return paramiko.Ed25519Key.from_private_key_file(str(path))


def app_public_key() -> str:
    app_key()
    return (ssh_dir() / "id_ed25519.pub").read_text().strip()


@dataclass
class FrameTarget:
    host: str
    user: str = "steamos"
    port: int = 22
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.host


_saved_cache: tuple[float, list] = (-1.0, [])


def saved_targets() -> list[FrameTarget]:
    """The remembered Frames (read again only when frames.json changed: the sidebar asks several times a second)."""
    global _saved_cache
    path = user_data_dir() / "frames.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if mtime != _saved_cache[0]:
        try:
            _saved_cache = (mtime, [d for d in json.loads(path.read_text()) if isinstance(d, dict)])
        except (OSError, ValueError):
            return []
    try:
        return [FrameTarget(**d) for d in _saved_cache[1]]
    except TypeError:
        return []


def save_target(target: FrameTarget) -> None:
    items = [t for t in saved_targets() if t.host != target.host]
    items.insert(0, target)
    write_atomic(user_data_dir() / "frames.json", json.dumps([t.__dict__ for t in items], indent=2))


# direct links to the Frame, fastest first (interface → what it is)
FAST_LINKS = {"usb0": "USB cable", "wlanap": "the Frame's own Wi-Fi hotspot"}


def bundled_agent_version() -> int | None:
    """AGENT_VERSION of the agent this app ships (it's uploaded to the Frame whenever it differs)."""
    try:
        m = re.search(r"^AGENT_VERSION = (\d+)", (agent_dir() / "frameport_agent.py").read_text(), re.M)
        return int(m.group(1)) if m else None
    except OSError:
        return None


RUN_TIMEOUT = 120  # seconds: default bound for one remote command's output


class AgentFailed(RuntimeError):
    pass


@dataclass
class Frame:
    target: FrameTarget
    password: str | None = None
    client: paramiko.SSHClient | None = None
    _sftp: threading.local = field(default_factory=threading.local)  # one SFTP channel per thread (see sftp)
    _sftps: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _agent_digest: str = ""  # the agent version known to be on the Frame (checked once per connection)
    home: str = ""

    # ------------------------------------------------------------------ connect
    def connect(self, timeout: float = 10) -> Frame:
        known = ssh_dir() / "known_hosts"
        known.touch(exist_ok=True)
        kwargs = dict(hostname=self.target.host, port=self.target.port, username=self.target.user, timeout=timeout,
                      banner_timeout=timeout, auth_timeout=timeout)
        errors = []
        for attempt in ("app_key", "agent", "password"):
            if attempt == "password" and not self.password:
                continue
            client = paramiko.SSHClient()  # a fresh client per attempt: a failed one keeps its transport otherwise
            client.load_host_keys(str(known))
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # trust on first use (pairing)
            try:
                if attempt == "app_key":
                    client.connect(pkey=app_key(), allow_agent=False, look_for_keys=False, **kwargs)
                elif attempt == "agent":
                    client.connect(allow_agent=True, look_for_keys=True, **kwargs)
                else:
                    client.connect(password=self.password, allow_agent=False, look_for_keys=False, **kwargs)
                break
            except paramiko.AuthenticationException as exc:
                client.close()
                errors.append(f"{attempt}: {exc}")
            except BaseException:
                client.close()
                raise
        else:
            raise ConnectionError("SSH authentication failed (" + "; ".join(errors) + "). Run the FramePort "
                                  "bootstrap on the Frame or enter the steamos password.")
        client.save_host_keys(str(known))
        transport = client.get_transport()
        transport.set_keepalive(15)
        self.client = client
        self.home = self.run("printf %s \"$HOME\"")[1].strip() or f"/home/{self.target.user}"
        return self

    def addresses(self) -> list[tuple[str, str]]:
        """(interface, IPv4) of every link the Frame has up."""
        out = self.run("ip -4 -o addr show up 2>/dev/null")[1]
        return re.findall(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/", out, re.M)

    def fast_link(self, timeout: float = 1.5) -> tuple[Frame, str]:
        """A second connection over the fastest direct link, for bulk uploads: the USB cable (usb0) or the Frame's own
        hotspot (wlanap; the PC joins it with a Wi-Fi adapter, e.g. Valve's USB dongle) — several times faster than
        both going through the home router (measured 83-97 vs 15-18 MB/s). Used only if this PC can reach it and it
        presents the same SSH host key as this connection. Returns (frame, link name); (self, "") if none."""
        import socket

        key = self.client.get_transport().get_remote_server_key()
        for iface in FAST_LINKS:
            for name, ip in self.addresses():
                if name != iface or ip == self.target.host:
                    continue
                try:
                    socket.create_connection((ip, self.target.port), timeout=timeout).close()
                    other = Frame(FrameTarget(ip, self.target.user, self.target.port), self.password)
                    other.connect(timeout=5)
                except (OSError, ConnectionError, paramiko.SSHException):
                    continue
                if other.client.get_transport().get_remote_server_key() != key:  # not our Frame: don't use it
                    other.close()
                    continue
                return other, f"{FAST_LINKS[iface]} ({ip})"
        return self, ""

    def close(self) -> None:
        with self._lock:
            sftps, self._sftps = self._sftps, []
        for s in sftps:
            try:
                s.close()
            except Exception:  # noqa: BLE001 - closing anyway
                pass
        if self.client:
            self.client.close()
        self.client = None
        self._sftp = threading.local()
        self._agent_digest = ""

    def alive(self) -> bool:
        """The SSH connection itself still works (a failed agent command doesn't mean the Frame is gone)."""
        transport = self.client.get_transport() if self.client else None
        return bool(transport and transport.is_active())

    @property
    def sftp(self) -> paramiko.SFTPClient:
        """This thread's SFTP channel. paramiko's SFTPClient must not be shared between threads (replies get mixed up);
        channels on one SSH connection can run side by side."""
        client = getattr(self._sftp, "client", None)
        if client is None or client.sock.closed:
            client = self.client.open_sftp()
            self._sftp.client = client
            with self._lock:
                self._sftps.append(client)
        return client

    def run(self, command: str, stdin: str | None = None,
            timeout: float | None = RUN_TIMEOUT) -> tuple[int, str, str]:
        """Run a shell command. `timeout` (s) bounds waiting for output, so a dead link can't hang the caller;
        None only for commands that legitimately run long (installs, launch tests)."""
        chan_in, out, err = self.client.exec_command(command, timeout=timeout)
        if stdin is not None:
            chan_in.write(stdin)
            chan_in.channel.shutdown_write()
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        return out.channel.recv_exit_status(), o, e

    def install_key(self) -> None:
        """After a password login: add FramePort's public key to authorized_keys."""
        pub = app_public_key()
        self.run("mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
                 f"grep -qxF {sh_quote(pub)} ~/.ssh/authorized_keys || echo {sh_quote(pub)} >> ~/.ssh/authorized_keys"
                 " && chmod 600 ~/.ssh/authorized_keys")

    # ------------------------------------------------------------------ agent
    def ensure_agent(self) -> str:
        local = agent_dir() / "frameport_agent.py"
        text = local.read_bytes()
        digest = hashlib.sha256(text).hexdigest()[:16]
        remote_dir = posixpath.join(self.home, REMOTE_AGENT_DIR)
        remote = posixpath.join(remote_dir, "frameport_agent.py")
        if self._agent_digest == digest:
            return remote
        code, out, _ = self.run(f"sha256sum {sh_quote(remote)} 2>/dev/null | cut -c1-16")
        if out.strip() != digest:
            self.run(f"mkdir -p {sh_quote(remote_dir)}")
            tmp = f"{remote}.{os.getpid()}.{threading.get_ident()}.tmp"  # two threads never share a temp file
            with self.sftp.open(tmp, "wb") as f:
                f.write(text)
            code, _, err = self.run(f"mv {sh_quote(tmp)} {sh_quote(remote)} && chmod 755 {sh_quote(remote)}")
            if code:
                raise AgentFailed(f"couldn't install the FramePort agent on the Frame: {err.strip()[-300:]}")
        self._agent_digest = digest
        return remote

    def agent(self, command: str, timeout: float | None = 600, ensure: bool = True, **args):
        """Run an agent command. ensure=False uses the agent already on the Frame (never re-uploads it)."""
        remote = self.ensure_agent() if ensure else posixpath.join(self.home, REMOTE_AGENT_DIR, "frameport_agent.py")
        code, out, err = self.run(f"python3 {sh_quote(remote)} {command}", stdin=json.dumps(args), timeout=timeout)
        line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("{")), "")
        try:
            reply = json.loads(line)
        except ValueError:
            raise AgentFailed(f"agent {command}: no reply (exit {code}) {err[-400:]}") from None
        if not reply.get("ok"):
            raise AgentFailed(reply.get("error", "unknown agent error"))
        return reply["result"]

    # ------------------------------------------------------------------ files
    def mkdirs(self, dirs) -> None:
        """Create many remote folders with one command."""
        dirs = sorted(set(dirs))
        if dirs:
            self.run("xargs -0 mkdir -p", stdin="\0".join(dirs))

    def put(self, local: Path, remote: str, progress=None, resume: bool = True, mkdir: bool = True) -> None:
        """Upload with resume: data goes to <remote>.part, appended from the existing size, then renamed. progress may
        raise (e.g. Cancelled): the .part stays and the next put continues from there."""
        local = Path(local)
        size = local.stat().st_size
        part = remote + ".part"
        if mkdir:
            self.run(f"mkdir -p {sh_quote(posixpath.dirname(remote))}")
        offset = 0
        if resume:
            try:
                offset = self.sftp.stat(part).st_size
                if offset > size:
                    offset = 0
            except OSError:
                offset = 0
        with open(local, "rb") as src, self.sftp.open(part, "ab" if offset else "wb") as dst:
            dst.set_pipelined(True)
            src.seek(offset)
            done = offset
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, size)
        self.run(f"mv -f {sh_quote(part)} {sh_quote(remote)}")

    def put_tar(self, files: list[tuple[Path, str]], remote_root: str, progress=None) -> None:
        """Upload many small files in one stream (tar → `tar -x` on the Frame): far faster than one SFTP transfer per
        file. files: (local path, path relative to remote_root). progress(done_bytes, rel) may raise to stop; files that
        arrived stay (a cut-off last file has the wrong size, so it's sent again next time)."""
        import tarfile

        stdin, stdout, stderr = self.client.exec_command(
            f"mkdir -p {sh_quote(remote_root)} && tar -xf - -C {sh_quote(remote_root)}")
        done = 0
        try:
            with tarfile.open(fileobj=stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for local, rel in files:
                    tar.add(str(local), arcname=rel, recursive=False)
                    done += Path(local).stat().st_size
                    if progress:
                        progress(done, rel)
        finally:
            try:
                stdin.channel.shutdown_write()
            except Exception:  # noqa: BLE001
                pass
            code = stdout.channel.recv_exit_status()
        if code:
            raise OSError(f"remote tar failed ({code}): {stderr.read().decode(errors='replace')[-300:]}")

    def get_text(self, remote: str, max_bytes: int = 64 << 20) -> str:
        with self.sftp.open(remote, "rb") as f:
            size = f.stat().st_size
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", "replace")

    def exists(self, remote: str) -> bool:
        try:
            self.sftp.stat(remote)
            return True
        except OSError:
            return False

    def is_dir(self, remote: str) -> bool:
        try:
            return stat.S_ISDIR(self.sftp.stat(remote).st_mode)
        except OSError:
            return False


def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def wait_for(predicate, timeout: float, interval: float = 2.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def parse_target(text: str) -> FrameTarget:
    m = re.fullmatch(r"(?:(?P<user>[^@]+)@)?(?P<host>[^:]+)(?::(?P<port>\d+))?", text.strip())
    if not m:
        raise ValueError(f"bad Frame address {text!r}")
    return FrameTarget(m["host"], m["user"] or "steamos", int(m["port"] or 22))
