from flask import Flask, jsonify, send_from_directory, request, redirect, Response, render_template
import os
import json
import shutil
import threading
import subprocess
import datetime
import re
from croniter import croniter
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen._file import File as MutagenFile
from scripts.timer import get_next_run_safe
from scripts.runner import run_scheduler_stream
from scripts.scheduler import log_run as _cron_log_run

app = Flask(__name__)

DATA_DIR = "/app/data"
RUNS_FILE = f"{DATA_DIR}/runs.json"
DOWNLOAD_DIR = "/app/downloads"
LOG_DIR = f"{DATA_DIR}/logs"
AUTH_DIR = f"{DATA_DIR}/auth"
HEADERS_AUTH_FILE = f"{AUTH_DIR}/headers_auth.json"
OAUTH_FILE = f"{AUTH_DIR}/oauth.json"
OAUTH_CLIENT_FILE = f"{AUTH_DIR}/oauth_client.json"
CRON_FILE        = "/etc/cron.d/ytmusic"
# Log to the bind mount, not /var/log: that path is in the writable layer
# too, so a recreate destroys the only record that the job ever ran. An
# absent cron.log then looks identical to "never fired".
CRON_SUFFIX      = ("root python /app/scripts/scheduler.py "
                    ">> /app/data/logs/cron.log 2>&1")
# The schedule's home is here, on the bind mount. /etc/cron.d lives in the
# container's writable layer, so every `docker compose up` that recreates the
# container silently discards it — the schedule vanishes, cron has nothing to
# fire, and the UI shows an empty box because it reads the file back. That is
# exactly how three deploys in one day left the 03:00 job never running.
SCHEDULE_FILE    = f"{DATA_DIR}/schedule.json"
DEFAULT_CRON     = "0 3 * * *"
SELECTION_FILE   = f"{DATA_DIR}/playlist_selection.json"
VIBE_DIR         = f"{DATA_DIR}/vibe"
SORT_QUEUE_FILE  = f"{VIBE_DIR}/sort_queue.json"
VIBE_LIBRARY_FILE = f"{VIBE_DIR}/library.json"
DECISIONS_FILE   = f"{VIBE_DIR}/reports/decisions.jsonl"
MUSIC_EXTENSIONS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".wma"}
UNSUPPORTED_TITLES = {"Liked Music", "Episodes for Later"}


def _persist_run(status, log_file, trigger="manual"):
    """Write a run entry to runs.json using an atomic temp-file rename.

    Avoids partial-write corruption and sidesteps Windows/WSL2 in-place
    file-overwrite locking that silently drops writes on bind mounts.
    """
    run = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "status": status,
        "trigger": trigger,
        "log": log_file,
    }
    try:
        with open(RUNS_FILE) as f:
            runs = json.load(f)
    except Exception:
        runs = []
    runs.insert(0, run)
    tmp = RUNS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(runs[:50], f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RUNS_FILE)


from threading import Lock
run_lock = Lock()
run_thread = None
run_active = False
_current_proc = None


