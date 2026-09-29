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

from ..core.paths import agent_dir, ssh_dir, user_data_dir

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


def saved_targets() -> list[FrameTarget]:
    path = user_data_dir() / "frames.json"
    try:
        return [FrameTarget(**d) for d in json.loads(path.read_text())]
    except (OSError, ValueError, TypeError):
        return []


def save_target(target: FrameTarget) -> None:
    items = [t for t in saved_targets() if t.host != target.host]
    items.insert(0, target)
    (user_data_dir() / "frames.json").write_text(json.dumps([t.__dict__ for t in items], indent=2))


class AgentFailed(RuntimeError):
    pass


@dataclass
class Frame:
    target: FrameTarget
    password: str | None = None
    client: paramiko.SSHClient | None = None
    _sftp: paramiko.SFTPClient | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    home: str = ""

    # ------------------------------------------------------------------ connect
    def connect(self, timeout: float = 10) -> "Frame":
        client = paramiko.SSHClient()
        known = ssh_dir() / "known_hosts"
        known.touch(exist_ok=True)
        client.load_host_keys(str(known))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # trust on first use (pairing)
        kwargs = dict(hostname=self.target.host, port=self.target.port, username=self.target.user, timeout=timeout,
                      banner_timeout=timeout, auth_timeout=timeout)
        errors = []
        for attempt in ("app_key", "agent", "password"):
            try:
                if attempt == "app_key":
                    client.connect(pkey=app_key(), allow_agent=False, look_for_keys=False, **kwargs)
                elif attempt == "agent":
                    client.connect(allow_agent=True, look_for_keys=True, **kwargs)
                elif self.password:
                    client.connect(password=self.password, allow_agent=False, look_for_keys=False, **kwargs)
                else:
                    continue
                break
            except paramiko.AuthenticationException as exc:
                errors.append(f"{attempt}: {exc}")
        else:
            raise ConnectionError("SSH authentication failed (" + "; ".join(errors) + "). Run the FramePort "
                                  "bootstrap on the Frame or enter the steamos password.")
        client.save_host_keys(str(known))
        transport = client.get_transport()
        transport.set_keepalive(15)
        self.client = client
        self.home = self.run("printf %s \"$HOME\"")[1].strip() or f"/home/{self.target.user}"
        return self

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self.client:
            self.client.close()
        self.client = self._sftp = None

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            self._sftp = self.client.open_sftp()
        return self._sftp

    def run(self, command: str, stdin: str | None = None, timeout: float | None = None) -> tuple[int, str, str]:
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
        code, out, _ = self.run(f"sha256sum {sh_quote(remote)} 2>/dev/null | cut -c1-16")
        if out.strip() != digest:
            self.run(f"mkdir -p {sh_quote(remote_dir)}")
            with self.sftp.open(remote + ".tmp", "wb") as f:
                f.write(text)
            self.run(f"mv {sh_quote(remote + '.tmp')} {sh_quote(remote)} && chmod 755 {sh_quote(remote)}")
        return remote

    def agent(self, command: str, timeout: float | None = 600, **args):
        remote = self.ensure_agent()
        code, out, err = self.run(f"python3 {sh_quote(remote)} {command}", stdin=json.dumps(args), timeout=timeout)
        line = next((l for l in reversed(out.splitlines()) if l.startswith("{")), "")
        try:
            reply = json.loads(line)
        except ValueError:
            raise AgentFailed(f"agent {command}: no reply (exit {code}) {err[-400:]}") from None
        if not reply.get("ok"):
            raise AgentFailed(reply.get("error", "unknown agent error"))
        return reply["result"]

    # ------------------------------------------------------------------ files
    def put(self, local: Path, remote: str, progress=None, resume: bool = True) -> None:
        """Upload with resume: data goes to <remote>.part, appended from the existing size, then renamed."""
        local = Path(local)
        size = local.stat().st_size
        part = remote + ".part"
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
