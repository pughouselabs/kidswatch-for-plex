"""The real PlexClient against a fake Plex server (no network)."""
import httpx

from app.plex import PlexClient


class Cfg:
    plex_url = "http://plex"
    tautulli_url = "http://tautulli"
    tautulli_config = "/nonexistent"

    def __init__(self, prefs):
        self.plex_prefs = prefs


def client(tmp_path, handler):
    prefs = tmp_path / "Preferences.xml"
    prefs.write_text('<Preferences PlexOnlineToken="T0K" ProcessedMachineIdentifier="M1"/>')
    return PlexClient(Cfg(str(prefs)), "cid", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_posters_are_cached(tmp_path):
    hits = []

    def handler(req):
        hits.append(req.url.path)
        assert req.headers["X-Plex-Token"] == "T0K" and "T0K" not in str(req.url)
        return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})

    p = client(tmp_path, handler)
    assert p.poster(11) == (b"img", "image/jpeg")
    assert p.poster(11) == (b"img", "image/jpeg")
    assert len(hits) == 1


def test_label_lookup_by_name(tmp_path):
    def handler(req):
        if req.url.path.endswith("/label"):
            return httpx.Response(200, json={"MediaContainer": {"Directory": [{"key": "77", "title": "pk-ok-3"}]}})
        assert req.url.params["label"] == "77"
        return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"ratingKey": "5"}, {"ratingKey": "9"}]}})

    p = client(tmp_path, handler)
    assert p.items_with_label("1", "pk-ok-3") == {5, 9}
    assert p.items_with_label("1", "pk-ok-4") == set()


def test_errors_never_include_urls_or_tokens(tmp_path):
    p = client(tmp_path, lambda req: httpx.Response(500))
    try:
        p.sessions()
    except Exception as e:  # PlexError
        assert "T0K" not in str(e) and "http" not in str(e)
    else:
        raise AssertionError("expected an error")


def test_bulk_label_keeps_other_labels(tmp_path):
    puts = []

    def handler(req):
        path, q = req.url.path, req.url.params
        if path == "/library/sections":
            return httpx.Response(200, json={"MediaContainer": {"Directory": [{"key": "1", "title": "Movies", "type": "movie"}]}})
        if path.endswith("/label"):
            return httpx.Response(200, json={"MediaContainer": {"Directory": [{"key": "9", "title": "owner-fav"}]}})
        if req.method == "GET" and q.get("label") == "9":
            return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"ratingKey": "2"}]}})
        if req.method == "PUT":
            puts.append(dict(q))
            return httpx.Response(200)
        return httpx.Response(404)

    p = client(tmp_path, handler)
    p.add_label_bulk("1", [1, 2, 3], "pk-ok-7")
    by_ids = {d["id"]: [v for k, v in sorted(d.items()) if k.startswith("label[")] for d in puts}
    assert by_ids == {"1,3": ["pk-ok-7"], "2": ["owner-fav", "pk-ok-7"]}  # 2 calls, not 3; owner's label kept
    assert all(d["type"] == "1" and d["label.locked"] == "1" for d in puts)


def test_update_share_uses_the_working_endpoint(tmp_path):
    seen = []

    def handler(req):
        if "/api/servers/" in req.url.path and req.method == "GET":
            return httpx.Response(200, text='<MediaContainer><Server machineIdentifier="M1"><Section id="101" key="1"/>'
                                             '<Section id="202" key="2"/></Server></MediaContainer>')
        seen.append((req.method, req.url.path, req.content))
        return httpx.Response(201, json={})

    p = client(tmp_path, handler)
    p.update_share(44, ["1", "2"], {"filterMovies": "label=pk-ok-1", "filterTelevision": "label=pk-ok-1"})
    method, path, body = seen[0]
    assert method == "POST" and path == "/api/v2/shared_servers/44"
    import json
    data = json.loads(body)
    assert data["librarySectionIds"] == [101, 202]
    assert data["settings"]["filterMovies"] == "label=pk-ok-1" and data["settings"]["allowSync"] is False


def test_history_is_cached_and_survives_a_tautulli_outage(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[General]\napi_key = K3Y\n")
    calls, down = [], []

    def handler(req):
        calls.append(req.url.path)
        if down:
            return httpx.Response(500)
        assert req.url.params["apikey"] == "K3Y"
        return httpx.Response(200, json={"response": {"data": {"data": [
            {"date": 1, "title": "Camping", "full_title": "Maple Club - Camping", "grandparent_title": "Maple Club",
             "media_type": "episode", "grandparent_rating_key": "12", "play_duration": 420, "player": "TV"}]}}})

    p = client(tmp_path, handler)
    p.cfg.tautulli_config = str(ini)
    first = p.history(5, 4)
    assert first[0]["name"] == "Maple Club" and first[0]["poster"] == 12
    assert p.history(5, 4) == first and len(calls) == 1  # second page load is served from memory
    key = "hist:5:4"
    p.cache._data[key] = (p.cache._data[key][0] - 120, first)  # a couple of minutes later...
    down.append(True)  # ...Tautulli is down: the last answer is shown instead of an error
    assert p.history(5, 4) == first and len(calls) == 2


def test_accept_skips_plex_home_invites(tmp_path):
    accepted = []

    def handler(req):
        if req.url.path == "/api/v2/user":
            return httpx.Response(200, json={"id": 1, "username": "owner", "email": "o@example.com"})
        if req.method == "GET" and req.url.path == "/api/invites/requests":
            return httpx.Response(200, content=(
                b'<MediaContainer><Invite id="1" username="owner" home="1" friend="0" server="0"/>'
                b'<Invite id="1" username="owner" home="0" friend="0" server="1"/></MediaContainer>'))
        if req.method == "PUT":
            accepted.append(dict(req.url.params))
            return httpx.Response(200, content=b"<MediaContainer/>")
        raise AssertionError(req.url)

    assert client(tmp_path, handler).accept_invite_from_owner("kid-token") is True
    assert accepted == [{"friend": "0", "home": "0", "server": "1"}]


def test_fresh_owner_bypasses_six_hour_cache_and_rotated_runtime_token(tmp_path):
    import os
    users=[]
    def handler(request):
        token=request.headers['X-Plex-Token']
        users.append(token)
        return httpx.Response(200,json={'id':1 if token=='T0K' else 2,'username':'owner','title':'Owner'})
    p=client(tmp_path,handler)
    assert p.owner().id==1
    assert p.owner().id==1 and len(users)==1
    prefs=tmp_path/'Preferences.xml'
    old=prefs.stat().st_mtime
    prefs.write_text('<Preferences PlexOnlineToken="NEW_FAKE_TOKEN" ProcessedMachineIdentifier="M1"/>')
    os.utime(prefs,(old+10,old+10))
    assert p.fresh_owner().id==2
    assert p.owner().id==2
    assert users==['T0K','NEW_FAKE_TOKEN']


def test_slow_old_owner_read_cannot_replace_new_cache(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    old_started,release=threading.Event(),threading.Event()
    hits=0
    def handler(request):
        nonlocal hits
        hits+=1
        which=hits
        if which==1:
            old_started.set()
            assert release.wait(5)
        return httpx.Response(200,json={'id':which,'username':'owner','title':'Owner'})
    p=client(tmp_path,handler)
    with ThreadPoolExecutor(2) as pool:
        old=pool.submit(p.fresh_owner)
        assert old_started.wait(5)
        new=pool.submit(p.fresh_owner)
        assert new.result(timeout=5).id==2
        release.set()
        assert old.result(timeout=5).id==2
    assert p.owner().id==2