def load_runs():
    if not os.path.exists(RUNS_FILE):
        return []
    try:
        with open(RUNS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return []


@app.route("/")
def index():
    runs = load_runs()
    next_run, delta = get_next_run_safe()

    for run in runs:
        run["log_name"] = os.path.basename(run["log"])

    return render_template(
        "index.html",
        runs=runs[:10],
        next_run=next_run,
        delta=delta,
        downloads=get_files(),
        download_size=get_download_size()
    )


def _is_music(filename):
    return os.path.splitext(filename)[1].lower() in MUSIC_EXTENSIONS


def get_files():
    result = []
    for root, _, files in os.walk(DOWNLOAD_DIR):
        for f in files:
            if _is_music(f):
                result.append(os.path.join(root, f).replace(DOWNLOAD_DIR.rstrip("/") + "/", ""))
    return result


def get_download_size():
    total = 0
    for root, _, files in os.walk(DOWNLOAD_DIR):
        for f in files:
            if not _is_music(f):
                continue
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    if total == 0:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if total < 1024:
            return f"{total:.1f} {unit}"
        total /= 1024
    return f"{total:.2f} TB"

@app.route("/api/status")
def api_status():
    files = get_files()
    runs = load_runs()
    last_run = runs[0] if runs else None
    next_run, delta = get_next_run_safe()
    global run_thread
    return jsonify({
        "tracks": len(files),
        "size": get_download_size(),
        "last_run": last_run["status"] if last_run else "never",
        "next_run": delta or next_run,
        "running": run_thread is not None and run_thread.is_alive(),
    })

@app.route("/run-stream")
def run_stream():
    return Response(run_scheduler_stream(), mimetype="text/event-stream")


def run_target():
    global run_active, _current_proc
    run_active = True
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_file = f"{LOG_DIR}/run_{ts}.log"
    status = "failed"
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(log_file, "w") as lf:
            _current_proc = subprocess.Popen(
                ["python3", "/app/scripts/download.py"],
                stdout=lf, stderr=lf
            )
            _current_proc.wait()
        rc = _current_proc.returncode
        if rc is not None and rc < 0:
            status = "stopped"
        elif rc == 0:
            status = "success"
    except Exception as e:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(log_file, "a") as lf:
                lf.write(f"\n[runner error: {e}]\n")
        except Exception:
            pass
    finally:
        try:
            _persist_run(status, log_file, trigger="manual")
        except Exception as persist_err:
            try:
                with open(log_file, "a") as lf:
                    lf.write(f"\n[_persist_run failed: {persist_err}]\n")
            except Exception:
                pass
        _current_proc = None
        run_active = False

@app.route("/run-now", methods=["POST"])
def run_now():
    global run_thread
    if run_lock.locked() or (run_thread and run_thread.is_alive()):
        return redirect("/")
    run_thread = threading.Thread(target=run_target, daemon=True)
    run_lock.acquire()
    try:
        run_thread.start()
    finally:
        run_lock.release()
    return redirect("/")

@app.route("/stop-now", methods=["POST"])
def stop_now():
    global run_active, _current_proc
    run_active = False
    if _current_proc and _current_proc.poll() is None:
        _current_proc.terminate()
    return redirect("/")

@app.route("/download-status", methods=["GET"])
def download_status():
    global run_thread
    if run_thread and run_thread.is_alive():
        return jsonify({"status": "running"})
    return jsonify({"status": "idle"})
    
 
@app.route("/clear-runs", methods=["POST"])
def clear_runs():
    with open(RUNS_FILE, "w") as f:
        json.dump([], f)
    return redirect("/")

@app.route("/clear-logs", methods=["POST"])
def clear_logs():
    for f in os.listdir(LOG_DIR):
        os.remove(os.path.join(LOG_DIR, f))
    return redirect("/")

@app.route("/clear-downloads", methods=["POST"])
def clear_downloads():
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    return redirect("/")

@app.route("/auth/status")
def auth_status():
    from scripts.ytmusic_auth import has_oauth, has_headers, headers_to_ytmusic
    # Match headers_to_ytmusic()'s precedence: headers win when both present.
    method = "headers" if has_headers() else ("oauth" if has_oauth() else None)
    if method is None:
        return jsonify({"authenticated": False, "reason": "no_credentials", "method": None})
    try:
        ytmusic = headers_to_ytmusic()
        # Revoked cookies don't raise: YouTube serves the signed-out page and
        # get_library_playlists() just returns [], which read as "authenticated".
        # get_account_info() fails loudly when signed out, so it's the only
        # reliable check.
        ytmusic.get_account_info()
        return jsonify({"authenticated": True, "method": method})
    except Exception:
        return jsonify({"authenticated": False, "reason": "expired", "method": method})


_HEADER_NAME_RE = re.compile(r"^:?[a-z][a-z0-9\-]*$")


def _normalize_headers_raw(raw: str) -> str:
    """Convert Chrome DevTools alternating name/value lines to 'Name: Value' format.

    Chrome DevTools "Copy request headers" gives lines like:
        header-name
        header-value
        ...
    but also injects a multi-line "Decoded:" block after x-client-data that
    shifts the naive i+=2 pairing. We detect genuine header names with a regex
    (lowercase letters/digits/hyphens only) and skip any lines that don't match,
    so the Decoded block is transparently ignored.

    If the input already contains ': ' separators it is returned unchanged.
    HTTP/2 pseudo-headers (:authority, :method, …) are dropped.
    """
    lines = [l for l in raw.splitlines() if l.strip()]
    if any(": " in line for line in lines):
        return raw
    result = []
    i = 0
    while i < len(lines):
        name = lines[i].strip()
        if _HEADER_NAME_RE.match(name) and i + 1 < len(lines):
            value = lines[i + 1].strip()
            if not name.startswith(":"):
                result.append(f"{name}: {value}")
            i += 2
        else:
            i += 1
    return "\n".join(result)


@app.route("/auth/headers", methods=["POST"])
def auth_headers():
    headers_raw = request.form.get("headers_raw", "").strip()
    if not headers_raw:
        return "No headers provided", 400
    os.makedirs(AUTH_DIR, exist_ok=True)
    try:
        from ytmusicapi import setup
        setup(filepath=HEADERS_AUTH_FILE, headers_raw=_normalize_headers_raw(headers_raw))
    except Exception as e:
        return f"Failed to parse headers: {e}", 400
    return redirect("/")


@app.route("/auth/revoke", methods=["POST"])
def auth_revoke():
    for path in (HEADERS_AUTH_FILE, OAUTH_FILE, OAUTH_CLIENT_FILE):
        if os.path.exists(path):
            os.remove(path)
    return redirect("/")


@app.route("/auth/oauth/setup", methods=["POST"])
def auth_oauth_setup():
    """Start device flow. Persists client credentials and returns a user_code
    + verification URL for the frontend to display."""
    client_id = (request.json or {}).get("client_id", "").strip()
    client_secret = (request.json or {}).get("client_secret", "").strip()
    if not client_id or not client_secret:
        return jsonify({"error": "client_id and client_secret required"}), 400
    try:
        from ytmusicapi.auth.oauth import OAuthCredentials
        creds = OAuthCredentials(client_id=client_id, client_secret=client_secret)
        code = creds.get_code()
    except Exception as e:
        return jsonify({"error": f"Failed to start OAuth flow: {e}"}), 400
    os.makedirs(AUTH_DIR, exist_ok=True)
    tmp = OAUTH_CLIENT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"client_id": client_id, "client_secret": client_secret}, f)
    os.replace(tmp, OAUTH_CLIENT_FILE)
    return jsonify({
        "device_code": code["device_code"],
        "user_code": code["user_code"],
        "verification_url": code["verification_url"],
        "interval": code.get("interval", 5),
        "expires_in": code.get("expires_in", 1800),
    })


