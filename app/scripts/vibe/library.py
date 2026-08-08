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
        # Removing a track from a playlist needs its setVideoId, which is
        # per-placement and only available from get_playlist. Captured here so
        # a future cleanup step doesn't require a second full fetch.
        "setVideoId": item.get("setVideoId"),
        "title": item.get("title"),
        "artist": ", ".join(a.get("name", "") for a in artists if a.get("name")),
        "duration_seconds": item.get("duration_seconds"),
    }


def _tracks(items):
    return [t for t in (_track(i) for i in items or []) if t]


class AuthError(RuntimeError):
    """Stored credentials exist but YouTube treats the session as signed out."""


def check_auth(ytmusic):
    """Fail fast when the stored cookies have been revoked.

    Revoked cookies don't produce an error — YouTube quietly serves the
    signed-out page, so get_library_playlists returns [] and the failure only
    surfaces later as an opaque KeyError from deep inside ytmusicapi.
    """
    try:
        account = ytmusic.get_account_info()
    except Exception as e:
        raise AuthError(
            "YouTube Music rejected the stored session - the cookies in "
            "headers_auth.json are expired or revoked.\n"
            "Re-authenticate: start the service "
            "(docker compose -f app/docker-compose.yml up -d), open "
            "http://localhost:8080, and paste fresh request headers from a "
            "signed-in music.youtube.com tab."
        ) from e
    print(f"Signed in as {account.get('accountName', '?')}")


def fetch_library():
    ytmusic = headers_to_ytmusic()
    check_auth(ytmusic)

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


def labelled_tracks(library, min_tracks=None, exclude=None):
    """Playlist membership -> single-label training rows.

    Tracks that sit in more than one playlist have an ambiguous label under a
    single-label model, so they're dropped and counted. Playlists below the
    minimum size are dropped too — they can't support a threshold you'd trust.

    Exclusions are applied here rather than at fetch time so library.json stays
    a complete snapshot and changing them costs nothing.

    Returns (rows, stats) where each row is the track dict plus "label".
    """
    if min_tracks is None:
        min_tracks = config.MIN_TRACKS_PER_PLAYLIST
    patterns = [p.lower() for p in
                (config.DEFAULT_EXCLUDE_PATTERNS if exclude is None else exclude)]

    def excluded(title):
        return any(p in title.lower() for p in patterns)

    candidates = [p for p in library["playlists"] if not excluded(p["title"])]
    by_pattern = [p["title"] for p in library["playlists"] if excluded(p["title"])]

    playlists = [p for p in candidates if len(p["tracks"]) >= min_tracks]
    too_small = [p["title"] for p in candidates if len(p["tracks"]) < min_tracks]

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
        "playlists_excluded": by_pattern,
        "playlists_too_small": too_small,
        "ambiguous_tracks": ambiguous,
        "total_labelled": len(rows),
    }
    return rows, stats
