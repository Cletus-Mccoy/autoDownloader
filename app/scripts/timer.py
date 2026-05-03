import re
import json
import datetime
import os
from croniter import croniter

CRON_FILE = "/etc/cron.d/ytmusic"
RUNS_FILE = "/app/data/runs.json"

def get_cron_expression():
    try:
        with open(CRON_FILE) as f:
            for line in f:
                line = line.strip()

                # skip comments / empty lines
                if not line or line.startswith("#"):
                    continue

                parts = line.split()

                # system cron format:
                # min hour dom mon dow user command
                if len(parts) >= 6:
                    return " ".join(parts[0:5])

        return None

    except Exception:
        return None

def get_last_run():
    try:
        if not os.path.exists(RUNS_FILE):
            return None

        with open(RUNS_FILE) as f:
            runs = json.load(f)

        if not runs:
            return None

        return datetime.datetime.fromisoformat(runs[0]["timestamp"])

    except Exception:
        return None

def get_next_run():
    cron_expr = get_cron_expression()
    last_run = get_last_run()

    if not cron_expr:
        return None

    try:
        base = last_run if last_run else datetime.datetime.now()

        itr = croniter(cron_expr, base)
        next_run = itr.get_next(datetime.datetime)

        return next_run

    except Exception:
        return None

def get_next_run_safe():
    next_run = get_next_run()

    if not next_run:
        return None, None

    now = datetime.datetime.now()
    delta = next_run - now

    if delta.total_seconds() < 0:
        return None, None

    return (
        next_run.strftime("%Y-%m-%d %H:%M:%S"),
        str(delta).split(".")[0]
    )