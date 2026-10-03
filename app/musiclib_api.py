"""Library, streaming and artwork endpoints for the player."""

import mimetypes
from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file

from scripts.musiclib import extract_art

bp = Blueprint("library", __name__)

# slim images ship a minimal mime table; browsers need the right type to play audio
for _ext, _mime in ((".flac", "audio/flac"), (".opus", "audio/ogg"), (".ogg", "audio/ogg"),
                    (".m4a", "audio/mp4"), (".aac", "audio/aac"), (".wma", "audio/x-ms-wma")):
    mimetypes.add_type(_mime, _ext)


def _lib():
    return current_app.config["get_library"]()


def _int(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


@bp.route("/api/library/tracks")
def tracks():
    return jsonify(_lib().list_tracks(
        q=request.args.get("q", ""),
        artist=request.args.get("artist"),
        album=request.args.get("album"),
        album_artist=request.args.get("album_artist"),
        source=request.args.get("source"),
        credit=request.args.get("credit"),
        needs_tags=request.args.get("needs_tags") == "1",
        sort=request.args.get("sort", "artist"),
        direction=request.args.get("dir"),
        page=_int("page", 1),
        limit=_int("limit", 50),
    ))


@bp.route("/api/library/artists")
def artists():
    return jsonify(_lib().artists())


@bp.route("/api/library/albums")
def albums():
    return jsonify(_lib().albums(artist=request.args.get("artist")))


def _playlist_call(fn, *args):
    try:
        return fn(*args), None
    except ValueError as e:
        return None, (jsonify({"error": str(e)}), 400)
    except LookupError as e:
        return None, (jsonify({"error": str(e)}), 404)
    except PermissionError as e:
        return None, (jsonify({"error": str(e)}), 403)


@bp.route("/api/library/playlists", methods=["GET", "POST"])
def playlists():
    lib = _lib()
    if request.method == "GET":
        return jsonify(lib.playlists())
    pid, err = _playlist_call(lib.create_playlist, (request.get_json(silent=True) or {}).get("name"))
    if err:
        return err
    ids = (request.get_json(silent=True) or {}).get("track_ids") or []
    if ids:
        lib.add_to_playlist(pid, [i for i in ids if isinstance(i, int)])
    return jsonify({"id": pid}), 201


@bp.route("/api/library/playlists/<int:pid>", methods=["GET", "PATCH", "DELETE"])
def playlist(pid):
    lib = _lib()
    info = lib.get_playlist(pid)
    if info is None:
        return jsonify({"error": "No such playlist"}), 404
    if request.method == "GET":
        return jsonify({**info, "tracks": lib.playlist_tracks(pid)})
    if request.method == "PATCH":
        _, err = _playlist_call(lib.rename_playlist, pid, (request.get_json(silent=True) or {}).get("name"))
    else:
        _, err = _playlist_call(lib.delete_playlist, pid)
    return err or jsonify({"ok": True})


@bp.route("/api/library/playlists/<int:pid>/tracks", methods=["POST"])
def playlist_add(pid):
    ids = [i for i in ((request.get_json(silent=True) or {}).get("track_ids") or []) if isinstance(i, int)]
    n, err = _playlist_call(_lib().add_to_playlist, pid, ids)
    return err or jsonify({"added": n})


@bp.route("/api/library/playlists/<int:pid>/tracks/<int:track_id>", methods=["DELETE"])
def playlist_remove(pid, track_id):
    _, err = _playlist_call(_lib().remove_from_playlist, pid, track_id)
    return err or jsonify({"ok": True})


@bp.route("/api/library/roots")
def roots():
    return jsonify(_lib().roots_info())


@bp.route("/api/library/organize/preview")
def organize_preview():
    lib = _lib()
    return jsonify({**lib.organize_preview(request.args.get("source", "import")), "can_undo": lib.organize_can_undo()})


@bp.route("/api/library/organize/apply", methods=["POST"])
def organize_apply():
    return jsonify(_lib().organize_apply((request.get_json(silent=True) or {}).get("source", "import")))


@bp.route("/api/library/organize/undo", methods=["POST"])
def organize_undo():
    return jsonify(_lib().organize_undo())


@bp.route("/api/library/stats")
def stats():
    return jsonify(_lib().stats())


@bp.route("/api/library/rescan", methods=["POST"])
def rescan():
    started = _lib().scan_async()
    return jsonify({"started": started}), 202 if started else 200


@bp.route("/api/library/scan-status")
def scan_status():
    return jsonify(_lib().scan_status())


@bp.route("/api/stream/<int:track_id>")
def stream(track_id):
    lib = _lib()
    row = lib.get_track(track_id)
    if row is None:
        abort(404)
    path = lib.resolve(row)
    if path is None:
        abort(404)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    # conditional=True enables HTTP Range so the browser can seek
    return send_file(path, mimetype=mime, conditional=True)


@bp.route("/api/art/<int:track_id>")
def art(track_id):
    lib = _lib()
    row = lib.get_track(track_id)
    if row is None or not row["has_art"]:
        abort(404)
    path = lib.resolve(row)
    found = extract_art(path) if path else None
    if not found:
        abort(404)
    data, mime = found
    resp = Response(data, mimetype=mime)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp
