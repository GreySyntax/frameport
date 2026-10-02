"""Pairing server: while the Setup page is open, serve the bootstrap script and FramePort's public key over HTTP on
the LAN. The user types one line on the Frame; the script calls back /paired so the UI knows who connected.

Every request needs the pairing code, a random secret that is only in that one line. The server stops after a
successful pairing, after MAX_FAILURES wrong codes (someone guessing) and after LIFETIME seconds."""
from __future__ import annotations

import http.server
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field

from ..core.paths import bootstrap_dir
from .connection import app_public_key
from .discovery import local_ip_towards

MAX_FAILURES = 20
LIFETIME = 30 * 60


@dataclass
class PairingServer:
    port: int = 0
    code: str = field(default_factory=lambda: secrets.token_hex(8))
    failures: int = 0
    paired: list[dict] = field(default_factory=list)
    on_paired: object = None
    _httpd: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{local_ip_towards()}:{self.port}"

    @property
    def one_liner(self) -> str:
        return f"curl -fsSL {self.url}/bootstrap.sh?code={self.code} | bash"

    def start(self) -> PairingServer:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body: bytes, ctype="text/plain", status=200):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(url.query))
                if not secrets.compare_digest(q.get("code", ""), server.code):
                    server.failures += 1
                    if server.failures >= MAX_FAILURES:
                        server.stop_soon()
                    return self._send(b"wrong or missing pairing code\n", status=403)
                if url.path == "/bootstrap.sh":
                    text = (bootstrap_dir() / "bootstrap.sh").read_text()
                    text = text.replace("__PC_URL__", server.url).replace("__PAIR_CODE__", server.code)
                    return self._send(text.encode(), "text/x-shellscript")
                if url.path == "/key":
                    return self._send((app_public_key() + "\n").encode())
                if url.path == "/paired":
                    info = {"host": self.client_address[0], "user": q.get("user", "steamos"), "name": q.get("host", "")}
                    server.paired.append(info)
                    if callable(server.on_paired):
                        server.on_paired(info)
                    server.stop_soon()  # paired: nothing else to serve
                    return self._send(b"ok\n")
                return self._send(b"not found\n", status=404)

        self._httpd = http.server.ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self._timer = threading.Timer(LIFETIME, self.stop)
        self._timer.daemon = True
        self._timer.start()
        return self

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def stop_soon(self) -> None:
        """Stop from inside a request handler (shutdown() waits for the serving thread, so not from that thread)."""
        threading.Thread(target=self.stop, daemon=True).start()

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        timer = getattr(self, "_timer", None)
        if timer:
            timer.cancel()
