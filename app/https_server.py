"""HTTPS listener that runs next to the plain HTTP listener in the same process.

The HTTP server (``WEB_PORT``) always stays available, so a broken certificate can
never lock administrators out. When HTTPS is turned on and a valid certificate is
stored, a second uvicorn server serves the same app on ``HTTPS_PORT`` (default 8443).
Renewed certificates are loaded into the running TLS context, so new connections
use them without a restart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import ssl

import uvicorn

from . import certificates

log = logging.getLogger("https")

DEFAULT_PORT = 8443


def configured_port() -> int:
    try:
        return int(os.environ.get("HTTPS_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def configured_bind() -> str:
    return os.environ.get("HTTPS_BIND") or os.environ.get("WEB_BIND") or "0.0.0.0"


def make_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


class HttpsListener:
    def __init__(self, app, *, host: str | None = None, port: int | None = None):
        self.app = app
        self.host = host or configured_bind()
        self.port = port if port is not None else configured_port()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None
        self.ctx: ssl.SSLContext | None = None
        self.sock: socket.socket | None = None
        self.error = ""
        self.cert_serial = ""
        self._apply_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------------
    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        certificates.set_listener_hook(self.request_apply)

    def request_apply(self) -> None:
        """Thread-safe: re-evaluate whether HTTPS should run and with which certificate."""
        if self.loop is None or self.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.apply(), self.loop)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def apply(self) -> None:
        async with self._apply_lock:
            cfg = certificates.get_config()
            cert = certificates.get_state().get("certificate")
            want = bool(cfg["enabled"] and cert and certificates.has_certificate())
            if not want:
                if self.running:
                    await self.stop()
                    log.info("HTTPS listener stopped")
                if not cfg["enabled"]:
                    self.error = ""
                return
            if not certificates.materialize():
                self.error = "The stored certificate could not be loaded."
                return
            certfile, keyfile = certificates.cert_paths()
            if self.running:
                if cert.get("serial") != self.cert_serial:
                    try:
                        self.ctx.load_cert_chain(certfile, keyfile)
                        self.cert_serial = cert.get("serial", "")
                        self.error = ""
                        log.info("HTTPS listener now uses the renewed certificate (serial %s)", self.cert_serial)
                    except (ssl.SSLError, OSError) as e:
                        self.error = f"The new certificate could not be loaded: {e}"
                        log.error("HTTPS: %s", self.error)
                return
            await self.start(certfile, keyfile, serial=cert.get("serial", ""))

    async def start(self, certfile: str, keyfile: str, *, serial: str = "") -> None:
        try:
            self.ctx = make_context(certfile, keyfile)
        except (ssl.SSLError, OSError) as e:
            self.error = f"The certificate could not be loaded: {e}"
            log.error("HTTPS: %s", self.error)
            return
        try:
            family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen(128)
            sock.setblocking(False)
        except OSError as e:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
            hint = " Ports below 1024 need the NET_BIND_SERVICE capability." if self.port < 1024 else ""
            self.error = f"Could not listen on port {self.port}: {e.strerror or e}.{hint}"
            log.error("HTTPS: %s", self.error)
            return
        self.sock = sock
        self.port = sock.getsockname()[1]
        config = uvicorn.Config(self.app, lifespan="off", proxy_headers=False, access_log=False, log_config=None,
                                timeout_graceful_shutdown=5)
        config.load()
        config.ssl = self.ctx
        self.server = uvicorn.Server(config)
        self.task = asyncio.get_running_loop().create_task(self._serve())
        self.cert_serial = serial
        self.error = ""
        log.info("HTTPS listener started on %s:%s", self.host, self.port)

    async def _serve(self) -> None:
        try:
            await self.server._serve(sockets=[self.sock])     # noqa: SLF001 - no signal capture for a second server
        except (Exception, SystemExit) as e:  # noqa: BLE001
            self.error = f"The HTTPS listener stopped unexpectedly: {e}"
            log.exception("HTTPS listener failed")

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=10)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self.task.cancel()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.server = self.task = self.sock = None
        self.cert_serial = ""

    def status(self) -> dict:
        return {"running": self.running, "port": self.port, "error": self.error}


_listener: HttpsListener | None = None


def get() -> HttpsListener | None:
    return _listener


def set_listener(listener: HttpsListener | None) -> None:
    global _listener
    _listener = listener


def status() -> dict:
    if _listener is None:
        return {"running": False, "port": configured_port(), "error": ""}
    return _listener.status()


def redirect_url(request) -> str | None:
    """Where to send a plain-HTTP request, or None to serve it as it is."""
    if request.url.scheme != "http" or _listener is None or not _listener.running:
        return None
    if request.url.path == "/api/health" or not certificates.redirect_enabled():
        return None
    client = request.client.host if request.client else ""
    if client in ("127.0.0.1", "::1", "localhost"):
        return None
    host = certificates.redirect_host(request.headers.get("host", ""))
    if not host:
        return None
    netloc = host if _listener.port == 443 else f"{host}:{_listener.port}"
    query = f"?{request.url.query}" if request.url.query else ""
    return f"https://{netloc}{request.url.path}{query}"
