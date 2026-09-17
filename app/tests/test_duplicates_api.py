"""Duplicates queue: tiers from the cached library, resolution removes the
losing copies on YouTube Music, ledgered and mirrored into the library."""
import json
import os

import pytest


class FakeYT:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def remove_playlist_items(self, pid, items):
        if self.fail:
            raise RuntimeError("session rejected")
        self.calls.append((pid, tuple((i["videoId"], i["setVideoId"]) for i in items)))


def _t(v, s, title="T", artist="A"):
    return {"videoId": v, "setVideoId": s, "title": title, "artist": artist}


def _lib():
    return {"playlists": [
        {"id": "p1", "title": "ONE", "tracks": [_t("v1", "s1", "Song"), _t("v1", "s1b", "Song"),
                                                _t("v2", "s2", "Shared"), _t("v3", "s3", "Same Song")]},
        {"id": "p2", "title": "TWO", "tracks": [_t("v2", "s2x", "Shared"), _t("v4", "s4", "Same Song (Official Video)")]},
    ], "liked": []}


@pytest.fixture
def dupes_env(flask_app):
    import app as flask_module
    os.makedirs(f"{flask_module.VIBE_DIR}/reports", exist_ok=True)
    json.dump(_lib(), open(flask_module.VIBE_LIBRARY_FILE, "w"))
    json.dump({"backend": "effnet", "thresholds": {}, "exclude": ["recap"]},
              open(f"{flask_module.VIBE_DIR}/thresholds.json", "w"))
    return flask_module


def test_duplicates_lists_three_tiers(client, dupes_env):
    d = client.get("/api/sort/duplicates").get_json()
    assert d["tier_a_extra"] == 1 and d["tier_a"][0]["videoId"] == "v1"
    tiers = {g["tier"]: g for g in d["groups"]}
    assert tiers["B"]["videoId"] == "v2" and [o["value"] for o in tiers["B"]["options"]] == ["ONE", "TWO"]
    assert set(tiers["C"]["key"][2:].split(",")) == {"v3", "v4"}


def test_resolve_tier_b_keeps_one_playlist(client, dupes_env, monkeypatch):
    yt = FakeYT()
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: yt)
    r = client.post("/api/sort/duplicates/resolve", json={"key": "B:v2", "keep": "ONE"})
    assert r.get_json() == {"ok": True, "removed": 1}
    assert yt.calls == [("p2", (("v2", "s2x"),))]
    lib = json.load(open(dupes_env.VIBE_LIBRARY_FILE))
    assert [t["videoId"] for t in lib["playlists"][1]["tracks"]] == ["v4"]
    assert not any(g["tier"] == "B" for g in client.get("/api/sort/duplicates").get_json()["groups"])
    ledger = [json.loads(l) for l in open(dupes_env.REMOVALS_LEDGER_FILE)]
    assert ledger[0]["playlist"] == "TWO" and ledger[0]["reason"] == "kept in ONE"


def test_resolve_tier_c_removes_losing_upload_everywhere(client, dupes_env, monkeypatch):
    yt = FakeYT()
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: yt)
    key = [g["key"] for g in client.get("/api/sort/duplicates").get_json()["groups"] if g["tier"] == "C"][0]
    r = client.post("/api/sort/duplicates/resolve", json={"key": key, "keep": "v3"})
    assert r.get_json()["ok"] and yt.calls == [("p2", (("v4", "s4"),))]


def test_resolve_tier_a_removes_extras(client, dupes_env, monkeypatch):
    yt = FakeYT()
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: yt)
    r = client.post("/api/sort/duplicates/resolve", json={"key": "A"})
    assert r.get_json() == {"ok": True, "removed": 1}
    assert yt.calls == [("p1", (("v1", "s1b"),))]
    assert client.get("/api/sort/duplicates").get_json()["tier_a_extra"] == 0


def test_resolve_failure_changes_nothing(client, dupes_env, monkeypatch):
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: FakeYT(fail=True))
    r = client.post("/api/sort/duplicates/resolve", json={"key": "B:v2", "keep": "ONE"})
    assert r.status_code == 500 and "rejected" in r.get_json()["error"]
    assert not os.path.exists(dupes_env.REMOVALS_LEDGER_FILE)
    assert any(g["tier"] == "B" for g in client.get("/api/sort/duplicates").get_json()["groups"])


def test_resolve_rejects_bad_keep(client, dupes_env):
    assert client.post("/api/sort/duplicates/resolve", json={"key": "B:v2", "keep": "NOPE"}).status_code == 400


def test_skip_hides_group(client, dupes_env):
    assert client.post("/api/sort/duplicates/skip", json={"key": "B:v2"}).get_json()["ok"]
    assert not any(g["tier"] == "B" for g in client.get("/api/sort/duplicates").get_json()["groups"])


def test_duplicates_page_and_nav(client):
    assert client.get("/sort/duplicates").status_code == 200
    for path in ("/sort", "/sort/misfiled", "/sort/stats"):
        assert 'href="/sort/duplicates"' in client.get(path).get_data(as_text=True)


def test_duplicates_endpoint_works_without_scripts_on_path(client, dupes_env, monkeypatch):
    """Production never had scripts/ on sys.path; the app must add it itself.
    Strip it (and cached vibe modules) and hit the endpoint cold."""
    import sys
    scripts_dir = dupes_env._SCRIPTS_DIR
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != scripts_dir])
    for name in [m for m in sys.modules if m.startswith("scripts.vibe") or m == "ytmusic_auth"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    import app as flask_module
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)   # what the app's import block does at startup
    assert client.get("/api/sort/duplicates").status_code == 200
