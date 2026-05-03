import subprocess


def run_download_job():
    subprocess.run(
        ["python3", "/app/scripts/download.py"],
        text=True
    )
    print("RUN STARTED DOWNLOAD")


def run_scheduler_stream():
    process = subprocess.Popen(
        ["python3", "-u", "/app/scripts/scheduler.py"],
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