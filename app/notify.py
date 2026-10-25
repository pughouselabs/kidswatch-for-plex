"""Send a short note through an optional notification webhook (including Home Assistant)."""
from __future__ import annotations

import logging
import threading

import httpx

log = logging.getLogger("kidswatch.notify")


class Notifier:
    def __init__(self, webhook_file: str, public_url: str):
        self.webhook_file = webhook_file
        self.public_url = public_url

    def _url(self) -> str | None:
        try:
            with open(self.webhook_file) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def configured(self) -> bool:
        return self._url() is not None

    def send(self, title: str, message: str, path: str = "/admin") -> None:
        url = self._url()
        if not url:
            log.info("notify skipped (no webhook configured): %s", title)
            return
        payload = {"title": title, "message": message, "url": self.public_url + path}

        def post():
            try:
                httpx.post(url, json=payload, timeout=10).raise_for_status()
            except httpx.HTTPError:
                log.warning("notify failed: %s", title)  # never log the webhook URL

        threading.Thread(target=post, daemon=True).start()
