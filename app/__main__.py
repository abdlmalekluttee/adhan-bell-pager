"""Run the web service: ``python -m app``.

The listening port comes from ``WEB_PORT`` (or ``web.port`` in config.yaml, default
8080) and the address from ``WEB_BIND`` (or ``web.bind``).
"""
from __future__ import annotations

import os

import uvicorn

from . import config


def main() -> None:
    cfg = config.load()
    port = int(os.environ.get("WEB_PORT") or cfg.web.port)
    bind = os.environ.get("WEB_BIND") or cfg.web.bind
    uvicorn.run("app.main:app", host=bind, port=port, proxy_headers=False, access_log=False,
                timeout_graceful_shutdown=15)


if __name__ == "__main__":
    main()
