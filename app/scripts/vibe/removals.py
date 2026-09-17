"""Remove specific placements from playlists, and mirror that in the cached
library. numpy-free so the web app can import it.

A removal is {"playlist", "videoId", "setVideoId"}; setVideoId is the
per-placement id YouTube needs, captured by the library fetch."""


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
