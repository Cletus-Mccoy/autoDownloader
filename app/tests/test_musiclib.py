"""Library index + streaming tests. Audio fixtures are tiny generated files."""
import os
import struct
import pytest

from scripts.musiclib import Library, read_tags


def _wav(path, seconds=1, rate=8000):
    n = rate * seconds
    data = b"\x00\x00" * n
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
           struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
           b"data" + struct.pack("<I", len(data)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hdr + data)


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "dl"
    _wav(root / "PL" / "Daft Punk - Around the World.wav")
    _wav(root / "PL" / "Boards of Canada - Roygbiv.wav", seconds=2)
    (root / "PL" / "notes.txt").write_text("ignore me")
    return Library(str(tmp_path / "data" / "library.db"), [(str(root), "ytmusic")]), root


def test_filename_fallback_tags(lib):
    _, root = lib
    t = read_tags(str(root / "PL" / "Daft Punk - Around the World.wav"))
    assert (t["artist"], t["title"]) == ("Daft Punk", "Around the World")


def test_scan_adds_updates_removes(lib):
    library, root = lib
    assert library.scan() == {"added": 2, "updated": 0, "removed": 0, "total": 2}
    assert library.scan()["added"] == 0                      # unchanged → skipped
    ids = {t["title"]: t["id"] for t in library.list_tracks()["tracks"]}
    os.remove(root / "PL" / "Boards of Canada - Roygbiv.wav")
    r = library.scan()
    assert r["removed"] == 1 and r["total"] == 1
    assert library.list_tracks()["tracks"][0]["id"] == ids["Around the World"]  # ids stable


def test_search_and_sort(lib):
    library, _ = lib
    library.scan()
    assert library.list_tracks(q="roygbiv")["total"] == 1
    assert library.list_tracks(q="daft around")["total"] == 1
    assert library.list_tracks(q="50%")["total"] == 0        # % is escaped, not a wildcard
    titles = [t["title"] for t in library.list_tracks(sort="title")["tracks"]]
    assert titles == sorted(titles, key=str.lower)


def test_artists_and_stats(lib):
    library, _ = lib
    library.scan()
    assert {a["name"] for a in library.artists()} == {"Daft Punk", "Boards of Canada"}
    assert library.stats()["tracks"] == 2


def test_resolve_blocks_traversal(lib):
    library, root = lib
    library.scan()
    row = dict(library.get_track(1))
    row["relpath"] = "../../etc/passwd"
    assert library.resolve(row) is None


# ── HTTP ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def lib_client(client, data_dir):
    dl = data_dir / "downloads"
    _wav(dl / "PL" / "Artist - Song.wav")
    import app as flask_module
    flask_module.get_library().scan()
    return client


def test_api_tracks_and_stream_range(lib_client):
    data = lib_client.get("/api/library/tracks").get_json()
    assert data["total"] >= 1
    tid = data["tracks"][0]["id"]
    full = lib_client.get(f"/api/stream/{tid}")
    assert full.status_code == 200
    part = lib_client.get(f"/api/stream/{tid}", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and len(part.data) == 10


def test_stream_unknown_id_404(lib_client):
    assert lib_client.get("/api/stream/999999").status_code == 404


def test_art_missing_404(lib_client):
    tid = lib_client.get("/api/library/tracks").get_json()["tracks"][0]["id"]
    assert lib_client.get(f"/api/art/{tid}").status_code == 404


def test_rescan_and_status(lib_client):
    assert lib_client.post("/api/library/rescan").status_code in (200, 202)
    assert "running" in lib_client.get("/api/library/scan-status").get_json()


def test_default_library_includes_music_root_when_present(tmp_path):
    from scripts.musiclib import default_library
    dl, music = tmp_path / "dl", tmp_path / "music"
    dl.mkdir(); music.mkdir()
    _wav(dl / "a - one.wav"); _wav(music / "b - two.wav")
    lib = default_library(str(tmp_path / "data"), str(dl), str(music))
    assert lib.scan()["total"] == 2
    # a second scan from any entry point must not drop the other root
    assert default_library(str(tmp_path / "data"), str(dl), str(music)).scan()["removed"] == 0


def test_flac_stream_mimetype(lib_client, data_dir):
    import musiclib_api  # noqa: F401  (registers audio mime types)
    import mimetypes
    assert mimetypes.guess_type("x.flac")[0] == "audio/flac"


# ── Playlists + sorting ──────────────────────────────────────────────────────

def test_folder_playlists_derived_from_download_folders(lib):
    library, root = lib
    library.scan()
    pls = library.playlists()
    assert [(p["name"], p["kind"], p["tracks"]) for p in pls] == [("PL", "folder", 2)]
    os.remove(root / "PL" / "Boards of Canada - Roygbiv.wav")
    library.scan()
    assert library.playlists()[0]["tracks"] == 1


def test_local_playlist_lifecycle_survives_organize_style_updates(lib):
    library, _ = lib
    library.scan()
    ids = [t["id"] for t in library.list_tracks()["tracks"]]
    pid = library.create_playlist("Mix")
    assert library.add_to_playlist(pid, ids + ids) == 2          # duplicates ignored
    assert [t["id"] for t in library.playlist_tracks(pid)] == ids
    library.remove_from_playlist(pid, ids[0])
    assert len(library.playlist_tracks(pid)) == 1
    library.scan()                                               # rescan must not touch local playlists
    assert len(library.playlist_tracks(pid)) == 1
    library.rename_playlist(pid, "Renamed")
    library.delete_playlist(pid)
    assert library.get_playlist(pid) is None


def test_folder_playlists_are_read_only(lib):
    library, _ = lib
    library.scan()
    folder_id = library.playlists()[0]["id"]
    with pytest.raises(PermissionError):
        library.delete_playlist(folder_id)


def test_sort_direction(lib):
    library, _ = lib
    library.scan()
    asc = [t["duration"] for t in library.list_tracks(sort="duration", direction="asc")["tracks"]]
    desc = [t["duration"] for t in library.list_tracks(sort="duration", direction="desc")["tracks"]]
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True) and asc != desc


