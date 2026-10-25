"""Remove every child's Plex share and labels (used before uninstalling).

Run inside the container:  python -m app.cleanup            (list only)
                           python -m app.cleanup --apply    (remove)
"""
import sys

from . import config
from .db import DB
from .plex import PlexClient, PlexError
from .service import Service


def main(argv: list[str]) -> int:
    cfg = config.load()
    db = DB(cfg.db_path)
    svc = Service(db, PlexClient(cfg, db.setting("client_id") or "plex-for-kids-cleanup"), cfg)
    kids = db.q("SELECT id, name, status, plex_username FROM kids "
                "WHERE status <> 'removed' OR sync_state='error' "
                "OR EXISTS (SELECT 1 FROM tracked_shares WHERE kid_id=kids.id)")
    if not kids:
        print("No kids' accounts to remove.")
        return 0
    failed = 0
    for kid in kids:
        label = f"{kid['name']} ({kid['status']}, Plex account: {kid['plex_username'] or 'not connected'})"
        if "--apply" not in argv:
            print("would remove:", label)
            continue
        try:
            svc.remove(kid["id"], "cleanup")
            print("removed:", label)
        except PlexError as e:
            failed += 1
            print("FAILED:", label, "-", e)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
