import threading
import json
import os

STATE_FILE = "/app/data/browser.json"

_lock = threading.Lock()

state = {
    "download_running": False,
    "scheduler_running": False,
    "last_run": None,
    "last_status": None
}


def load_state():
    global state
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state.update(json.load(f))


def save_state():
    with _lock:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)


def set(key, value):
    with _lock:
        state[key] = value
        save_state()


def get(key, default=None):
    return state.get(key, default)


def is_running(job="download"):
    return state.get(f"{job}_running", False)


def set_running(job="download", value=True):
    set(f"{job}_running", value)