"""
Youtube Music Playlist Downloader
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from ytmusicapi import YTMusic

load_dotenv()

BASE_DIR = "/app/data/downloads"
COOKIES_FILE = "/app/data/auth/cookies.txt"
BROWSER_AUTH_FILE = "/app/data/browser.json"

AUDIO_QUALITY = os.getenv("YT_DLP_QUALITY", "320")
AUDIO_CODEC = os.getenv("YT_DLP_CODEC", "mp3")
NORMALIZE_AUDIO = os.getenv("YT_DLP_NORMALIZE", "false").lower() == "true"


def get_cookies():
    if os.path.exists(COOKIES_FILE):
        return COOKIES_FILE
    return None

def get_playlists():
    ytmusic = YTMusic(BROWSER_AUTH_FILE)
    raw = ytmusic.get_library_playlists(limit=200)

    playlists = []

    for pl in raw:
        pid = pl.get("playlistId")
        title = pl.get("title")

        if not pid or not title:
            continue

        playlists.append({
            "title": title,
            "url": f"https://music.youtube.com/playlist?list={pid}"
        })

    return playlists

def download_playlist(playlists):
    title = playlists['title']
    url = playlists['url']
    count = playlists.get('count', 'Unknown')

    skip_playlists = ['Liked Music', 'Episodes for Later']
    if title in skip_playlists:
        print(f"\n⚠ Skipping {title} (not supported by yt-dlp)")
        return True

    safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
    safe_name = safe_name.strip().replace(' ', '_')
    while '__' in safe_name:
        safe_name = safe_name.replace('__', '_')
    safe_name = safe_name.strip('_')

    playlist_dir = os.path.join(BASE_DIR, safe_name)
    archive_file = os.path.join(playlist_dir, 'downloaded.txt')

    os.makedirs(playlist_dir, exist_ok=True)

    print("\n" + "="*60)
    print(f"Downloading: {title}")
    print(f"Tracks: {count}")
    print(f"Saving to: {safe_name}/")
    print("="*60)

    cookies_file = get_cookies()

    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
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
        "--postprocessor-args", "ffmpeg:-avoid_negative_ts make_zero -c:a libmp3lame -b:a 320k -ac 2 -ar 44100"  # Force proper MP3 encoding    ]

        ]
    
    if cookies_file:
        cmd.extend(["--cookies", cookies_file])
        print("Using saved auth cookies")
    else:
        print("⚠ No auth cookies — authenticate via the web UI first")

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
    print("\n" + "="*60)
    print("YouTube Music Playlist Downloader")
    print("="*60)

    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, check=True)
        print(f"✓ yt-dlp: {result.stdout.strip()}")
    except Exception:
        print("✗ yt-dlp not found")
        sys.exit(1)

    cookies_file = get_cookies()
    if not cookies_file:
        print("⚠ No auth cookies found — authenticate via the web UI first")

    print(f"\n📁 Download location: {BASE_DIR}")

    playlists = get_playlists()
    for playlist in playlists:
        download_playlist(playlist)


if __name__ == "__main__":
    main()
