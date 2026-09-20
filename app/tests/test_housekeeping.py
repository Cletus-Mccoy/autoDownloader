"""Housekeeping: relocate what moved, quarantine only what is provably spare,
and never touch a download that might be the last copy of the track."""
import json
import os

import pytest

import housekeeping as hk

KEEP = "aaaaaaaaaaa"      # still in 28. UNSORTED TECH
MOVED = "bbbbbbbbbbb"     # now lives in 34. BOUNCE, not downloaded there
SPARE = "ccccccccccc"     # now lives in 34. BOUNCE, already downloaded there
DEDUPED = "ddddddddddd"   # in no playlist; our ledger removed it
GONE = "eeeeeeeeeee"      # in no playlist; nothing explains it — a takedown
WANTED = "fffffffffff"    # in the playlist, in the archive, file missing


def _write(path, size=16):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * size)


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    base = tmp_path / "downloads"
    layout = {
        "28_UNSORTED_TECH/keep.mp3": KEEP,
        "28_UNSORTED_TECH/copy_of_keep.mp3": KEEP,
        "28_UNSORTED_TECH/moved_to_bounce.mp3": MOVED,
        "28_UNSORTED_TECH/already_in_bounce.mp3": SPARE,
        "28_UNSORTED_TECH/deduped.mp3": DEDUPED,
        "28_UNSORTED_TECH/taken_down.mp3": GONE,
        "34_BOUNCE/already_in_bounce.mp3": SPARE,
        "2025_Recap/whatever.mp3": "ggggggggggg",
    }
    for rel in layout:
        # keep.mp3 is the fuller copy, so it is the one that survives
        _write(str(base / rel), size=64 if rel.endswith("/keep.mp3") else 16)
    (base / "28_UNSORTED_TECH" / hk.ARCHIVE).write_text(
        "".join(f"youtube {v}\n" for v in
                (KEEP, MOVED, SPARE, DEDUPED, GONE, WANTED)))
    (base / "34_BOUNCE" / hk.ARCHIVE).write_text(f"youtube {SPARE}\n")
    monkeypatch.setattr(hk, "BASE_DIR", str(base))
    monkeypatch.setattr(hk, "QUARANTINE", str(base / ".orphans"))
    monkeypatch.setattr(
        hk, "video_id",
        lambda p: layout.get(os.path.relpath(p, str(base)).replace(os.sep, "/")))
    return base


def _library():
    return {"fetched_at": "t", "playlists": [
        {"title": "28. UNSORTED TECH",
         "tracks": [{"videoId": KEEP}, {"videoId": WANTED}]},
        {"title": "34. BOUNCE",
         "tracks": [{"videoId": MOVED}, {"videoId": SPARE}]},
    ]}


def _scan(base):
    return hk.scan(_library(), base=str(base), removed_ids={DEDUPED})


def test_the_bigger_copy_of_a_double_is_the_one_kept(downloads):
    p = next(x for x in _scan(downloads)["playlists"]
             if x["folder"] == "28_UNSORTED_TECH")
    assert [f["file"] for f in p["double"]] == ["copy_of_keep.mp3"]


def test_scan_classifies_every_case(downloads):
    p = next(x for x in _scan(downloads)["playlists"]
             if x["folder"] == "28_UNSORTED_TECH")
    assert [f["file"] for f in p["relocate"]] == ["moved_to_bounce.mp3"]
    assert p["relocate"][0]["to"] == "34_BOUNCE"
    assert [f["file"] for f in p["spare"]] == ["already_in_bounce.mp3"]
    assert [f["file"] for f in p["double"]] == ["copy_of_keep.mp3"]
    assert [f["file"] for f in p["removed"]] == ["deduped.mp3"]
    assert [f["file"] for f in p["unexplained"]] == ["taken_down.mp3"]
    assert p["missing"] == [WANTED]


def test_a_track_gone_from_youtube_is_never_touched(downloads):
    """The whole point of downloading: if YouTube dropped it and nothing
    explains the removal, the file stays put."""
    report = _scan(downloads)
    hk.apply(report, base=str(downloads))

    assert (downloads / "28_UNSORTED_TECH" / "taken_down.mp3").exists()
    # and its archive line survives, so nothing re-fetches a dead video
    assert GONE in hk.read_archive(
        str(downloads / "28_UNSORTED_TECH" / hk.ARCHIVE))


