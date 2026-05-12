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


def run_downloader():
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_file = f"{LOG_DIR}/run_{ts}.log"

    with open(log_file, "w") as f:
        proc = subprocess.run(
            ["python3", "/app/scripts/download.py"],
            stdout=f,
            stderr=f
        )

    status = "success" if proc.returncode == 0 else "failed"
    log_run(status, log_file)


if __name__ == "__main__":
    run_downloader()