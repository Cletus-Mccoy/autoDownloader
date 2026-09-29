"""Remove specific placements from playlists, and mirror that in the cached
library. numpy-free so the web app can import it.

A removal is {"playlist", "videoId", "setVideoId"}; setVideoId is the
per-placement id YouTube needs, captured by the library fetch."""

import json


def remove_placements(ytmusic, lib, removals):
    """Group by playlist and remove. Returns (done, failed, errors)."""
    ids = {p["title"]: p["id"] for p in lib.get("playlists", [])}
    by_playlist = {}
    for r in removals:
        by_playlist.setdefault(r["playlist"], []).append(r)
    done, failed, errors = 0, 0, []
    for playlist, items in by_playlist.items():
        playlist_id = ids.get(playlist)
        videos = [{"videoId": i["videoId"], "setVideoId": i["setVideoId"]}
                  for i in items if i.get("setVideoId")]
        if not playlist_id or len(videos) != len(items):
            failed += len(items)
            errors.append(f"{playlist}: missing playlistId or setVideoId")
            continue
        try:
            ytmusic.remove_playlist_items(playlist_id, videos)
        except Exception as e:  # noqa: BLE001 - surfaced to the caller
            failed += len(items)
            errors.append(f"{playlist}: {e}")
            continue
        done += len(items)
        drop_from_library(lib, playlist, videos)
    return done, failed, errors


def drop_from_library(lib, playlist, videos):
    gone = {(v["videoId"], v["setVideoId"]) for v in videos}
    for pl in lib.get("playlists", []):
        if pl.get("title") != playlist:
            continue
        pl["tracks"] = [t for t in pl.get("tracks", [])
                        if (t.get("videoId"), t.get("setVideoId")) not in gone]


# ---------------------------------------------------------------------------
# What dedupe took out, so nothing puts it back.
#
# Resolving a tier-C group removes the losing upload from every playlist, which
# leaves it liked but "unsorted" — exactly what the nightly route step looks
# for. Without this it re-filed wT6KJKaE1xM into the playlist it had been
# removed from the night after (card #596). Tiers A and B leave the video in
# the playlist that was kept, so they never look unsorted, but their removed
# placements are blocked too for when that changes.

DEDUPE_TIERS = ("A", "B", "C")


def dedupe_blocks(ledger_path, decisions_path=None):
    """Read what dedupe removed. Returns (dropped, pairs):

      dropped  videoIds removed as the losing copy of a tier-C group — a second
               upload of a song that is kept under another id, so it belongs
               in no playlist and not in the review queue either
      pairs    {(playlist, videoId)} placements any dedupe tier removed

    The ledger (reports/removals.jsonl, written by the UI and by cleanup.py)
    is the primary record; dupes_decisions.json adds tier-C losers in case a
    ledger line is missing. Missing or unreadable files block nothing.
    """
    dropped, pairs = set(), set()
    try:
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                tier, video_id = r.get("tier"), r.get("videoId")
                if tier not in DEDUPE_TIERS or not video_id:
                    continue
                if r.get("playlist"):
                    pairs.add((r["playlist"], video_id))
                if tier == "C":
                    dropped.add(video_id)
    except OSError:
        pass

    if decisions_path:
        try:
            with open(decisions_path, encoding="utf-8") as f:
                decisions = json.load(f)
        except (OSError, ValueError):
            decisions = {}
        for key, d in decisions.items():
            if key.startswith("C:") and d.get("action") == "resolve" and d.get("keep"):
                dropped.update(v for v in key[2:].split(",") if v != d["keep"])
    return dropped, pairs
