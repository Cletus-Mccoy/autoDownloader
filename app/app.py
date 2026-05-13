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
from mutagen import File as MutagenFile
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
CRON_FILE        = "/etc/cron.d/ytmusic"
CRON_SUFFIX      = "root python /app/scripts/scheduler.py >> /var/log/cron.log 2>&1"
SELECTION_FILE   = f"{DATA_DIR}/playlist_selection.json"
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
    return jsonify({"authenticated": os.path.exists(HEADERS_AUTH_FILE)})


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
    if os.path.exists(HEADERS_AUTH_FILE):
        os.remove(HEADERS_AUTH_FILE)
    return redirect("/")


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


@app.route("/api/cron", methods=["GET"])
def get_cron():
    try:
        with open(CRON_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    return jsonify({"expression": " ".join(parts[:5])})
        return jsonify({"expression": ""})
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
        with open(CRON_FILE, "w") as f:
            f.write(f"{expression} {CRON_SUFFIX}\n")
        subprocess.run(["service", "cron", "reload"], capture_output=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)