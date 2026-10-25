"""Everything that talks to Plex (plex.tv and the local server) and to Tautulli.

The owner token and the Tautulli key are read in place from the apps' own config files
and only ever sent in request headers (or, for Tautulli, inside the private Docker network).
Error messages never include URLs, so a failed call cannot leak a key into logs.
"""
from __future__ import annotations

import configparser
import os
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

PRODUCT = "KidsWatch for Plex"
VERSION = "1.0"
PLEX_TV = "https://plex.tv"
CLIENTS = "https://clients.plex.tv"


class PlexError(Exception):
    """A Plex or Tautulli call failed. The message is safe to show and log."""


@dataclass
class Account:
    id: int
    uuid: str
    username: str
    title: str
    email: str
    thumb: str

    @property
    def name(self) -> str:
        return self.title or self.username or self.email


@dataclass
class Share:
    share_id: int
    user_id: int
    username: str
    accepted: bool
    sections: list[str]
    filter_movies: str
    filter_tv: str
    allow_sync: bool


@dataclass
class SharedUser:
    id: int
    username: str
    title: str
    thumb: str


@dataclass
class Session:
    session_id: str | None
    user_id: int | None
    media_type: str
    title: str
    state: str
    player: str


@dataclass
class Item:
    rating_key: int
    title: str
    year: int | None
    rating: str | None
    item_type: str  # movie | show
    section_key: str
    labels: list[str] = field(default_factory=list)
    summary: str = ""
    duration_ms: int = 0
    genres: list[str] = field(default_factory=list)
    added_at: int = 0
    seasons: int = 0
    episodes: int = 0


def _item(m: dict, section_key: str) -> "Item":
    return Item(rating_key=int(m["ratingKey"]), title=m.get("title", ""), year=m.get("year"),
                rating=m.get("contentRating"), item_type=m["type"], section_key=str(section_key),
                labels=[x.get("tag") for x in m.get("Label", []) or []], summary=m.get("summary") or "",
                duration_ms=int(m.get("duration") or 0), genres=[g.get("tag") for g in m.get("Genre", []) or []][:3],
                added_at=int(m.get("addedAt") or 0), seasons=int(m.get("childCount") or 0),
                episodes=int(m.get("leafCount") or 0))


class _Cache:
    def __init__(self):
        self._data: dict[str, tuple[float, object]] = {}

    def get(self, key: str, ttl: float):
        hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    def put(self, key: str, value):
        self._data[key] = (time.monotonic(), value)
        return value

    def drop(self, prefix: str = "") -> None:
        for key in [k for k in self._data if k.startswith(prefix)]:
            self._data.pop(key, None)


