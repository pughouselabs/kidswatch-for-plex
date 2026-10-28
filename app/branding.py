"""Optional owner artwork, captured at startup without exposing a directory."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import stat

log = logging.getLogger("kidswatch")
SLOTS = ("maple-club", "cloud-garden", "paper-harbor", "mountain-puzzle", "pebble-trail", "winter-picnic")
CONTENT_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def bundled_art(slot: str) -> str:
    return f"/static/lp/{slot}.svg?v=1"


def _read_file(directory: int, filename: str, limit: int) -> bytes:
    # Single-component names and O_NOFOLLOW protect against traversal and symlink swaps.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", filename):
        raise ValueError("invalid filename")
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not a regular file")
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("invalid file size")
    return data


class OwnerArt:
    def __init__(self, directory: str):
        self.images: dict[str, tuple[bytes, str]] = {}
        self.titles: dict[str, str] = {}
        if not directory:
            return
        try:
            fd = os.open(Path(directory), os.O_RDONLY | os.O_DIRECTORY)
            try:
                mapping = json.loads(_read_file(fd, "art.json", 16 * 1024))
                if not isinstance(mapping, dict):
                    raise ValueError("invalid mapping")
                for slot in SLOTS:
                    if slot not in mapping:
                        continue
                    try:
                        entry = mapping[slot]
                        if not isinstance(entry, dict):
                            raise ValueError("invalid entry")
                        filename, title = entry.get("filename"), entry.get("title")
                        if not isinstance(filename, str) or not isinstance(title, str) or not title.strip():
                            raise ValueError("invalid filename or title")
                        content_type = CONTENT_TYPES.get(Path(filename).suffix.lower())
                        if content_type is None:
                            raise ValueError("unsupported extension")
                        data = _read_file(fd, filename, MAX_IMAGE_BYTES)
                        self.images[slot] = (data, content_type)
                        self.titles[slot] = title.strip()[:120]
                    except (OSError, ValueError):
                        log.warning("Invalid owner artwork for slot %s; using bundled art", slot)
            finally:
                os.close(fd)
        except (OSError, ValueError, UnicodeError, RecursionError):
            log.warning("Cannot load PK_LANDING_ART_DIR mapping; using bundled art")

    def url(self, slot: str) -> str:
        return f"/owner-art/{slot}" if slot in self.images else bundled_art(slot)

    def title(self, slot: str, default: str) -> str:
        return self.titles.get(slot, default)
