"""Local preview with a fake Plex and sample data. Never deployed.

Run: .venv/bin/python -m tests.dev_server   then open http://127.0.0.1:8791/dev-login?u=alex&next=/home
"""
import os
import tempfile
import time

import uvicorn
from fastapi import Request
from fastapi.responses import RedirectResponse

from app import config
from app.main import create_app
from app.plex import Session

from .fakes import ACCOUNTS, KID, FakePlex

tmp = tempfile.mkdtemp()
os.environ.update(PK_DB=f"{tmp}/dev.db", PK_PUBLIC_URL="http://127.0.0.1:8791", PK_SEED_PARENTS="alex,sam",
                  PK_SECURE_COOKIES="0", PK_START_ENFORCER="0", PK_HA_WEBHOOK_FILE=f"{tmp}/none",
                  PK_MEDIA_REQUEST_URL="")
fake = FakePlex()
# Fixture PINs are already authorized; keep preview sign-in entirely local.
fake.auth_url = lambda _code, forward_url: forward_url
app = create_app(config.load(), plex=fake)
db, svc = app.state.db, app.state.svc
# Preview-only receiver: exercise setup delivery without an external service or
# credentials. This module binds to loopback and is never the production app.
app.state.notifier._url = lambda: "http://127.0.0.1:8791/dev-notification"
app.state.dev_notifications = []
now = "2026-10-19T15:00:00+00:00"
for a in (ACCOUNTS["tok-alex"], ACCOUNTS["tok-sam"]):
    db.x("INSERT INTO parents(plex_id,username,title,thumb,enabled,created_at,last_login) VALUES(?,?,?,?,1,?,?)",
         (a.id, a.username, a.title, "", now, now))
app.state.svc.sync_inline = True
cols = ("parent_id,name,status,age_level,sections,weekday_minutes,weekend_minutes,weekday_start,weekday_end,"
        "weekend_start,weekend_end,created_at,updated_at")
kid1 = db.x(f"INSERT INTO kids({cols}) VALUES(101,'Kid1','draft','kids','1,2',120,180,'07:00','20:00','07:00','21:00',?,?)",
            (now, now))
db.x(f"INSERT INTO kids({cols}) VALUES(101,'Kid2','draft','little','1,2',60,90,'07:00','19:00','08:00','19:30',?,?)",
     (now, now))
svc.connect(kid1, "tok-kid1")
db.x("INSERT INTO link_tokens(token,kid_id,parent_id,expires_at) VALUES(?,?,?,?)",
     ("preview-link-token-for-kid2-0001", 2, 101, time.time() + 10**7))
svc.approve(kid1, "owner")
from datetime import date, timedelta
for back, minutes in enumerate([47, 95, 130, 60, 88, 140, 30]):
    db.add_usage(kid1, (date.fromisoformat(svc.today()) - timedelta(days=back)).isoformat(), minutes * 60)
fake.history = lambda user_id, length=25: [
    {"date": 1792282042 - i * 7000, "title": t, "seconds": sec, "player": dev, "percent": 80, "poster": k, "name": t.split(": ")[0]}
    for i, (t, sec, dev, k) in enumerate([("Maple Club: Camping", 420, "Living Room TV", 12), ("Cloud Garden", 6300, "iPad", 11),
                                           ("Sunny Wheels", 6900, "Living Room TV", 14), ("Maple Club: Takeaway", 430, "iPad", 12),
                                           ("Paper Harbor", 6000, "Living Room TV", 32)])][:length]
db.x("INSERT INTO requests(parent_id,kind,message,status,owner_note,created_at,resolved_at) "
     "VALUES(101,'title','Could you add Maple Club season 3?','done','Added, enjoy!',?,?)", (now, now))
db.x("INSERT INTO stops(kid_id,at,reason,title) VALUES(?,?,?,?)", (kid1, now, "hours", "Cloud Garden"))
svc.set_title(kid1, 16, "allow", "alex")
svc.set_title(kid1, 11, "block", "alex")
app.state.enforcer.live = {kid1: [{"title": "Maple Club: Camping", "state": "playing", "player": "Living Room TV"}]}
app.state.enforcer.last_ok = time.time() + 10**9
COLORS = ["#d9631e", "#2f7d4f", "#1f6fb2", "#8a4fbf", "#b3261e", "#9a5b00"]


def fake_poster(rating_key):
    it = fake.items[rating_key]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><rect width="200" height="300" '
           f'fill="{COLORS[rating_key % len(COLORS)]}"/><text x="100" y="160" font-size="22" fill="#fff" '
           f'text-anchor="middle" font-family="sans-serif">{it.title}</text></svg>')
    return svg.encode(), "image/svg+xml"


fake.poster = fake_poster
for n, title in enumerate(["Kite Club", "Winter Picnic", "Paper Harbor", "Button Parade", "Meadow Tales", "Green Cottage", "Tiny Workshop",
                           "Rhythm Garden", "Color Quest", "Maple Club Movie"], start=30):
    from app.plex import Item
    fake.items[n] = Item(n, title, 2000 + n % 20, ["G", "PG", "TV-Y", "PG-13"][n % 4],
                         "show" if n % 4 == 2 else "movie", "2" if n % 4 == 2 else "1",
                         summary=f"{title} is a family favorite about friendship, courage and finding your way home.",
                         duration_ms=5_700_000, genres=["Animation", "Family"],
                         added_at=int(time.time()) - (0 if n % 3 == 0 else 90 * 86400))
svc.sync_titles(kid1)


@app.post("/dev-notification")
async def dev_notification(request: Request):
    app.state.dev_notifications.append(await request.json())
    return {"received": True}


@app.post("/dev-plex-account")
def dev_plex_account(u: str):
    """Choose a fixture identity for the next simulated Plex OAuth exchange."""
    fake.next_token = f"tok-{u}" if f"tok-{u}" in ACCOUNTS else None
    return {"ready": fake.next_token is not None}


@app.get("/dev-login")
def dev_login(request: Request, u: str, next: str = "/"):
    a = ACCOUNTS[f"tok-{u}"]
    role = "owner" if u == "owner" else "parent"
    request.session.clear()
    request.session["user"] = {"id": a.id, "name": a.title, "username": a.username, "thumb": "", "role": role}
    request.session["sid"] = app.state.sessions.create(a.id, role)
    request.session["epoch"] = db.setting("session_epoch", "1")
    return RedirectResponse(next, status_code=303)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8791, log_level="warning")