class PlexClient:
    def __init__(self, cfg, client_id: str, http: httpx.Client | None = None):
        self.cfg = cfg
        self.client_id = client_id
        self.http = http or httpx.Client(timeout=20, follow_redirects=False)
        self.cache = _Cache()
        self._prefs: tuple[float, str, str] | None = None
        self._tautulli: tuple[float, str] | None = None
        self._label_lock = threading.Lock()

    # ---------- credentials, read in place ----------
    def _owner(self) -> tuple[str, str]:
        """(token, machine id) from Plex's Preferences.xml, re-read when the file changes."""
        path = self.cfg.plex_prefs
        try:
            mtime = os.stat(path).st_mtime
        except OSError as e:
            raise PlexError("Plex settings file is not readable") from e
        if not self._prefs or self._prefs[0] != mtime:
            attrs = ET.parse(path).getroot().attrib
            token = attrs.get("PlexOnlineToken", "")
            machine = attrs.get("ProcessedMachineIdentifier") or attrs.get("MachineIdentifier", "")
            if not token or not machine:
                raise PlexError("Plex server is not signed in")
            self._prefs = (mtime, token, machine)
        return self._prefs[1], self._prefs[2]

    def owner_token(self) -> str:
        return self._owner()[0]

    @property
    def machine_id(self) -> str:
        return self._owner()[1]

    def _tautulli_key(self) -> str:
        path = self.cfg.tautulli_config
        try:
            mtime = os.stat(path).st_mtime
        except OSError as e:
            raise PlexError("Tautulli settings are not readable") from e
        if not self._tautulli or self._tautulli[0] != mtime:
            parser = configparser.RawConfigParser()
            parser.read(path)
            self._tautulli = (mtime, parser.get("General", "api_key", fallback=""))
        return self._tautulli[1]

    # ---------- HTTP ----------
    def _headers(self, token: str | None, accept: str = "application/json") -> dict:
        h = {
            "X-Plex-Product": PRODUCT,
            "X-Plex-Version": VERSION,
            "X-Plex-Client-Identifier": self.client_id,
            "X-Plex-Device-Name": PRODUCT,
            "Accept": accept,
        }
        if token:
            h["X-Plex-Token"] = token
        return h

    def _req(self, method: str, url: str, what: str, token: str | None = None,
             accept: str = "application/json", **kw) -> httpx.Response:
        try:
            r = self.http.request(method, url, headers=self._headers(token, accept), **kw)
        except httpx.HTTPError as e:
            raise PlexError(f"{what}: could not reach {'plex.tv' if 'plex.tv' in url else 'the server'}") from None
        if r.status_code >= 400:
            raise PlexError(f"{what}: Plex answered {r.status_code}")
        return r

    def _xml(self, r: httpx.Response) -> ET.Element:
        try:
            return ET.fromstring(r.content)
        except ET.ParseError:
            raise PlexError("Plex sent an unexpected answer") from None

    # ---------- sign in with Plex ----------
    def create_pin(self) -> tuple[int, str]:
        r = self._req("POST", f"{CLIENTS}/api/v2/pins", "Start sign-in", params={"strong": "true"})
        data = r.json()
        return int(data["id"]), str(data["code"])

    def auth_url(self, code: str, forward_url: str) -> str:
        params = {
            "clientID": self.client_id,
            "code": code,
            "forwardUrl": forward_url,
            "context[device][product]": PRODUCT,
        }
        return "https://app.plex.tv/auth#?" + urlencode(params)

    def check_pin(self, pin_id: int, code: str) -> str | None:
        r = self._req("GET", f"{CLIENTS}/api/v2/pins/{int(pin_id)}", "Finish sign-in", params={"code": code})
        return r.json().get("authToken") or None

    def account(self, token: str) -> Account:
        d = self._req("GET", f"{PLEX_TV}/api/v2/user", "Read Plex account", token=token).json()
        return Account(id=int(d["id"]), uuid=d.get("uuid", ""), username=d.get("username") or "",
                       title=d.get("title") or d.get("friendlyName") or "", email=d.get("email") or "",
                       thumb=d.get("thumb") or "")

    def owner(self) -> Account:
        hit = self.cache.get("owner", 6 * 3600)
        return hit or self.cache.put("owner", self.account(self.owner_token()))

    # ---------- sharing (owner token) ----------
    def section_ids(self) -> dict[str, int]:
        """Local library key -> plex.tv section id."""
        hit = self.cache.get("sections", 3600)
        if hit:
            return hit
        root = self._xml(self._req("GET", f"{PLEX_TV}/api/servers/{self.machine_id}", "Read libraries",
                                   token=self.owner_token(), accept="application/xml"))
        out = {}
        for server in root:
            for sec in server:
                out[sec.get("key")] = int(sec.get("id"))
        return self.cache.put("sections", out)

    def _sections(self) -> dict[str, tuple[str, str]]:
        hit = self.cache.get("section_info", 3600)
        if hit:
            return hit
        d = self._req("GET", f"{self.cfg.plex_url}/library/sections", "Read libraries",
                      token=self.owner_token()).json()
        out = {x["key"]: (x["title"], x.get("type", "")) for x in d["MediaContainer"].get("Directory", [])}
        return self.cache.put("section_info", out)

    def section_titles(self) -> dict[str, str]:
        return {k: v[0] for k, v in self._sections().items()}

    def readiness_sections(self) -> dict[str, str]:
        """Probe local Plex with credentials, bypassing the normal library cache."""
        d = self._req("GET", f"{self.cfg.plex_url}/library/sections", "Check Plex readiness",
                      token=self.owner_token(), timeout=4).json()
        return {str(x["key"]): x["type"] for x in d["MediaContainer"].get("Directory", [])}

    def section_type(self, section_key: str) -> str:
        return self._sections().get(str(section_key), ("", ""))[1]

    def shares(self, fresh: bool = False) -> dict[int, Share]:
        """Every share of this server, keyed by plex.tv user id. Includes pending invites."""
        hit = None if fresh else self.cache.get("shares", 30)
        if hit is not None:
            return hit
        root = self._xml(self._req("GET", f"{PLEX_TV}/api/servers/{self.machine_id}/shared_servers",
                                   "Read shares", token=self.owner_token(), accept="application/xml"))
        out = {}
        for s in root.findall("SharedServer"):
            if not s.get("userID"):
                continue
            out[int(s.get("userID"))] = Share(
                share_id=int(s.get("id")), user_id=int(s.get("userID")), username=s.get("username") or "",
                accepted=bool(s.get("acceptedAt")),
                sections=[c.get("key") for c in s.findall("Section") if c.get("shared") == "1"],
                filter_movies=s.get("filterMovies") or "", filter_tv=s.get("filterTelevision") or "",
                allow_sync=s.get("allowSync") == "1")
        return self.cache.put("shares", out)

    def shared_users(self) -> dict[int, SharedUser]:
        """Read accepted users fresh. Authorization snapshots are ordered and cached by Service.

        Never cache this response here: a slow older HTTP response must not be returned later as a
        new authorization observation. Other Plex caches are not used to authorize parent sessions.
        """
        root = self._xml(self._req("GET", f"{PLEX_TV}/api/users", "Read shared users",
                                   token=self.owner_token(), accept="application/xml"))
        mid = self.machine_id
        out = {}
        for u in root.findall("User"):
            if not any(s.get("machineIdentifier") == mid for s in u.findall("Server")):
                continue
            out[int(u.get("id"))] = SharedUser(id=int(u.get("id")), username=u.get("username") or "",
                                               title=u.get("title") or "", thumb=u.get("thumb") or "")
        return out

    def invite(self, identifier: str, section_keys: list[str], filters: dict[str, str]) -> None:
        ids = self.section_ids()
        library_ids = [ids[k] for k in section_keys if k in ids]
        if not library_ids:
            raise PlexError("No permitted libraries were found on Plex. Review your library choices before sharing.")
        body = {
            "server_id": self.machine_id,
            "shared_server": {"library_section_ids": library_ids,
                              "invited_email": identifier},
            "sharing_settings": {"allowSync": "0", "allowCameraUpload": "0", "allowChannels": "0",
                                 **filters},
        }
        self._req("POST", f"{PLEX_TV}/api/servers/{self.machine_id}/shared_servers", "Share the libraries",
                  token=self.owner_token(), accept="application/xml", json=body)
        self.cache.drop("shares")

    def update_share(self, share_id: int, section_keys: list[str], filters: dict[str, str]) -> None:
        """Set a share's libraries and filters in one call.

        Verified live 2026-10-19: plex.tv takes POST /api/v2/shared_servers/{id} with "settings" and
        "librarySectionIds" (201). The older PUT /api/v2/sharings/{userId} (python-plexapi 4.18.3) answers 404,
        and PUT /api/servers/{machine}/shared_servers/{id} answers 200 but ignores the filters."""
        ids = self.section_ids()
        library_ids = [ids[k] for k in section_keys if k in ids]
        if not library_ids:
            raise PlexError("No permitted libraries were found on Plex. Review your library choices before sharing.")
        body = {"librarySectionIds": library_ids,
                "settings": {"allowSync": False, "allowCameraUpload": False, "allowChannels": False,
                             "filterMovies": filters["filterMovies"], "filterTelevision": filters["filterTelevision"],
                             "filterMusic": filters.get("filterMusic", "")}}
        self._req("POST", f"{PLEX_TV}/api/v2/shared_servers/{int(share_id)}", "Update the child's share",
                  token=self.owner_token(), json=body)
        self.cache.drop("shares")

    def remove_share(self, share_id: int) -> None:
        self._req("DELETE", f"{PLEX_TV}/api/servers/{self.machine_id}/shared_servers/{int(share_id)}",
                  "Remove the share", token=self.owner_token(), accept="application/xml")
        self.cache.drop("shares")

    # ---------- the child's own account (token used once, never stored) ----------
    def accept_invite_from_owner(self, kid_token: str) -> bool:
        owner = self.owner()
        root = self._xml(self._req("GET", f"{PLEX_TV}/api/invites/requests", "Read invitations",
                                   token=kid_token, accept="application/xml"))
        for inv in root.findall("Invite"):
            from_owner = (inv.get("id") == str(owner.id)
                          or (owner.username and inv.get("username", "").lower() == owner.username.lower())
                          or (owner.email and inv.get("email", "").lower() == owner.email.lower()))
            if not from_owner or inv.get("home") == "1":  # only a library share, never a Plex Home invite
                continue
            params = {k: inv.get(k, "0") for k in ("friend", "home", "server")}
            self._req("PUT", f"{PLEX_TV}/api/invites/requests/{inv.get('id')}", "Accept the invitation",
                      token=kid_token, accept="application/xml", params=params)
            return True
        return False

    def block_online_sources(self, kid_token: str, kid_uuid: str) -> tuple[list[str], list[str]]:
        """Turn off Plex's own free movies, shows and Live TV for the child's account.

        Returns (keys changed, keys still on after reading the settings back)."""
        url = f"{PLEX_TV}/api/v2/user/{kid_uuid}/settings/opt_outs"

        def read() -> dict[str, str]:
            root = self._xml(self._req("GET", url, "Read Plex's own content settings", token=kid_token,
                                       accept="application/xml"))
            return {o.get("key"): o.get("value") for o in root.iter("optOut") if o.get("key")}

        changed = []
        for key, value in read().items():
            if value != "opt_out":
                self._req("POST", url, "Turn off Plex's own content", token=kid_token,
                          accept="application/xml", params={"key": key, "value": "opt_out"})
                changed.append(key)
        still_on = [k for k, v in read().items() if v != "opt_out"]
        return changed, still_on

    # ---------- local server ----------
    def sessions(self) -> list[Session]:
        d = self._req("GET", f"{self.cfg.plex_url}/status/sessions", "Read what is playing",
                      token=self.owner_token()).json()
        out = []
        for m in d.get("MediaContainer", {}).get("Metadata", []) or []:
            user = m.get("User") or {}
            player = m.get("Player") or {}
            if m.get("type") == "episode":
                title = f"{m.get('grandparentTitle', '')}: {m.get('title', '')}"
            else:
                title = m.get("title", "")
            out.append(Session(
                session_id=(m.get("Session") or {}).get("id"),
                user_id=int(user["id"]) if user.get("id") else None,
                media_type=m.get("type", ""), title=title,
                state=player.get("state", ""), player=player.get("title") or player.get("product") or ""))
        return out

    def terminate(self, session_id: str, reason: str) -> None:
        self._req("GET", f"{self.cfg.plex_url}/status/sessions/terminate", "Stop a video",
                  token=self.owner_token(), params={"sessionId": session_id, "reason": reason})

    def library(self, section_key: str, fresh: bool = False) -> list[Item]:
        hit = None if fresh else self.cache.get(f"lib:{section_key}", 600)
        if hit is not None:
            return hit
        d = self._req("GET", f"{self.cfg.plex_url}/library/sections/{section_key}/all", "Read the library",
                      token=self.owner_token()).json()
        items = [_item(m, section_key) for m in d.get("MediaContainer", {}).get("Metadata", []) or []
                 if m.get("type") in ("movie", "show")]
        return self.cache.put(f"lib:{section_key}", items)

    def library_cached(self, section_key: str) -> list[Item] | None:
        """The library list only if it is already in memory (never waits on the network)."""
        return self.cache.get(f"lib:{section_key}", 3600)

    def item(self, rating_key: int) -> Item | None:
        hit = self.cache.get(f"item:{rating_key}", 300)
        if hit is not None:
            return hit
        try:
            d = self._req("GET", f"{self.cfg.plex_url}/library/metadata/{int(rating_key)}", "Read a title",
                          token=self.owner_token()).json()
        except PlexError:
            return None
        ms = d.get("MediaContainer", {}).get("Metadata", []) or []
        if not ms or ms[0].get("type") not in ("movie", "show"):
            return None
        it = _item(ms[0], str(ms[0].get("librarySectionID")))
        return self.cache.put(f"item:{rating_key}", it)

    def _edit_labels(self, it: Item, params: dict) -> None:
        params = {"type": 1 if it.item_type == "movie" else 2, "id": it.rating_key, "label.locked": 1, **params}
        self._req("PUT", f"{self.cfg.plex_url}/library/sections/{it.section_key}/all", "Update a title",
                  token=self.owner_token(), params=params)
        self.cache.drop(f"item:{it.rating_key}")
        self.cache.drop(f"lib:{it.section_key}")

    def add_label(self, rating_key: int, label: str) -> None:
        with self._label_lock:  # the edit sends the whole label list, so read it fresh and edit one at a time
            self.cache.drop(f"item:{rating_key}")
            it = self.item(rating_key)
            if not it:
                raise PlexError("That title was not found")
            if label in it.labels:
                return
            tags = it.labels + [label]
            self._edit_labels(it, {f"label[{i}].tag.tag": t for i, t in enumerate(tags)})

    def remove_label(self, rating_key: int, label: str) -> None:
        with self._label_lock:
            self.cache.drop(f"item:{rating_key}")
            it = self.item(rating_key)
            if not it or label not in it.labels:
                return
            self._edit_labels(it, {"label[].tag.tag-": label})

    def items_with_label(self, section_key: str, label: str) -> set[int]:
        """Rating keys in one library that carry this label (empty if no title has it yet)."""
        d = self._req("GET", f"{self.cfg.plex_url}/library/sections/{section_key}/label", "Read labels",
                      token=self.owner_token()).json()
        ids = [x.get("key") for x in d.get("MediaContainer", {}).get("Directory", []) or [] if x.get("title") == label]
        if not ids:
            return set()
        d = self._req("GET", f"{self.cfg.plex_url}/library/sections/{section_key}/all", "Read labeled titles",
                      token=self.owner_token(), params={"label": ids[0]}).json()
        return {int(m["ratingKey"]) for m in d.get("MediaContainer", {}).get("Metadata", []) or []}

    def labels_by_item(self, section_key: str) -> dict[int, list[str]]:
        """Every labeled title in a library and its labels (one call per label; libraries have few labels)."""
        d = self._req("GET", f"{self.cfg.plex_url}/library/sections/{section_key}/label", "Read labels",
                      token=self.owner_token()).json()
        out: dict[int, list[str]] = {}
        for lab in d.get("MediaContainer", {}).get("Directory", []) or []:
            items = self._req("GET", f"{self.cfg.plex_url}/library/sections/{section_key}/all", "Read labeled titles",
                              token=self.owner_token(), params={"label": lab.get("key")}).json()
            for m in items.get("MediaContainer", {}).get("Metadata", []) or []:
                out.setdefault(int(m["ratingKey"]), []).append(lab.get("title"))
        return out

    def add_label_bulk(self, section_key: str, rating_keys: list[int], label: str) -> None:
        """Add one label to many titles. Titles that share the same existing labels are edited together,
        because Plex's edit call replaces the whole label list."""
        kind = 2 if self.section_type(section_key) == "show" else 1
        with self._label_lock:
            current = self.labels_by_item(section_key)
            groups: dict[tuple, list[int]] = {}
            for key in rating_keys:
                existing = tuple(sorted(current.get(int(key), [])))
                if label not in existing:
                    groups.setdefault(existing, []).append(int(key))
            for existing, keys in groups.items():
                tags = list(existing) + [label]
                for i in range(0, len(keys), 50):
                    chunk = keys[i:i + 50]
                    params = {"type": kind, "id": ",".join(str(k) for k in chunk), "label.locked": 1}
                    params.update({f"label[{n}].tag.tag": t for n, t in enumerate(tags)})
                    self._req("PUT", f"{self.cfg.plex_url}/library/sections/{section_key}/all", "Update titles",
                              token=self.owner_token(), params=params)
                    for k in chunk:
                        self.cache.drop(f"item:{k}")

    def remove_label_bulk(self, section_key: str, rating_keys: list[int], label: str) -> None:
        """Take one label off many titles at once (removal leaves every other label alone)."""
        kind = 2 if self.section_type(section_key) == "show" else 1
        with self._label_lock:
            for i in range(0, len(rating_keys), 50):
                chunk = rating_keys[i:i + 50]
                self._req("PUT", f"{self.cfg.plex_url}/library/sections/{section_key}/all", "Update titles",
                          token=self.owner_token(),
                          params={"type": kind, "id": ",".join(str(int(k)) for k in chunk), "label.locked": 1,
                                  "label[].tag.tag-": label})
                for k in chunk:
                    self.cache.drop(f"item:{k}")

    def poster(self, rating_key: int) -> tuple[bytes, str]:
        """Poster thumbnails, kept in memory for a day (about 15 KB each, at most 600) so grids load fast."""
        hit = self.cache.get(f"poster:{rating_key}", 86400)
        if hit:
            return hit
        result = self._fetch_poster(rating_key)
        posters = [k for k in self.cache._data if k.startswith("poster:")]
        if len(posters) >= 600:
            for k in sorted(posters, key=lambda k: self.cache._data[k][0])[:100]:
                self.cache._data.pop(k, None)
        return self.cache.put(f"poster:{rating_key}", result)

    def _fetch_poster(self, rating_key: int) -> tuple[bytes, str]:
        params = {"width": 240, "height": 360, "minSize": 1, "upscale": 1,
                  "url": f"/library/metadata/{int(rating_key)}/thumb"}
        r = self._req("GET", f"{self.cfg.plex_url}/photo/:/transcode", "Read a poster",
                      token=self.owner_token(), accept="image/*", params=params)
        return r.content, r.headers.get("content-type", "image/jpeg")

    # ---------- Tautulli ----------
    def history(self, user_id: int, length: int = 25) -> list[dict]:
        """Recent plays from Tautulli. Cached for a minute so pages stay quick; if Tautulli is slow or down,
        the last answer (up to an hour old) is shown instead of an error."""
        key = f"hist:{int(user_id)}:{int(length)}"
        hit = self.cache.get(key, 60)
        if hit is not None:
            return hit
        params = {"apikey": self._tautulli_key(), "cmd": "get_history", "user_id": int(user_id),
                  "length": int(length)}
        try:
            r = self.http.get(f"{self.cfg.tautulli_url}/api/v2", params=params, timeout=6)
            r.raise_for_status()
            data = r.json()["response"]["data"]["data"]
        except (httpx.HTTPError, KeyError, ValueError):
            stale = self.cache.get(key, 3600)
            if stale is not None:
                return stale
            raise PlexError("Watch history is not available right now") from None
        out = []
        for row in data:
            poster = row.get("grandparent_rating_key") if row.get("media_type") == "episode" else row.get("rating_key")
            out.append({"date": row.get("date"), "title": row.get("full_title") or row.get("title"),
                        "seconds": row.get("play_duration") or row.get("duration") or 0,
                        "player": row.get("player"), "percent": row.get("percent_complete"),
                        "poster": int(poster) if str(poster or "").isdigit() else None,
                        "name": (row.get("grandparent_title") if row.get("media_type") == "episode" else None)
                        or row.get("title") or row.get("full_title") or ""})
        return self.cache.put(key, out)