@app.route("/auth/oauth/poll", methods=["POST"])
def auth_oauth_poll():
    """Poll Google for the token. Returns pending until the user completes
    the code entry, then saves the token in ytmusicapi's expected format
    (Token dataclass fields including computed `expires_at`)."""
    import time
    device_code = (request.json or {}).get("device_code", "").strip()
    if not device_code:
        return jsonify({"error": "device_code required"}), 400
    from scripts.ytmusic_auth import load_oauth_client
    client = load_oauth_client()
    if not client:
        return jsonify({"error": "no client credentials — run setup first"}), 400
    try:
        from ytmusicapi.auth.oauth import OAuthCredentials
        creds = OAuthCredentials(
            client_id=client["client_id"],
            client_secret=client["client_secret"],
        )
        raw = creds.token_from_code(device_code)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400
    if isinstance(raw, dict) and raw.get("error"):
        err = raw["error"]
        if err in ("authorization_pending", "slow_down"):
            return jsonify({"status": "pending"})
        return jsonify({"status": "error", "error": err}), 400
    if not isinstance(raw, dict) or "access_token" not in raw:
        return jsonify({"status": "pending"})
    # Match ytmusicapi's on-disk shape: include all Token dataclass fields
    # plus a computed expires_at so OAuthToken.is_expiring works on load.
    token_file = {
        "access_token":  raw["access_token"],
        "refresh_token": raw.get("refresh_token", ""),
        "scope":         raw.get("scope", "https://www.googleapis.com/auth/youtube"),
        "token_type":    raw.get("token_type", "Bearer"),
        "expires_in":    raw.get("expires_in", 3600),
        "expires_at":    int(time.time()) + raw.get("expires_in", 3600),
    }
    os.makedirs(AUTH_DIR, exist_ok=True)
    tmp = OAUTH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(token_file, f)
    os.replace(tmp, OAUTH_FILE)
    return jsonify({"status": "success"})


