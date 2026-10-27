"""A stand-in for PlexClient that records every change instead of calling Plex."""
from __future__ import annotations

from app.plex import Account, Item, PlexError, Session, Share, SharedUser

OWNER = Account(id=1, uuid="u-owner", username="owner", title="Admin", email="owner@example.com", thumb="")
ALEX = Account(id=101, uuid="u-alex", username="alex", title="Alex", email="alex@example.com", thumb="")
SAM = Account(id=102, uuid="u-sam", username="sam", title="Sam", email="sam@example.com", thumb="")
OTHER = Account(id=103, uuid="u-other", username="casey", title="Casey", email="casey@example.com", thumb="")
STRANGER = Account(id=999, uuid="u-x", username="stranger", title="Stranger", email="x@example.com", thumb="")
KID = Account(id=201, uuid="u-kid", username="kid1", title="kid1", email="alex+kid1@example.com", thumb="")

ACCOUNTS = {f"tok-{a.username}": a for a in (OWNER, ALEX, SAM, OTHER, STRANGER, KID)}


class FakePlex:
    def __init__(self):
        self.calls: list[tuple] = []
        self._pin = 0
        self.pin_token: dict[int, str | None] = {}
        self.next_token: str | None = None
        self.share_map: dict[int, Share] = {}
        self.live: list[Session] = []
        self.accept_ok = True
        self.fail_filters = False
        self.fail_remove_share = False
        self.fail_remove_labels = False
        self.fail_labels = False
        self.drop_filters = False  # plex.tv "forgets" the filters on a new share
        self.fail_terminate: set[str] = set()
        self.items = {
            11: Item(11, "Cloud Garden", 2016, "PG", "movie", "1", genres=["Animation", "Family"]),
            12: Item(12, "Maple Club", 2018, "TV-Y", "show", "2"),
            13: Item(13, "Midnight Mystery", 2016, "R", "movie", "1"),
            14: Item(14, "Sunny Wheels", 2006, "G", "movie", "1"),
            15: Item(15, "Some Album", 2001, None, "movie", "3"),
            16: Item(16, "Mountain Puzzle", 2017, "PG-13", "movie", "1"),
            17: Item(17, "Home Video", 2020, None, "movie", "1"),
        }

    # credentials
    def owner_token(self):
        return "owner-secret-token"

    machine_id = "machine"

    def fresh_owner(self):
        return self.owner()

    def owner(self):
        return OWNER

    # sign in
    def create_pin(self):
        self._pin += 1
        self.pin_token[self._pin] = self.next_token
        return self._pin, f"code{self._pin}"

    def auth_url(self, code, forward_url):
        return f"https://app.plex.tv/auth#?code={code}&forwardUrl={forward_url}"

    def check_pin(self, pin_id, code):
        return self.pin_token.get(pin_id)

    def account(self, token):
        if token not in ACCOUNTS:
            raise PlexError("bad token")
        return ACCOUNTS[token]

    # sharing
    def shared_users(self):
        return {a.id: SharedUser(a.id, a.username, a.title, "") for a in (ALEX, SAM, OTHER)}

    def shares(self, fresh=False):
        return dict(self.share_map)

    def section_titles(self):
        return {"1": "Movies", "2": "TV Shows", "3": "Music"}

    def invite(self, identifier, section_keys, filters):
        self.calls.append(("invite", identifier, tuple(section_keys), dict(filters)))
        acct = next(a for a in ACCOUNTS.values() if identifier in (a.username, a.email))
        kept = {} if self.drop_filters else filters
        self.share_map[acct.id] = Share(500 + acct.id, acct.id, acct.username, False, list(section_keys),
                                        kept.get("filterMovies", ""), kept.get("filterTelevision", ""), False)

    def update_share(self, share_id, section_keys, filters):
        if self.fail_filters:
            raise PlexError("Update the child's share: Plex answered 500")
        self.calls.append(("update_share", share_id, tuple(section_keys), dict(filters)))
        for s in self.share_map.values():
            if s.share_id == share_id:
                s.sections = list(section_keys)
                s.filter_movies, s.filter_tv = filters["filterMovies"], filters["filterTelevision"]
                s.allow_sync = False

    def remove_share(self, share_id):
        self.calls.append(("remove_share", share_id))
        if self.fail_remove_share:
            raise PlexError("Remove the share: Plex answered 500")
        self.share_map = {k: v for k, v in self.share_map.items() if v.share_id != share_id}

    def accept_invite_from_owner(self, kid_token):
        self.calls.append(("accept", kid_token))
        acct = ACCOUNTS[kid_token]
        if self.accept_ok and acct.id in self.share_map:
            self.share_map[acct.id].accepted = True
            return True
        return False

    def block_online_sources(self, kid_token, kid_uuid):
        self.calls.append(("block_online", kid_uuid))
        if getattr(self, "optout_fails", False):
            return ["tv.plex.provider.vod"], ["tv.plex.provider.epg"]
        return ["tv.plex.provider.vod"], []

    # server
    def sessions(self):
        return list(self.live)

    def terminate(self, session_id, reason):
        if session_id in self.fail_terminate:
            raise PlexError("Stop the video: Plex answered 500")
        self.calls.append(("terminate", session_id, reason))

    def library(self, section_key, fresh=False):
        return [i for i in self.items.values() if i.section_key == section_key]

    def library_cached(self, section_key):
        return self.library(section_key)

    def readiness_sections(self):
        return {"1": "movie", "2": "show", "3": "artist"}

    def section_type(self, section_key):
        return self.readiness_sections().get(str(section_key), "")

    def items_with_label(self, section_key, label):
        if self.fail_labels:
            raise PlexError("Read labels: Plex answered 500")
        return {k for k, i in self.items.items() if i.section_key == section_key and label in i.labels}

    def add_label_bulk(self, section_key, rating_keys, label):
        self.calls.append(("add_label_bulk", section_key, tuple(rating_keys), label))
        for k in rating_keys:
            if label not in self.items[k].labels:
                self.items[k].labels.append(label)

    def remove_label_bulk(self, section_key, rating_keys, label):
        self.calls.append(("remove_label_bulk", section_key, tuple(rating_keys), label))
        if self.fail_remove_labels:
            raise PlexError("Remove labels: Plex answered 500")
        for k in rating_keys:
            if label in self.items[k].labels:
                self.items[k].labels.remove(label)

    def labeled(self, label):
        return sorted(k for k, i in self.items.items() if label in i.labels)

    def item(self, rating_key):
        return self.items.get(rating_key)

    def add_label(self, rating_key, label):
        self.calls.append(("add_label", rating_key, label))
        self.items[rating_key].labels.append(label)

    def remove_label(self, rating_key, label):
        self.calls.append(("remove_label", rating_key, label))
        if label in self.items[rating_key].labels:
            self.items[rating_key].labels.remove(label)

    def poster(self, rating_key):
        return b"\x89PNG", "image/png"

    def history(self, user_id, length=25):
        return [{"date": 1792282042, "title": "Cloud Garden", "seconds": 1800, "player": "Living Room TV", "percent": 40,
                 "poster": 11}]

    def names(self):
        return [c[0] for c in self.calls]
