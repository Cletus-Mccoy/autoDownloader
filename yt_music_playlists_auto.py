"""
YouTube Music Playlist Downloader (Enhanced with ytmusicapi)
Automatically discovers and downloads all playlists from your YouTube Music account

Requirements:
    pip install ytmusicapi yt-dlp

Setup:
    1. Run: ytmusicapi browser
    2. Follow instructions to create browser.json
    3. Run this script
"""

import os
import sys
import json
import subprocess
import time
from pathlib import Path
from env import load_dotenv

load_dotenv()

try:
    from ytmusicapi import YTMusic
    YTMUSIC_AVAILABLE = True
except ImportError:
    YTMUSIC_AVAILABLE = False
    print("⚠ ytmusicapi not installed. Install with: pip install ytmusicapi")

# Configuration
BASE_DIR = r"C:\Users\kaspe\Desktop\MUSIC\TEST_1"
AUDIO_FORMAT = "mp3"
AUTH_FILE = "oauth.json"  # Try OAuth first, then browser.json
BROWSER_AUTH_FILE = os.getenv("FIREFOX_HEADERS", "browser.json")  # Browser headers auth file

# Traktor-compatible audio settings
AUDIO_QUALITY = os.getenv("YT_DLP_QUALITY", "320")  # 320kbps CBR for best Traktor compatibility
AUDIO_CODEC = os.getenv("YT_DLP_CODEC", "mp3")    # Force MP3 codec
NORMALIZE_AUDIO = os.getenv("YT_DLP_NORMALIZE", "false").lower() == "true"  # Normalize audio levels


def setup_ytmusicapi():
    """Guide user through ytmusicapi setup"""
    print("\n" + "="*60)
    print("YouTube Music API Setup")
    print("="*60)
    print("\nTo automatically discover your playlists, you need to authenticate.")
    print("\nOption 1: Using ytmusicapi (Recommended)")
    print("  1. Run: ytmusicapi browser")
    print("  2. Follow the instructions to paste headers")
    print("  3. Save as browser.json in this directory")
    print("\nOption 2: Manual cookie file")
    print("  Use browser cookies with yt-dlp")
    
    choice = input("\nSetup method (1=ytmusicapi, 2=cookies, skip): ").strip()
    
    if choice == "1":
        print("\nRun this command in your terminal:")
        print("  ytmusicapi browser")
        print("\nThen run this script again.")
        sys.exit(0)
    elif choice == "2":
        return None
    else:
        return None


def get_playlists_ytmusicapi():
    """Get playlists using ytmusicapi"""
    if not YTMUSIC_AVAILABLE:
        return None
    
    # Only check for browser.json (headers method)
    if not os.path.exists(BROWSER_AUTH_FILE):
        print(f"\n⚠ {BROWSER_AUTH_FILE} not found")
        return None
    
    try:
        print(f"\nUsing browser authentication ({BROWSER_AUTH_FILE})")
        print("Connecting to YouTube Music...")
        ytmusic = YTMusic(BROWSER_AUTH_FILE)
        
        print("Fetching your library playlists...")
        library_playlists = ytmusic.get_library_playlists(limit=None)
        
        playlists = []
        for pl in library_playlists:
            # Filter out playlists with Unknown track count (incompatible with yt-dlp)
            track_count = pl.get('count', 'Unknown')
            if track_count == 'Unknown' or track_count == '':
                print(f"  Skipping '{pl['title']}' (Unknown tracks - incompatible)")
                continue
            
            playlists.append({
                'title': pl['title'],
                'url': f"https://music.youtube.com/playlist?list={pl['playlistId']}",
                'count': track_count,
                'id': pl['playlistId']
            })
        
        return playlists
        
    except Exception as e:
        print(f"Error with ytmusicapi: {e}")
        return None

