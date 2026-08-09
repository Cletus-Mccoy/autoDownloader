import json
import subprocess
import datetime
import os

RUNS_FILE = "/app/data/runs.json"
LOG_DIR = "/app/data/logs"

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs("/app/data", exist_ok=True)

def log_run(status, log_file, trigger="cron"):
    run = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "status": status,
        "trigger": trigger,
        "log": log_file
    }

    if os.path.exists(RUNS_FILE):
        try:
            with open(RUNS_FILE) as f:
                runs = json.load(f)
        except (json.JSONDecodeError, ValueError):
            runs = []
    else:
        runs = []

    runs.insert(0, run)

    with open(RUNS_FILE, "w") as f:
        json.dump(runs[:50], f, indent=2)  # keep last 50 runs


# The sorter files liked tracks into playlists; the downloader then collects
# whatever is in those playlists. Order matters — sorting after downloading
# would leave every newly filed track waiting a full day.
SORTER_ENABLED = os.getenv("VIBE_SORTER", "true").lower() == "true"
# Caps the nightly download of preview snippets. A backlog just drains over
# several nights rather than one run fetching thousands of files.
SORTER_MAX_NEW_AUDIO = os.getenv("VIBE_MAX_NEW_AUDIO", "50")


def _step(name, argv, log):
    """Run one pipeline step. Returns True on success.

    Sorter failures are logged and stepped over: a broken model or expired
    auth must not stop the downloader from collecting what's already filed.
    """
    log.write(f"\n{'=' * 60}\n{name}\n{'=' * 60}\n")
    log.flush()
    try:
        proc = subprocess.run(argv, stdout=log, stderr=log)
    except Exception as e:
        log.write(f"\n{name} could not start: {e}\n")
        log.flush()
        return False
    if proc.returncode != 0:
        log.write(f"\n{name} exited {proc.returncode}\n")
        log.flush()
    return proc.returncode == 0


def run_downloader():
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_file = f"{LOG_DIR}/run_{ts}.log"

    with open(log_file, "w") as f:
        if SORTER_ENABLED:
            # Retrain first: tracks placed from the review queue since the last
            # run are new labels, and this is what turns your corrections into
            # a better model.
            _step("Retraining the sorter",
                  ["python3", "/app/scripts/vibe_train.py",
                   "--refresh-library"], f)
            _step("Sorting liked tracks",
                  ["python3", "/app/scripts/vibe_route.py", "--execute",
                   "--no-refresh-library",
                   "--max-new-audio", SORTER_MAX_NEW_AUDIO], f)

        ok = _step("Downloading playlists",
                   ["python3", "/app/scripts/download.py"], f)

    log_run("success" if ok else "failed", log_file)


if __name__ == "__main__":
    run_downloader()