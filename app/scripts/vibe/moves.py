"""Move one track between playlists: add to the target, then remove from the
source.

Add first on purpose. If the add fails the track stays where it was, which is
recoverable; removing first would risk losing the placement entirely.

Kept free of numpy/sklearn imports so the web app can call it without loading
the training stack.
"""


def playlist_ids(lib):
    return {p["title"]: p["id"] for p in lib.get("playlists", [])}


def set_video_id(lib, playlist, video_id):
    """The per-placement id YouTube needs for a removal, from the cached library."""
    for pl in lib.get("playlists", []):
        if pl.get("title") != playlist:
            continue
        for track in pl.get("tracks", []):
            if track.get("videoId") == video_id:
                return track.get("setVideoId")
    return None


def live_set_video_id(ytmusic, playlist_id, video_id):
    """The placement id straight from YouTube, for when the cached library is
    older than the placement (added since the last refresh, or moved here by
    an earlier UI move, which leaves setVideoId empty)."""
    for track in ytmusic.get_playlist(playlist_id, limit=None).get("tracks", []):
        if track.get("videoId") == video_id and track.get("setVideoId"):
            return track["setVideoId"]
    return None


def move_track(ytmusic, lib, video_id, source, target):
    """Raises ValueError when an id it needs can't be found, and passes
    through whatever ytmusicapi raises."""
    ids = playlist_ids(lib)
    target_id, source_id = ids.get(target), ids.get(source)
    if not target_id:
        raise ValueError(f"unknown target playlist {target!r}")
    if not source_id:
        raise ValueError(f"unknown source playlist {source!r}")
    placement = (set_video_id(lib, source, video_id)
                 or live_set_video_id(ytmusic, source_id, video_id))
    if not placement:
        raise ValueError(f"the track is not in {source!r} on YouTube Music")
    ytmusic.add_playlist_items(target_id, [video_id], duplicates=False)
    ytmusic.remove_playlist_items(source_id, [{"videoId": video_id,
                                               "setVideoId": placement}])


def relocate_in_library(lib, video_id, source, target):
    """Mirror a move in the cached library so the UI stays consistent until
    the next refresh. The new placement has no setVideoId yet."""
    moved = None
    for pl in lib.get("playlists", []):
        if pl.get("title") == source:
            keep = []
            for track in pl.get("tracks", []):
                if track.get("videoId") == video_id and moved is None:
                    moved = dict(track)
                else:
                    keep.append(track)
            pl["tracks"] = keep
    if moved is None:  # not in the cache (newer than it): record it in target anyway
        moved = {"videoId": video_id}
    moved["setVideoId"] = None
    for pl in lib.get("playlists", []):
        if pl.get("title") == target:
            if not any(t.get("videoId") == video_id for t in pl.get("tracks", [])):
                pl.setdefault("tracks", []).append(moved)
            return True
    return False
