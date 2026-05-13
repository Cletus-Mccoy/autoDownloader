"""
Youtube Music Playlist Downloader
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from ytmusic_auth import headers_to_ytmusic, HEADERS_AUTH_FILE

load_dotenv()

BASE_DIR = "/app/data/downloads"
SELECTION_FILE = "/app/data/playlist_selection.json"

AUDIO_QUALITY = os.getenv("YT_DLP_QUALITY", "320")
AUDIO_CODEC = os.getenv("YT_DLP_CODEC", "mp3")
NORMALIZE_AUDIO = os.getenv("YT_DLP_NORMALIZE", "false").lower() == "true"

UNSUPPORTED_TITLES = {"Liked Music", "Episodes for Later"}


def get_cookie_header():
    try:
        with open(HEADERS_AUTH_FILE) as f:
            headers = json.load(f)
        return headers.get("Cookie") or headers.get("cookie")
    except Exception:
        return None



def get_playlists():
    ytmusic = headers_to_ytmusic()
    raw = ytmusic.get_library_playlists(limit=200)

    playlists = []
    for pl in raw:
        pid = pl.get("playlistId")
        title = pl.get("title")
        if not pid or not title:
            continue
        playlists.append({
            "id": pid,
            "title": title,
            "url": f"https://music.youtube.com/playlist?list={pid}",
            "count": pl.get("count"),
        })

    return playlists


def get_selection():
    try:
        with open(SELECTION_FILE) as f:
            return json.load(f).get("ids", [])
    except Exception:
        return []


def download_playlist(playlist):
    title = playlist["title"]
    url = playlist["url"]
    count = playlist.get("count", "Unknown")

    if title in UNSUPPORTED_TITLES:
        print(f"\n⚠ Skipping {title} (not supported by yt-dlp)")
        return True

    safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in title)
    safe_name = safe_name.strip().replace(" ", "_")
    while "__" in safe_name:
        safe_name = safe_name.replace("__", "_")
    safe_name = safe_name.strip("_")

    playlist_dir = os.path.join(BASE_DIR, safe_name)
    archive_file = os.path.join(playlist_dir, "downloaded.txt")

    os.makedirs(playlist_dir, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"Downloading: {title}")
    print(f"Tracks: {count}")
    print(f"Saving to: {safe_name}/")
    print("=" * 60)

    cookie = get_cookie_header()

    cmd = [
        "yt-dlp",
        "-f", "bestaudio/best",
        "-x", "--extract-audio",
        "--audio-format", AUDIO_CODEC,
        "--download-archive", archive_file,
        "--output", f"{playlist_dir}/%(artist)s - %(title)s.%(ext)s",
        url,
        "--embed-thumbnail",
        "--embed-metadata",
        "--yes-playlist",
        "--ignore-errors",
        "--no-abort-on-error",
        "--extractor-args", "youtube:player_client=web,web_creator,web_music,android",
        "--no-warnings",
        "--js-runtimes", "node",
        "--audio-multistreams",
        "--restrict-filenames",
        "--sleep-interval", "2",
        "--max-sleep-interval", "5",
        "--postprocessor-args", "ffmpeg:-avoid_negative_ts make_zero -c:a libmp3lame -b:a 320k -ac 2 -ar 44100",
    ]

    if cookie:
        cmd.extend(["--add-header", f"Cookie:{cookie}"])
        print("Using saved auth headers")
    else:
        print("⚠ No auth headers — authenticate via the web UI first")

    try:
        print(f"\nStarting download for {title}...")
        subprocess.run(cmd, check=False)

        audio_files = list(Path(playlist_dir).glob("*.mp3"))
        if audio_files:
            print(f"\n✓ Completed: {title} ({len(audio_files)} files)")
        else:
            print(f"\n⚠ No files downloaded for: {title}")

        return True
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False


def main():
    print("\n" + "=" * 60)
    print("YouTube Music Playlist Downloader")
    print("=" * 60)

    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, check=True)
        print(f"✓ yt-dlp: {result.stdout.strip()}")
    except Exception:
        print("✗ yt-dlp not found")
        sys.exit(1)

    if not get_cookie_header():
        print("⚠ No auth headers found — authenticate via the web UI first")

    print(f"\n📁 Download location: {BASE_DIR}")

    playlists = get_playlists()

    selected_ids = get_selection()
    if selected_ids:
        playlists = [p for p in playlists if p["id"] in selected_ids]
        print(f"\n▶ Downloading {len(playlists)} selected playlist(s)")
    else:
        print(f"\n▶ Downloading all {len(playlists)} playlist(s)")

    for playlist in playlists:
        download_playlist(playlist)


if __name__ == "__main__":
    main()