def create_cookies_file():
    """Create a Netscape cookie file from browser.json"""
    if not os.path.exists(BROWSER_AUTH_FILE):
        return None
    
    try:
        with open(BROWSER_AUTH_FILE, 'r') as f:
            auth_data = json.load(f)
        
        cookie_header = auth_data.get('cookie', '')
        if not cookie_header:
            return None
        
        # Create cookies.txt file
        cookies_file = os.path.join(os.getcwd(), 'cookies.txt')
        with open(cookies_file, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# This is a generated file! Do not edit.\n\n")
            
            # Parse cookies from the header
            cookies = cookie_header.split('; ')
            for cookie in cookies:
                if '=' in cookie:
                    name, value = cookie.split('=', 1)
                    # Format: domain, domain_specified, path, secure, expires, name, value
                    f.write(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
                    f.write(f".music.youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
        
        return cookies_file
    except Exception as e:
        print(f"Error creating cookies file: {e}")
        return None


def get_manual_playlists():
    """Manual fallback - your predefined playlists"""
    print("\n" + "="*60)
    print("Using predefined playlists...")
    print("="*60)
    
    return []


def download_playlist(playlist_info):
    """Download a single playlist using browser.json authentication"""
    title = playlist_info['title']
    url = playlist_info['url']
    count = playlist_info.get('count', 'Unknown')
    
    # Skip problematic playlists
    skip_playlists = ['Liked Music', 'Episodes for Later']
    if title in skip_playlists:
        print(f"\n⚠ Skipping {title} (not supported by yt-dlp)")
        return True
    
    # Sanitize folder name for Traktor compatibility
    # Remove problematic characters that can cause issues in DJ software
    safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
    safe_name = safe_name.strip().replace(' ', '_')
    # Remove multiple consecutive underscores
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
    
    # Create cookies.txt from browser.json if it exists
    cookies_file = create_cookies_file()
    
    # Detect Node.js path for JS runtime
    node_paths = [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        "node"  # If in PATH
    ]
    
    node_exe = None
    for path in node_paths:
        try:
            if path == "node":
                subprocess.run(["node", "--version"], capture_output=True, check=True)
                node_exe = "node"
            elif os.path.exists(path):
                node_exe = path
                break
        except:
            continue
    
    # Build command with Traktor-compatible settings
    cmd = [
        "yt-dlp",
        "-x", "--extract-audio",
        "--audio-format", AUDIO_FORMAT,
        "--audio-quality", AUDIO_QUALITY,  # 320kbps for best quality
        "--download-archive", archive_file,
        "--output", f"{playlist_dir}/%(artist)s - %(title)s.%(ext)s",  # Better naming for DJ software
        url,
        "--embed-thumbnail",
        "--embed-metadata",
        "--yes-playlist",
        "--ignore-errors",
        "--no-abort-on-error",
        "--extractor-args", "youtube:player_client=web,web_creator",
        "--no-warnings",
        # Traktor-specific improvements
        "--audio-multistreams",  # Handle multiple audio streams properly
        "--postprocessor-args", "ffmpeg:-avoid_negative_ts make_zero -c:a libmp3lame -b:a 320k -ac 2 -ar 44100",  # Force proper MP3 encoding
        "--restrict-filenames",  # Avoid problematic characters in filenames
    ]
    
    # Add JavaScript runtime if Node.js is available
    if node_exe:
        cmd.extend(["--js-runtimes", f"node:{node_exe}"])
        print(f"Using Node.js runtime: {node_exe}")
    else:
        print("⚠ Node.js not found - may have issues with some videos")
    
    # Add cookies if available
    if cookies_file and os.path.exists(cookies_file):
        cmd.extend(["--cookies", cookies_file])
        print("Using authentication from browser.json")
    else:
        print("⚠ No authentication - may have limited access")
    
    try:
        print(f"\nStarting download for {title}...")
        subprocess.run(cmd, check=False)
        
        # Check results
        audio_files = list(Path(playlist_dir).glob("*.mp3"))
        if len(audio_files) > 0:
            print(f"\n✓ Completed: {title} ({len(audio_files)} files)")
        else:
            print(f"\n⚠ No files downloaded for: {title}")
            print("This playlist may be empty, private, or all tracks were already downloaded")
        
        # Clean up cookies file
        if cookies_file and os.path.exists(cookies_file):
            try:
                os.remove(cookies_file)
            except:
                pass
        
        return True
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False


def main():
    """Main function"""
    print("\n" + "="*60)
    print("YouTube Music Playlist Downloader")
    print("="*60)
    
    # Check yt-dlp
    try:
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, check=True)
        print(f"✓ yt-dlp: {result.stdout.strip()}")
    except:
        print("✗ yt-dlp not found. Install with: pip install yt-dlp")
        sys.exit(1)
    
    # Configuration
    print(f"\n📁 Download location: {BASE_DIR}")
    print(f"🎵 Audio format: {AUDIO_FORMAT}")
    print(f"� Audio quality: {AUDIO_QUALITY}kbps CBR (Traktor optimized)")
    print(f"🌐 Authentication: Firefox Headers (browser.json)")
    
    # Get playlists using ytmusicapi with browser headers
    playlists = None
    
    # Only method: ytmusicapi with browser headers
    if YTMUSIC_AVAILABLE and os.path.exists(BROWSER_AUTH_FILE):
        print("\n🔍 Method: ytmusicapi (Firefox Browser Headers)")
        playlists = get_playlists_ytmusicapi()
    else:
        print("\n❌ browser.json not found!")
        print("\nTo set up authentication:")
        print("1. Run: ytmusicapi browser")
        print("2. Open Firefox and go to YouTube Music")
        print("3. Open Developer Tools (F12) -> Network tab")
        print("4. Filter by 'browse' and find a POST request")
        print("5. Copy request headers and paste when prompted")
        print("6. Run this script again")
        
        choice = input("\nRun ytmusicapi setup now? (y/n): ").strip().lower()
        if choice == 'y':
            print("\nRunning ytmusicapi browser setup...")
            try:
                subprocess.run(["ytmusicapi", "browser"], check=True)
                print("\n✓ Setup complete! Please run this script again.")
            except Exception as e:
                print(f"\n❌ Setup failed: {e}")
        
        sys.exit(1)
    
    # Ensure we have playlists (this should not happen with proper setup)
    if not playlists:
        print("\n❌ No playlists found!")
        sys.exit(1)
    
    # Display playlists
    print("\n" + "="*60)
    print(f"Found {len(playlists)} playlists:")
    print("="*60)
    
    for i, pl in enumerate(playlists, 1):
        print(f"{i:2d}. {pl['title']:<40} ({pl.get('count', '?')} tracks)")
    
    # # Confirm
    # print("\n" + "="*60)
    # confirm = input("Start downloading? (y/n): ").strip().lower()
    # if confirm == 'n':
    #     print("Cancelled")
    #     sys.exit(0)
    
    # Download
    print("\n" + "="*60)
    print("Starting downloads...")
    print("="*60)
    
    successful = 0
    failed = 0
    
    for i, playlist in enumerate(playlists, 1):
        print(f"\n📦 [{i}/{len(playlists)}]")
        try:
            if download_playlist(playlist):
                successful += 1
            else:
                failed += 1
        except KeyboardInterrupt:
            print("\n\n⚠ Cancelled by user")
            break
        except Exception as e:
            print(f"\n✗ Unexpected error: {e}")
            failed += 1
        
        # Add break between playlists to avoid rate limiting
        if i < len(playlists):  # Don't sleep after the last playlist
            print(f"\n💤 Taking a 10-second break...")
            try:
                time.sleep(10)
            except KeyboardInterrupt:
                print("\n⚠ Cancelled by user")
                break
    
    # Summary
    print("\n" + "="*60)
    print("✅ Download Summary")
    print("="*60)
    print(f"Successful: {successful}/{len(playlists)}")
    if failed > 0:
        print(f"Failed: {failed}")
    print(f"Location: {BASE_DIR}")
    print("="*60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)