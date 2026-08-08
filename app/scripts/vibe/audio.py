"""Fetch and cache a short audio snippet per track via yt-dlp.

One 60s mono 16kHz WAV from the middle of each track, keyed by videoId, so a
track is never downloaded twice. This is the slow stage; everything after it
runs off the cache in seconds.

Auth reuses the service's existing cookie path (headers_auth.json -> Netscape
cookies file) rather than introducing a second credential route.
"""

import os
import shutil
import subprocess
import sys
import threading

from concurrent.futures import ThreadPoolExecutor

from download import get_cookie_header, write_cookies_file

from . import config

_print_lock = threading.Lock()


def yt_dlp_binary():
    """Prefer the yt-dlp installed alongside this interpreter.

    Whatever is first on PATH is often an unrelated, much older install (a
    stale conda one here), and an out-of-date yt-dlp fails YouTube's signature
    challenge — it reports "Requested format is not available" and only
    storyboard images remain. The venv copy is the one kept current alongside
    yt-dlp-ejs, so resolve that first.
    """
    scripts_dir = os.path.dirname(sys.executable)
    for name in ("yt-dlp.exe", "yt-dlp"):
        candidate = os.path.join(scripts_dir, name)
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("yt-dlp")
    if not found:
        raise RuntimeError("yt-dlp not found next to the interpreter or on PATH")
    return found


def snippet_path(video_id):
    return os.path.join(config.AUDIO_DIR, f"{video_id}.wav")


def is_cached(video_id):
    path = snippet_path(video_id)
    return os.path.exists(path) and os.path.getsize(path) > 0


def _window(duration_seconds):
    """Centre window of SNIPPET_SECONDS. Short/unknown tracks start at 0."""
    length = config.SNIPPET_SECONDS
    if not duration_seconds or duration_seconds <= length:
        return 0, length
    start = int(duration_seconds / 2 - length / 2)
    return start, start + length


def _prepare_cookies():
    cookie = get_cookie_header()
    if not cookie:
        print("⚠ No auth cookies found — snippet downloads will likely fail. "
              "Authenticate via the web UI first.")
        return None
    return write_cookies_file(cookie, config.COOKIES_FILE)


def fetch_one(track, cookies_path=None):
    """Download one snippet. Returns the path, or None on failure."""
    video_id = track["videoId"]
    if is_cached(video_id):
        return snippet_path(video_id)

    start, end = _window(track.get("duration_seconds"))
    cmd = [
        yt_dlp_binary(),
        "-f", "bestaudio/best",
        "--no-playlist",
        "--download-sections", f"*{start}-{end}",
        "--force-keyframes-at-cuts",
        "-x", "--audio-format", "wav",
        "--postprocessor-args", f"ExtractAudio:-ac 1 -ar {config.SAMPLE_RATE}",
        "--output", os.path.join(config.AUDIO_DIR, "%(id)s.%(ext)s"),
        "--extractor-args", "youtube:player_client=web,web_music,android",
        "--no-warnings",
        "--ignore-errors",
        "--no-abort-on-error",
        "--sleep-interval", "1",
        "--max-sleep-interval", "3",
        f"https://music.youtube.com/watch?v={video_id}",
    ]
    if shutil.which("node"):
        cmd.extend(["--js-runtimes", "node"])
    if cookies_path:
        cmd.extend(["--cookies", cookies_path])

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None

    return snippet_path(video_id) if is_cached(video_id) else None


def fetch_many(tracks, workers=3):
    """Fetch snippets for many tracks. Returns {videoId: path} for successes.

    Workers default low on purpose: YouTube throttles parallel clients, and a
    rate-limited run poisons the cache with failures rather than going faster.
    """
    binary = yt_dlp_binary()
    version = subprocess.run([binary, "--version"], capture_output=True,
                             text=True).stdout.strip()
    print(f"Using {binary} ({version})")

    config.ensure_dirs()
    cookies_path = _prepare_cookies()

    pending = [t for t in tracks if not is_cached(t["videoId"])]
    cached = {t["videoId"]: snippet_path(t["videoId"])
              for t in tracks if is_cached(t["videoId"])}
    print(f"Audio: {len(cached)} cached, {len(pending)} to fetch "
          f"({workers} workers)")

    results = dict(cached)
    done = 0

    def work(track):
        nonlocal done
        path = fetch_one(track, cookies_path)
        with _print_lock:
            done += 1
            mark = "✓" if path else "✗"
            print(f"  [{done}/{len(pending)}] {mark} "
                  f"{track.get('artist') or '?'} — {track.get('title') or '?'}")
        return track["videoId"], path

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for video_id, path in pool.map(work, pending):
                if path:
                    results[video_id] = path

    failed = len(tracks) - len(results)
    print(f"Audio ready for {len(results)}/{len(tracks)} tracks "
          f"({failed} unavailable)")
    return results