@app.route("/logs/<path:name>")
def get_log(name):
    return send_from_directory(LOG_DIR, name)


@app.route("/api/runs")
def api_runs():
    # If runs.json exists and is valid JSON, use it (even if empty — user may have cleared it)
    if os.path.exists(RUNS_FILE):
        try:
            with open(RUNS_FILE) as f:
                runs = json.load(f)
            for run in runs:
                if "log" in run:
                    run.setdefault("log_name", os.path.basename(run["log"]))
            return jsonify(runs)
        except (json.JSONDecodeError, ValueError):
            pass
    # Fallback only when file is missing or corrupt: synthesise from log files on disk
    if not os.path.exists(LOG_DIR):
        return jsonify([])
    files = sorted(
        [f for f in os.listdir(LOG_DIR) if f.endswith(".log")],
        reverse=True
    )[:50]
    synthetic = []
    for fname in files:
        try:
            ts_part = fname.replace("run_", "").replace(".log", "")
            ts = datetime.datetime.strptime(ts_part, "%Y%m%d_%H%M%S").isoformat()
        except ValueError:
            ts = fname
        synthetic.append({
            "timestamp": ts,
            "status": "unknown",
            "log": os.path.join(LOG_DIR, fname),
            "log_name": fname,
        })
    return jsonify(synthetic)


@app.route("/api/playlists")
def api_playlists():
    if not os.path.exists(HEADERS_AUTH_FILE):
        return jsonify({"error": "not authenticated"}), 401
    try:
        from scripts.ytmusic_auth import headers_to_ytmusic
        ytmusic = headers_to_ytmusic()
        raw = ytmusic.get_library_playlists(limit=200)
        playlists = []
        for pl in raw:
            pid   = pl.get("playlistId")
            title = pl.get("title", "")
            if not pid or not title:
                continue
            playlists.append({
                "id":          pid,
                "title":       title,
                "count":       pl.get("count"),
                "unsupported": title in UNSUPPORTED_TITLES,
            })
        return jsonify(playlists)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/playlists/selection", methods=["GET"])
def get_playlist_selection():
    try:
        with open(SELECTION_FILE) as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"ids": []})


@app.route("/api/playlists/selection", methods=["POST"])
def set_playlist_selection():
    ids = request.json.get("ids", [])
    tmp = SELECTION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ids": ids}, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SELECTION_FILE)
    return jsonify({"ok": True})


@app.route("/api/next-run")
def api_next_run():
    next_run, delta = get_next_run_safe()
    return jsonify({"next_run": next_run, "delta": delta})


@app.route("/api/logs")
def api_logs():
    if not os.path.exists(LOG_DIR):
        return jsonify([])
    files = sorted(
        [f for f in os.listdir(LOG_DIR) if f.endswith(".log")],
        reverse=True
    )
    return jsonify(files)