def test_playlist_api(lib_client):
    r = lib_client.post("/api/library/playlists", json={"name": "Road trip"})
    assert r.status_code == 201
    pid = r.get_json()["id"]
    tid = lib_client.get("/api/library/tracks").get_json()["tracks"][0]["id"]
    assert lib_client.post(f"/api/library/playlists/{pid}/tracks", json={"track_ids": [tid]}).get_json()["added"] == 1
    assert len(lib_client.get(f"/api/library/playlists/{pid}").get_json()["tracks"]) == 1
    assert lib_client.post("/api/library/playlists", json={"name": " "}).status_code == 400
    folder = next(p for p in lib_client.get("/api/library/playlists").get_json() if p["kind"] == "folder")
    assert lib_client.delete(f"/api/library/playlists/{folder['id']}").status_code == 403
    assert lib_client.delete(f"/api/library/playlists/{pid}").status_code == 200
    assert lib_client.get(f"/api/library/playlists/{pid}").status_code == 404


# ── Starting points: YT-only, unsorted local, mixed ──────────────────────────

def _mixed(tmp_path):
    dl, music = tmp_path / "dl", tmp_path / "music"
    _wav(dl / "Road_Trip" / "Daft Punk - Around the World.wav")        # downloaded, untagged wav → filename guess
    _wav(music / "dump" / "random_file_01.wav")                         # unsorted, no artist anywhere
    _wav(music / "dump" / "Boards of Canada - Roygbiv.wav")
    (music / "dump" / "corrupt.mp3").write_bytes(b"not audio at all")  # unreadable but must not break the scan
    (music / "dump" / "cover.jpg").write_bytes(b"\xff\xd8")
    from scripts.musiclib import default_library
    return default_library(str(tmp_path / "data"), str(dl), str(music))


def test_mixed_sources_scan_without_errors(tmp_path):
    lib = _mixed(tmp_path)
    r = lib.scan()
    assert r["total"] == 4 and r["removed"] == 0         # corrupt mp3 indexed by filename, jpg ignored
    st = lib.stats()
    assert st["sources"] == {"ytmusic": 1, "import": 3}


def test_only_download_folders_become_playlists(tmp_path):
    lib = _mixed(tmp_path)
    lib.scan()
    assert [(p["name"], p["kind"]) for p in lib.playlists()] == [("Road Trip", "folder")]


def test_needs_tags_flags_unsorted_files(tmp_path):
    lib = _mixed(tmp_path)
    lib.scan()
    assert lib.stats()["needs_tags"] == 4                # generated wavs carry no tags at all
    assert lib.list_tracks(needs_tags=True)["total"] == 4
    names = {t["title"] for t in lib.list_tracks()["tracks"]}
    assert "random_file_01" in names                     # title falls back to the filename


def test_empty_library_is_fine(tmp_path):
    from scripts.musiclib import default_library
    lib = default_library(str(tmp_path / "data"), str(tmp_path / "nope"), str(tmp_path / "nomusic"))
    assert lib.scan()["total"] == 0
    assert lib.list_tracks()["tracks"] == [] and lib.playlists() == [] and lib.artists() == []