def test_unexplained_can_be_swept_on_request(downloads):
    report = _scan(downloads)
    hk.apply(report, base=str(downloads), include_unexplained=True)
    assert not (downloads / "28_UNSORTED_TECH" / "taken_down.mp3").exists()


def test_relocate_moves_the_file_and_fixes_both_archives(downloads):
    report = _scan(downloads)
    relocated, _, _ = hk.apply(report, base=str(downloads))

    assert relocated == 1
    assert not (downloads / "28_UNSORTED_TECH" / "moved_to_bounce.mp3").exists()
    assert (downloads / "34_BOUNCE" / "moved_to_bounce.mp3").exists()
    src = hk.read_archive(str(downloads / "28_UNSORTED_TECH" / hk.ARCHIVE))
    dst = hk.read_archive(str(downloads / "34_BOUNCE" / hk.ARCHIVE))
    assert MOVED not in src, "source would fetch it again"
    assert MOVED in dst, "destination would fetch a file it already has"


def test_spare_and_double_and_removed_are_quarantined(downloads):
    report = _scan(downloads)
    _, quarantined, dest = hk.apply(report, base=str(downloads))

    assert quarantined == 3
    for name in ("already_in_bounce.mp3", "copy_of_keep.mp3", "deduped.mp3"):
        assert os.path.exists(os.path.join(dest, "28_UNSORTED_TECH", name))
        assert not (downloads / "28_UNSORTED_TECH" / name).exists()
    assert (downloads / "28_UNSORTED_TECH" / "keep.mp3").exists()

    archive = hk.read_archive(str(downloads / "28_UNSORTED_TECH" / hk.ARCHIVE))
    assert KEEP in archive, "the kept copy must not be re-downloaded"
    assert SPARE not in archive and DEDUPED not in archive
    assert WANTED not in archive, "missing file must be fetched again"
    kinds = {e["kind"] for e in json.load(
        open(os.path.join(dest, "housekeeping.json")))}
    assert kinds == {"relocate", "spare", "double", "removed"}


def test_ghost_folders_need_the_flag(downloads):
    hk.apply(_scan(downloads), base=str(downloads))
    assert (downloads / "2025_Recap").exists()
    _, _, dest = hk.apply(_scan(downloads), base=str(downloads),
                          include_ghosts=True)
    assert not (downloads / "2025_Recap").exists()
    assert os.path.isdir(os.path.join(dest, "2025_Recap"))


def test_unreadable_files_are_left_alone(downloads, monkeypatch):
    monkeypatch.setattr(hk, "video_id", lambda p: None)
    report = hk.scan(_library(), base=str(downloads), removed_ids=set())
    assert report["unreadable"] == 8
    assert all(not p["spare"] and not p["relocate"]
               for p in report["playlists"])
    assert (downloads / "28_UNSORTED_TECH" / "moved_to_bounce.mp3").exists()


def test_quarantine_folder_is_not_rescanned(downloads):
    _write(str(downloads / ".orphans" / "old" / "x.mp3"))
    assert not any(g["folder"] == ".orphans"
                   for g in _scan(downloads)["ghosts"])


def test_applying_twice_is_a_no_op(downloads):
    hk.apply(_scan(downloads), base=str(downloads))
    relocated, quarantined, _ = hk.apply(_scan(downloads), base=str(downloads))
    assert (relocated, quarantined) == (0, 0)


def test_folder_name_matches_the_downloader():
    from download import folder_name
    assert folder_name("28. UNSORTED TECH") == "28_UNSORTED_TECH"
    assert folder_name("72. RNB / CHANSON / SWING / RAGGA") == "72_RNB_CHANSON_SWING_RAGGA"
    assert folder_name("80. Feeling Good! 🙃") == "80_Feeling_Good"
    assert folder_name("82. LO-FI (memories of you)") == "82_LO-FI_memories_of_you"