@app.route("/api/downloads")
def api_downloads():
    return jsonify({"files": get_files(), "size": get_download_size()})


@app.route("/api/downloads/metadata")
def downloads_metadata():
    page  = max(1, int(request.args.get("page",  1)))
    limit = max(1, min(int(request.args.get("limit", 20)), 100))

    all_files = []
    for root, _, files in os.walk(DOWNLOAD_DIR):
        for fname in files:
            if _is_music(fname):
                fpath = os.path.join(root, fname)
                rel   = fpath[len(DOWNLOAD_DIR):].lstrip("/")
                all_files.append((rel, fpath))
    all_files.sort(key=lambda x: x[0])

    total      = len(all_files)
    start      = (page - 1) * limit
    page_files = all_files[start:start + limit]

    result = []
    for rel, fpath in page_files:
        info = {"path": rel}
        try:
            audio = MutagenFile(fpath, easy=True)
            if audio is not None:
                info["title"]    = audio.get("title",  [""])[0]
                info["artist"]   = audio.get("artist", [""])[0]
                info["album"]    = audio.get("album",  [""])[0]
                info["duration"] = int(audio.info.length)
            # cover art: ID3-tagged files only (MP3/AIFF)
            try:
                tags = ID3(fpath)
                info["has_art"] = any(k.startswith("APIC") for k in tags)
            except Exception:
                info["has_art"] = False
        except Exception:
            pass
        try:
            info["size"] = os.path.getsize(fpath)
        except OSError:
            info["size"] = 0
        result.append(info)

    return jsonify({
        "files": result,
        "total": total,
        "page":  page,
        "pages": max(1, (total + limit - 1) // limit),
        "limit": limit,
    })


@app.route("/api/downloads/art/<path:filename>")
def download_art(filename):
    fpath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(fpath):
        return "", 404
    try:
        tags = ID3(fpath)
        for key in tags:
            if key.startswith("APIC"):
                apic = tags[key]
                return Response(apic.data, mimetype=apic.mime)
    except Exception:
        pass
    return "", 404


def _cron_line(expression):
    return f"{expression} {CRON_SUFFIX}\n"


def _cron_file_matches(expression):
    """Whether the installed file is exactly what we'd write today.

    Compares the whole line, not just the schedule: the command changed once
    (the log path moved to persistent storage) and comparing expressions alone
    would have left every existing container on the old one forever.
    """
    try:
        with open(CRON_FILE) as f:
            return f.read() == _cron_line(expression)
    except Exception:
        return False


def _expression_from_cron_file():
    """Read the 5-field expression out of /etc/cron.d/ytmusic, if it's there."""
    try:
        with open(CRON_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    return " ".join(parts[:5])
    except Exception:
        pass
    return None


def read_schedule():
    """The persisted expression, migrating an older container's if needed."""
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            expression = json.load(f).get("expression")
            if expression:
                return expression
    except Exception:
        pass
    # Nothing persisted yet: adopt whatever the container is currently running
    # so upgrading doesn't silently reset someone's schedule.
    return _expression_from_cron_file()


def write_schedule(expression):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SCHEDULE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"expression": expression,
                   "updated_at": datetime.datetime.utcnow().isoformat()}, f,
                  indent=2)
    os.replace(tmp, SCHEDULE_FILE)


def write_cron_file(expression):
    """Materialise /etc/cron.d/ytmusic from an expression.

    Debian's cron silently ignores entries in cron.d that are group- or
    world-writable, and wants a trailing newline — both failures are quiet, so
    they're worth being explicit about rather than trusting the default umask.
    """
    os.makedirs(os.path.dirname(CRON_FILE), exist_ok=True)
    with open(CRON_FILE, "w") as f:
        f.write(f"{expression} {CRON_SUFFIX}\n")
    os.chmod(CRON_FILE, 0o644)


