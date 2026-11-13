"""Moving a downloaded track to another YouTube Music playlist from the player:
the YouTube move, its records, and the file following it into the new folder."""
import json
import os
import shutil
import struct

import pytest

from tests.test_remodel_api import FakeYT

SRC, DST = "28. 240 SOUND", "28. BOUNCE"
VID = "v1aaaaaaaaa"


def _wav(path):
    data = b"\x00\x00" * 800
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
           struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16) +
           b"data" + struct.pack("<I", len(data)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(hdr + data)


@pytest.fixture
def player_env(flask_app, data_dir, monkeypatch):
    import app as flask_module
    import scripts.housekeeping as hk
    from scripts.download import folder_name
    dl = data_dir / "downloads"
    src, dst = dl / folder_name(SRC), dl / folder_name(DST)
    _wav(str(src / "A - T1.wav"))
    _wav(str(src / "B - untagged.wav"))
    (src / "downloaded.txt").write_text(f"youtube {VID}\nyoutube v2\n")
    # The files carry their watch URL in real downloads; here a lookup by name.
    monkeypatch.setattr(hk, "video_id",
                        lambda p: VID if os.path.basename(p) == "A - T1.wav" else None)
    os.makedirs(os.path.dirname(flask_module.MOVES_LEDGER_FILE), exist_ok=True)
    json.dump({"playlists": [
        {"id": "p240", "title": SRC, "tracks": [{"videoId": VID, "setVideoId": "s1"}]},
        {"id": "pb", "title": DST, "tracks": []},
        {"id": "pr", "title": "2025 Recap", "tracks": []},
    ]}, open(flask_module.VIBE_LIBRARY_FILE, "w"))
    yt = FakeYT()
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: yt)
    from scripts.musiclib import Library
    monkeypatch.setattr(Library, "scan_async", lambda self: False)  # no thread racing teardown
    library = flask_module.get_library()
    library.scan()
    ids = {t["title"]: t["id"] for t in library.list_tracks(limit=50)["tracks"]}
    yield flask_module, yt, ids, src, dst
    shutil.rmtree(src, ignore_errors=True)   # data_dir is shared by the session
    shutil.rmtree(dst, ignore_errors=True)
    library.scan()


def test_youtube_info_names_the_playlists(client, player_env):
    _, _, ids, _, _ = player_env
    d = client.get(f"/api/library/tracks/{ids['T1']}/youtube").get_json()
    assert d["videoId"] == VID
    assert d["playlists"] == [SRC] and d["folder_playlist"] == SRC
    assert DST in d["all"] and "2025 Recap" not in d["all"]   # sorter excludes


def test_move_from_the_player_moves_on_youtube_and_on_disk(client, player_env):
    flask_module, yt, ids, src, dst = player_env
    r = client.post(f"/api/library/tracks/{ids['T1']}/move", json={"target": DST})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["from"] == SRC and d["relpath"].endswith("/A - T1.wav")
    assert yt.calls == [("add", "pb", (VID,)), ("remove", "p240", ((VID, "s1"),))]
    assert (dst / "A - T1.wav").exists() and not (src / "A - T1.wav").exists()
    row = flask_module.get_library().get_track(ids["T1"])   # same id: a playing track keeps playing
    assert row["relpath"] == d["relpath"] and client.get(f"/api/stream/{ids['T1']}").status_code == 200
    assert "youtube v1aaaaaaaaa" not in (src / "downloaded.txt").read_text()
    assert "youtube v2" in (src / "downloaded.txt").read_text()
    assert f"youtube {VID}" in (dst / "downloaded.txt").read_text()
    ledger = [json.loads(l) for l in open(flask_module.MOVES_LEDGER_FILE)]
    assert ledger[-1]["videoId"] == VID and ledger[-1]["target"] == DST
    lib = json.load(open(flask_module.VIBE_LIBRARY_FILE))
    assert [t["videoId"] for t in lib["playlists"][1]["tracks"]] == [VID]


def test_move_to_where_it_already_is_is_refused(client, player_env):
    _, yt, ids, _, _ = player_env
    r = client.post(f"/api/library/tracks/{ids['T1']}/move", json={"target": SRC})
    assert r.status_code == 400 and yt.calls == []


def test_a_file_without_a_youtube_id_cannot_be_moved(client, player_env):
    _, yt, ids, _, _ = player_env
    r = client.post(f"/api/library/tracks/{ids['untagged']}/move", json={"target": DST})
    assert r.status_code == 400 and yt.calls == []


def test_failed_youtube_move_leaves_the_file(client, player_env, monkeypatch):
    _, _, ids, src, _ = player_env
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: FakeYT(fail=True))
    r = client.post(f"/api/library/tracks/{ids['T1']}/move", json={"target": DST})
    assert r.status_code == 500
    assert (src / "A - T1.wav").exists()
