import subprocess
import sys


def run_download_job():
    subprocess.run(
        [sys.executable, "/app/scripts/download.py"],
        text=True
    )
    print("RUN STARTED DOWNLOAD")


def run_scheduler_stream():
    process = subprocess.Popen(
        [sys.executable, "-u", "/app/scripts/scheduler.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    if process.stdout is None:
        yield "data: ERROR no stdout\n\n"
        return

    for line in process.stdout:
        yield f"data: {line.strip()}\n\n"

    yield "data: [DONE]\n\n"