def ensure_cron():
    """Rebuild the cron file from persistent storage. Safe to call repeatedly.

    Called at startup because the container's writable layer doesn't survive a
    recreate. Without this, the schedule only exists between someone saving it
    in the UI and the next deploy.
    """
    expression = read_schedule() or DEFAULT_CRON
    try:
        if not _cron_file_matches(expression):
            write_cron_file(expression)
            subprocess.run(["service", "cron", "reload"], capture_output=True)
        write_schedule(expression)
    except Exception as e:
        print(f"[cron] could not restore schedule {expression!r}: {e}")
        return None
    return expression


@app.route("/api/cron", methods=["GET"])
def get_cron():
    try:
        expression = read_schedule()
        if not expression:
            return jsonify({"expression": ""})
        # Self-heal: if the container was recreated since the schedule was
        # saved, the cron file is gone even though we still know the schedule.
        if not _cron_file_matches(expression):
            write_cron_file(expression)
            subprocess.run(["service", "cron", "reload"], capture_output=True)
        return jsonify({"expression": expression})
    except Exception as e:
        return jsonify({"expression": "", "error": str(e)})


@app.route("/api/cron", methods=["POST"])
def set_cron():
    expression = request.json.get("expression", "").strip()
    parts = expression.split()
    if len(parts) != 5:
        return jsonify({"error": "Must be exactly 5 fields: min hour dom mon dow"}), 400
    try:
        croniter(expression)
    except Exception as e:
        return jsonify({"error": f"Invalid expression: {e}"}), 400
    try:
        # Persist first: the bind mount is the copy that survives a recreate,
        # and the cron file is derived from it rather than the other way round.
        write_schedule(expression)
        write_cron_file(expression)
        subprocess.run(["service", "cron", "reload"], capture_output=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


# ── Vibe sorter: review queue for tracks the model wasn't confident about ────

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_queue(payload):
    """Atomic write — same reason as _persist_run: bind mounts drop in-place
    overwrites, and a half-written queue would lose pending tracks."""
    os.makedirs(VIBE_DIR, exist_ok=True)
    tmp = SORT_QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SORT_QUEUE_FILE)


def _sorter_excludes():
    """The exclusion patterns the model was trained with.

    Kept in step with training so the UI can't offer a destination the sorter
    doesn't know about — and so it never offers YouTube's own generated mixes,
    whose items have no setVideoId and can't be edited at all.
    """
    meta = _read_json(f"{VIBE_DIR}/thresholds.json", {})
    return [p.lower() for p in meta.get("exclude", ["recap", "hotlist"])]


def _playlist_ids():
    """Title -> playlistId, from the cached library; falls back to the API."""
    excludes = _sorter_excludes()

    def usable(title):
        return not any(p in title.lower() for p in excludes)

    lib = _read_json(VIBE_LIBRARY_FILE, None)
    if lib:
        return {p["title"]: p["id"] for p in lib.get("playlists", [])
                if usable(p["title"])}
    # No cached library: ask YouTube, but never let that failure take the queue
    # page down with it. Without auth the page should still list what's
    # pending, just with no destinations to offer.
    try:
        from scripts.ytmusic_auth import headers_to_ytmusic
        return {p["title"]: p["playlistId"]
                for p in headers_to_ytmusic().get_library_playlists(limit=200)
                if p.get("playlistId") and p.get("title") and usable(p["title"])}
    except Exception:
        return {}


def _log_decision(record):
    os.makedirs(os.path.dirname(DECISIONS_FILE), exist_ok=True)
    with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(
            {**record, "at": datetime.datetime.utcnow().isoformat()},
            ensure_ascii=False) + "\n")


def _drop_from_queue(video_id):
    queue = _read_json(SORT_QUEUE_FILE, {"tracks": []})
    before = len(queue.get("tracks", []))
    queue["tracks"] = [t for t in queue.get("tracks", [])
                       if t.get("videoId") != video_id]
    _write_queue(queue)
    return before != len(queue["tracks"])


