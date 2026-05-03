from flask import Flask, jsonify, send_from_directory, request, redirect, Response, render_template
import os
import json
import threading
import subprocess
from scripts.timer import get_next_run_safe
from scripts.runner import run_scheduler_stream
from scripts.scheduler import run_downloader

app = Flask(__name__)

DATA_DIR = "/app/data"
RUNS_FILE = f"{DATA_DIR}/runs.json"
DOWNLOAD_DIR = f"{DATA_DIR}/downloads"
LOG_DIR = f"{DATA_DIR}/logs"
AUTH_DIR = f"{DATA_DIR}/auth"
COOKIES_FILE = f"{AUTH_DIR}/cookies.txt"

allow_run = True

def load_runs():
    if not os.path.exists(RUNS_FILE):
        return []
    with open(RUNS_FILE) as f:
        return json.load(f)


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
        downloads=get_files()
    )


def get_files():
    result = []
    for root, _, files in os.walk(DOWNLOAD_DIR):
        for f in files:
            result.append(os.path.join(root, f).replace("/app/data/downloads/", ""))
    return result


@app.route("/run-stream")
def run_stream():
    return Response(run_scheduler_stream(), mimetype="text/event-stream")


@app.route("/run-now", methods=["POST"])
def run_now():
    t = threading.Thread(target=run_downloader, daemon=True)
    while allow_run:
        t.start()
        return redirect("/")

@app.route("/stop-now", methods=["POST"])
def stop_now():
    allow_run = False
    return redirect("/")
    allow_run = True

@app.route("/download-status", methods=["POST"])
def download_status():
    if subprocess.run(["pgrep", "-f", "yt-dlp"], stdout=subprocess.PIPE).returncode == 0:
        return jsonify({"status": "running"})
    else:
        return jsonify({"status": "idle"})
    
 
@app.route("/clear-logs", methods=["POST"])
def clear_logs():
    for f in os.listdir(LOG_DIR):
        os.remove(os.path.join(LOG_DIR, f))
    return redirect("/")

@app.route("/clear-downloads", methods=["POST"])
def clear_downloads():
    for f in os.listdir(DOWNLOAD_DIR):
        os.remove(os.path.join(DOWNLOAD_DIR, f))
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
    return jsonify(load_runs())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)