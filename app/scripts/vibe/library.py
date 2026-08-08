"""Read the YouTube Music library: playlists, their tracks, and liked songs.

The result is cached to library.json so the rest of the pipeline can be re-run
without hitting YouTube again. Pass refresh=True to re-fetch.
"""

import datetime
import json
import os
import time

from ytmusic_auth import headers_to_ytmusic

from . import config


def _track(item):
    """Normalise a ytmusicapi track dict; returns None for unplayable rows."""
    video_id = item.get("videoId")
    if not video_id:
        return None
    artists = item.get("artists") or []
    return {
        "videoId": video_id,
        "title": item.get("title"),
        "artist": ", ".join(a.get("name", "") for a in artists if a.get("name")),
        "duration_seconds": item.get("duration_seconds"),
    }


def _tracks(items):
    return [t for t in (_track(i) for i in items or []) if t]


def fetch_library():
    ytmusic = headers_to_ytmusic()

    print("Fetching library playlists...")
    playlists = []
    for pl in ytmusic.get_library_playlists(limit=200):
        pid, title = pl.get("playlistId"), pl.get("title")
        if not pid or not title or title in config.EXCLUDED_TITLES:
            continue
        print(f"  {title} ...", end="", flush=True)
        try:
            detail = ytmusic.get_playlist(pid, limit=None)
        except Exception as e:
            print(f" failed ({e})")
            continue
        tracks = _tracks(detail.get("tracks"))
        print(f" {len(tracks)} tracks")
        playlists.append({"id": pid, "title": title, "tracks": tracks})
        time.sleep(0.5)

    print("Fetching liked songs...")
    liked = _tracks(ytmusic.get_liked_songs(limit=5000).get("tracks"))
    print(f"  {len(liked)} liked tracks")

    return {
        "fetched_at": datetime.datetime.utcnow().isoformat(),
        "playlists": playlists,
        "liked": liked,
    }


def load_library(refresh=False):
    if not refresh and os.path.exists(config.LIBRARY_FILE):
        with open(config.LIBRARY_FILE, encoding="utf-8") as f:
            library = json.load(f)
        print(f"Loaded cached library from {config.LIBRARY_FILE} "
              f"(fetched {library.get('fetched_at', '?')})")
        return library

    library = fetch_library()
    config.ensure_dirs()
    tmp = config.LIBRARY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)
    os.replace(tmp, config.LIBRARY_FILE)
    print(f"Saved library to {config.LIBRARY_FILE}")
    return library


def labelled_tracks(library, min_tracks=None):
    """Playlist membership -> single-label training rows.

    Tracks that sit in more than one playlist have an ambiguous label under a
    single-label model, so they're dropped and counted. Playlists below the
    minimum size are dropped too — they can't support a threshold you'd trust.

    Returns (rows, stats) where each row is the track dict plus "label".
    """
    if min_tracks is None:
        min_tracks = config.MIN_TRACKS_PER_PLAYLIST

    playlists = [p for p in library["playlists"] if len(p["tracks"]) >= min_tracks]
    too_small = [p["title"] for p in library["playlists"] if len(p["tracks"]) < min_tracks]

    memberships = {}
    for pl in playlists:
        for track in pl["tracks"]:
            memberships.setdefault(track["videoId"], []).append((pl["title"], track))

    rows, ambiguous = [], []
    for video_id, entries in memberships.items():
        titles = {title for title, _ in entries}
        if len(titles) > 1:
            ambiguous.append((video_id, sorted(titles)))
            continue
        title, track = entries[0]
        rows.append({**track, "label": title})

    stats = {
        "playlists_kept": [p["title"] for p in playlists],
        "playlists_too_small": too_small,
        "ambiguous_tracks": ambiguous,
        "total_labelled": len(rows),
    }
    return rows, stats