@app.route("/sort")
def sort_page():
    return render_template("sort.html")


@app.route("/api/sort/queue")
def sort_queue():
    queue = _read_json(SORT_QUEUE_FILE, {"tracks": [], "generated_at": None})
    return jsonify({
        "generated_at": queue.get("generated_at"),
        "tracks": queue.get("tracks", []),
        "playlists": sorted(_playlist_ids().keys()),
    })


@app.route("/sort/stats")
def sort_stats_page():
    return render_template("sort_stats.html")


@app.route("/api/sort/stats")
def sort_stats():
    """What the sorter knows and what it has done.

    The number worth watching is the automation rate — placed unattended
    against total placed. It should climb as queue decisions accumulate,
    because every pick is a training label for the playlists the model is
    currently worst at.
    """
    meta = _read_json(f"{VIBE_DIR}/thresholds.json", {})
    thresholds = meta.get("thresholds", {})
    queue = _read_json(SORT_QUEUE_FILE, {"tracks": []})

    counts = {"auto": 0, "manual": 0, "skipped": 0}
    by_playlist = {}
    by_day = {}
    if os.path.exists(DECISIONS_FILE):
        with open(DECISIONS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                kind = d.get("kind", "auto")
                counts[kind] = counts.get(kind, 0) + 1
                playlist = d.get("playlist")
                if playlist and kind in ("auto", "manual"):
                    slot = by_playlist.setdefault(playlist, {"auto": 0, "manual": 0})
                    slot[kind] += 1
                day = (d.get("at") or "")[:10]
                if day:
                    slot = by_day.setdefault(day, {"auto": 0, "manual": 0,
                                                   "skipped": 0})
                    slot[kind] = slot.get(kind, 0) + 1

    placed = counts["auto"] + counts["manual"]

    # Pipeline state: what's been fetched and embedded. An .npz is a zip of
    # "<videoId>.npy" entries, so the names can be listed without numpy and
    # without decompressing anything.
    audio_dir = os.path.join(VIBE_DIR, "audio")
    audio_files = audio_bytes = 0
    if os.path.isdir(audio_dir):
        with os.scandir(audio_dir) as entries:
            for entry in entries:
                if entry.is_file():
                    audio_files += 1
                    audio_bytes += entry.stat().st_size

    embedded = {}
    embed_dir = os.path.join(VIBE_DIR, "embeddings")
    if os.path.isdir(embed_dir):
        import zipfile
        for name in sorted(os.listdir(embed_dir)):
            if not name.endswith(".npz"):
                continue
            try:
                with zipfile.ZipFile(os.path.join(embed_dir, name)) as z:
                    embedded[name[:-4]] = len(z.namelist())
            except Exception:
                embedded[name[:-4]] = None

    lib = _read_json(VIBE_LIBRARY_FILE, None) or {}
    excludes = _sorter_excludes()
    filed = {t["videoId"] for p in lib.get("playlists", [])
             if not any(x in p["title"].lower() for x in excludes)
             for t in p.get("tracks", [])}
    liked = [t["videoId"] for t in lib.get("liked", [])]
    unsorted = [v for v in liked if v not in filed]

    backend = meta.get("backend")
    have_vectors = embedded.get(backend)
    pipeline = {
        "audio_files": audio_files,
        "audio_mb": round(audio_bytes / 1e6, 1),
        "embedded": embedded,
        "backend": backend,
        "liked": len(liked),
        "filed": len(filed),
        "unsorted": len(unsorted),
        "queue_generated_at": queue.get("generated_at"),
        # Tracks still needing work before they could be placed at all.
        "awaiting_embedding": max(0, len(unsorted) - len(queue.get("tracks", []))),
        "vectors": have_vectors,
    }
    rows = []
    for playlist, rule in sorted(thresholds.items()):
        activity = by_playlist.get(playlist, {})
        rows.append({
            "playlist": playlist,
            "threshold": rule.get("threshold"),
            "precision": rule.get("precision"),
            "recall": rule.get("recall"),
            "support": rule.get("support"),
            "revoked": rule.get("revoked"),
            "auto": activity.get("auto", 0),
            "manual": activity.get("manual", 0),
        })

    return jsonify({
        "model": {
            "trained_at": meta.get("trained_at"),
            "n_train": meta.get("n_train"),
            "top1": meta.get("top1"),
            "top3": meta.get("top3"),
            "backend": meta.get("backend"),
            "target_precision": meta.get("target_precision"),
            "routable": sum(1 for r in rows if r["threshold"] is not None),
            "playlists": len(rows),
        },
        "activity": {
            "queued": len(queue.get("tracks", [])),
            "auto": counts["auto"],
            "manual": counts["manual"],
            "skipped": counts["skipped"],
            "placed": placed,
            "automation_rate": (counts["auto"] / placed) if placed else None,
        },
        "pipeline": pipeline,
        "by_day": [{"day": d, **v} for d, v in sorted(by_day.items())][-14:],
        "playlists": rows,
        "nested": meta.get("nested"),
    })


@app.route("/api/sort/preview/<video_id>")
def sort_preview(video_id):
    """Serve the cached 60s snippet as the preview.

    It already exists — it's what the embedding was computed from — and it's
    taken from the middle of the track, which is the part worth judging. No
    network call, so previews are instant. Mono 16kHz: fine for recognising a
    track, not meant for listening pleasure.

    Returns 404 when the snippet was pruned (--prune-audio) or never fetched;
    the UI falls back to a link out to YouTube Music.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,20}", video_id or ""):
        return jsonify({"error": "bad id"}), 400
    audio_dir = os.path.join(VIBE_DIR, "audio")
    # m4a is what new fetches store; wav is the older format, still readable.
    for extension, mime in (("m4a", "audio/mp4"), ("wav", "audio/wav")):
        name = f"{video_id}.{extension}"
        if os.path.exists(os.path.join(audio_dir, name)):
            return send_from_directory(audio_dir, name, mimetype=mime)
    return jsonify({"error": "no preview"}), 404


@app.route("/api/sort/assign", methods=["POST"])
def sort_assign():
    """Place one queued track. The pick is also a training label: the track
    joins the playlist, and playlists are what the next training run reads."""
    body = request.json or {}
    video_id, playlist = body.get("videoId"), body.get("playlist")
    if not video_id or not playlist:
        return jsonify({"error": "videoId and playlist are required"}), 400

    playlist_id = _playlist_ids().get(playlist)
    if not playlist_id:
        return jsonify({"error": f"unknown playlist {playlist!r}"}), 400

    try:
        from scripts.ytmusic_auth import headers_to_ytmusic
        headers_to_ytmusic().add_playlist_items(
            playlist_id, [video_id], duplicates=False)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    _drop_from_queue(video_id)
    _log_decision({"kind": "manual", "videoId": video_id,
                   "playlist": playlist,
                   "title": body.get("title"), "artist": body.get("artist")})
    return jsonify({"ok": True})


@app.route("/api/sort/skip", methods=["POST"])
def sort_skip():
    """Leave a track unsorted. It stays liked and reappears on a later run
    once the model has learned more."""
    video_id = (request.json or {}).get("videoId")
    if not video_id:
        return jsonify({"error": "videoId is required"}), 400
    _drop_from_queue(video_id)
    _log_decision({"kind": "skipped", "videoId": video_id})
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Before serving: the container may have just been recreated, in which case
    # /etc/cron.d/ytmusic is gone and nothing would ever fire.
    restored = ensure_cron()
    if restored:
        print(f"[cron] schedule active: {restored}")
    app.run(host="0.0.0.0", port=8080)