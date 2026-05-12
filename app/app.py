from flask import Flask, jsonify, send_from_directory, request, redirect, Response, render_template
import os
import json
import shutil
import threading
import subprocess
import datetime
from croniter import croniter
from scripts.timer import get_next_run_safe
from scripts.runner import run_scheduler_stream
from scripts.scheduler import log_run

app = Flask(__name__)

DATA_DIR = "/app/data"
RUNS_FILE = f"{DATA_DIR}/runs.json"
DOWNLOAD_DIR = f"{DATA_DIR}/downloads"
LOG_DIR = f"{DATA_DIR}/logs"
AUTH_DIR = f"{DATA_DIR}/auth"
COOKIES_FILE = f"{AUTH_DIR}/cookies.txt"
CRON_FILE = "/etc/cron.d/ytmusic"


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


def get_files():
    result = []
    for root, _, files in os.walk(DOWNLOAD_DIR):
        for f in files:
            result.append(os.path.join(root, f).replace("/app/data/downloads/", ""))
    return result


def get_download_size():
    total = 0
    for root, _, files in os.walk(DOWNLOAD_DIR):
        for f in files:
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
        else:
            status = "failed"
        log_run(status, log_file, trigger="manual")
    except Exception:
        pass
    finally:
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
    return jsonify({"authenticated": os.path.exists(COOKIES_FILE)})


@app.route("/auth/upload", methods=["POST"])
def auth_upload():
    f = request.files.get("cookies")
    if not f:
        return "No file provided", 400
    os.makedirs(AUTH_DIR, exist_ok=True)
    f.save(COOKIES_FILE)
    return redirect("/")


@app.route("/auth/revoke", methods=["POST"])
def auth_revoke():
    if os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)
    return redirect("/")


@app.route("/logs/<path:name>")
def get_log(name):
    return send_from_directory(LOG_DIR, name)


@app.route("/api/runs")
def api_runs():
    runs = load_runs()
    if runs:
        return jsonify(runs)
    # Fallback: synthesise entries from log files on disk
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


@app.route("/api/cron", methods=["GET"])
def get_cron():
    try:
        with open(CRON_FILE) as f:
            return jsonify({"content": f.read()})
    except Exception as e:
        return jsonify({"content": "", "error": str(e)})


@app.route("/api/cron", methods=["POST"])
def set_cron():
    content = request.json.get("content", "").strip() + "\n"
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 5:
            expr = " ".join(parts[:5])
            try:
                croniter(expr)
            except Exception as e:
                return jsonify({"error": f"Invalid cron expression '{expr}': {e}"}), 400
            break
    try:
        with open(CRON_FILE, "w") as f:
            f.write(content)
        subprocess.run(["service", "cron", "reload"], capture_output=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)