def test_yt_playlist_removed_when_folder_emptied_but_local_playlists_stay(tmp_path):
    lib = _mixed(tmp_path)
    lib.scan()
    pid = lib.create_playlist("Mine")
    lib.add_to_playlist(pid, [t["id"] for t in lib.list_tracks()["tracks"]][:2])
    import shutil
    shutil.rmtree(tmp_path / "dl" / "Road_Trip")
    lib.scan()
    kinds = {p["kind"] for p in lib.playlists()}
    assert kinds == {"local"}


# ── Organize (sorter) ────────────────────────────────────────────────────────
from scripts import musiclib as L


def _tagged_lib(tmp_path):
    """Two import-root files with real tags (via mutagen) in a messy layout, plus an untagged one."""
    from mutagen.wave import WAVE
    music = tmp_path / "music"
    for rel, tags in (("dump/a.wav", ("Song One", "Band", "Debut", "1")),
                      ("dump/deep/b.wav", ("Song Two", "Band", "Debut", "2")),
                      ("dump/c.wav", ("Single: Odd/Name?", "Solo", "", ""))):
        _wav(music / rel)
        w = WAVE(str(music / rel)); w.add_tags()
        from mutagen.id3 import TIT2, TPE1, TALB, TRCK
        w.tags.add(TIT2(encoding=3, text=tags[0])); w.tags.add(TPE1(encoding=3, text=tags[1]))
        if tags[2]: w.tags.add(TALB(encoding=3, text=tags[2]))
        if tags[3]: w.tags.add(TRCK(encoding=3, text=tags[3]))
        w.save()
    _wav(music / "dump" / "mystery.wav")                  # untagged: must be left alone
    lib = L.default_library(str(tmp_path / "data"), str(tmp_path / "dl"), str(music))
    lib.scan()
    return lib, music


def test_organize_preview_plans_and_skips_untagged(tmp_path):
    lib, music = _tagged_lib(tmp_path)
    plan = lib.organize_preview()
    targets = {m["from"]: m["to"] for m in plan["moves"]}
    assert targets["dump/a.wav"] == "Band/Debut/01 - Song One.wav"
    assert targets["dump/deep/b.wav"] == "Band/Debut/02 - Song Two.wav"
    assert targets["dump/c.wav"] == "Solo/Single_ Odd_Name_.wav"      # unsafe chars sanitised, no album → Artist/Title
    assert plan["untagged"] == 1 and not plan["conflicts"]
    assert (music / "dump" / "a.wav").exists()                         # preview touched nothing


def test_organize_apply_keeps_ids_and_playlists_then_undo(tmp_path):
    lib, music = _tagged_lib(tmp_path)
    ids = {t["relpath"]: t["id"] for t in lib.list_tracks()["tracks"]}
    pid = lib.create_playlist("Mine"); lib.add_to_playlist(pid, [ids["dump/a.wav"]])
    res = lib.organize_apply()
    assert res["moved"] == 3 and not res["errors"]
    assert (music / "Band" / "Debut" / "01 - Song One.wav").exists() and not (music / "dump" / "a.wav").exists()
    assert not (music / "dump" / "deep").exists()                      # emptied dir removed
    assert (music / "dump" / "mystery.wav").exists()                   # untagged untouched
    assert [t["id"] for t in lib.playlist_tracks(pid)] == [ids["dump/a.wav"]]   # same id, playlist intact
    assert lib.scan()["added"] == 0                                    # rescan sees moved files as known
    assert lib.organize_preview()["moves"] == []                       # idempotent
    undo = lib.organize_undo()
    assert undo["restored"] == 3 and (music / "dump" / "a.wav").exists()
    assert lib.get_track(ids["dump/a.wav"])["relpath"] == "dump/a.wav"


def test_organize_conflict_is_skipped_not_overwritten(tmp_path):
    lib, music = _tagged_lib(tmp_path)
    _wav(music / "Band" / "Debut" / "01 - Song One.wav")               # target already occupied
    lib.scan()
    plan = lib.organize_preview()
    assert any(c["from"] == "dump/a.wav" for c in plan["conflicts"])
    assert lib.organize_apply()["conflicts"] >= 1
    assert (music / "dump" / "a.wav").exists()


def test_roots_info(tmp_path):
    lib, _ = _tagged_lib(tmp_path)
    info = {r["source"]: r for r in lib.roots_info()}
    assert info["import"]["tracks"] == 4 and info["import"]["folders"][0]["name"] == "dump"
    assert info["ytmusic"]["exists"] is False


def test_media_endpoints(lib_client):
    assert lib_client.get("/api/library/roots").status_code == 200
    assert "moves" in lib_client.get("/api/library/organize/preview").get_json()
