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
from pathlib import Path

try:
    from ytmusicapi import YTMusic
    YTMUSIC_AVAILABLE = True
except ImportError:
    YTMUSIC_AVAILABLE = False
    print("⚠ ytmusicapi not installed. Install with: pip install ytmusicapi")

# Configuration
BASE_DIR = r"C:\Users\kaspe\Desktop\MUSIC\TEST_2"
BROWSER = "firefox"  # Options: chrome, firefox, edge, opera, brave
AUDIO_FORMAT = "mp3"
AUTH_FILE = "oauth.json"  # Try OAuth first, then browser.json
BROWSER_AUTH_FILE = "browser.json"  # Fallback auth file


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
    """Fallback: Get playlists using browser cookies with yt-dlp"""
    print("\n" + "="*60)
    print("Discovering playlists using browser cookies...")
    print("="*60)
    
    # First, let's try to get the user's "Liked Music" and work from there
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--cookies-from-browser", browser,
        "https://music.youtube.com/library/playlists"
    ]
    
    try:
        print(f"\nExtracting cookies from {browser}...")
        print("(Make sure you're logged into YouTube Music in your browser)")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if "ERROR" in result.stderr and "cookie" in result.stderr.lower():
            print(f"\n⚠ Cookie extraction failed")
            print(f"Try: 1) Closing {browser} completely")
            print("     2) Using a different browser (edit BROWSER in script)")
            return None
        
        if result.returncode != 0:
            print("\nCouldn't access library playlists")
            return None
        
        data = json.loads(result.stdout)
        playlists = []
        
        if 'entries' in data:
            for entry in data['entries']:
                if entry:
                    playlist_id = entry.get('id', '')
                    if playlist_id:
                        playlists.append({
                            'title': entry.get('title', f'Playlist {playlist_id}'),
                            'url': f"https://music.youtube.com/playlist?list={playlist_id}",
                            'count': entry.get('playlist_count', 'Unknown'),
                            'id': playlist_id
                        })
        
        return playlists if playlists else None
        
    except subprocess.TimeoutExpired:
        print("Timeout - browser might be locked")
        return None
    except json.JSONDecodeError:
        print("Could not parse playlist data")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def get_manual_playlists():
    """Manual fallback - your predefined playlists"""
    print("\n" + "="*60)
    print("Using predefined playlists...")
    print("="*60)
    
    return []


def download_playlist(playlist_info, browser=BROWSER, use_cookies_file=False, cookies_file=None):
    """Download a single playlist"""
    title = playlist_info['title']
    url = playlist_info['url']
    count = playlist_info.get('count', 'Unknown')
    
    # Skip problematic playlists
    skip_playlists = ['Liked Music', 'Episodes for Later']
    if title in skip_playlists:
        print(f"\n⚠ Skipping {title} (not supported by yt-dlp)")
        return True
    
    # Sanitize folder name
    safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
    safe_name = safe_name.strip().replace(' ', '_')
    
    playlist_dir = os.path.join(BASE_DIR, safe_name)
    archive_file = os.path.join(playlist_dir, 'downloaded.txt')
    
    os.makedirs(playlist_dir, exist_ok=True)
    
    print("\n" + "="*60)
    print(f"Downloading: {title}")
    print(f"Tracks: {count}")
    print(f"Saving to: {safe_name}/")
    print("="*60)
    
    # Build command - Use simple approach with explicit JS runtime options
    cmd = [
        "yt-dlp",
        "-x", "--extract-audio",
        "--audio-format", AUDIO_FORMAT,
        "--download-archive", archive_file,
        "--output", f"{playlist_dir}/%(title)s.%(ext)s",
        url,
        "--embed-thumbnail",
        "--embed-metadata",
        "--yes-playlist",
        "--ignore-errors",
        "--no-abort-on-error",
        "--js-runtimes", "node:C:\\Program Files\\nodejs\\node.exe",  # Explicit JS runtime
        "--extractor-args", "youtube:player_client=ios,android,web",  # Use mobile clients to avoid SABR
        "--no-warnings",  # Suppress SABR and other non-critical warnings
    ]
    
    # Add authentication using cookies file
    cookies_file_path = create_cookies_file()
    if cookies_file_path:
        cmd.extend(["--cookies", cookies_file_path])
        print("Using cookies file for authentication")
    else:
        # Fallback to browser cookies
        cmd.extend(["--cookies-from-browser", browser])
        print(f"Extracting cookies from {browser}")
    
    try:
        subprocess.run(cmd, check=False)
        print(f"\n✓ Completed: {title}")
        
        # Clean up cookies file
        if cookies_file_path and os.path.exists(cookies_file_path):
            os.remove(cookies_file_path)
        
        return True
    except KeyboardInterrupt:
        # Clean up cookies file on interrupt
        if cookies_file_path and os.path.exists(cookies_file_path):
            os.remove(cookies_file_path)
        raise
    except Exception as e:
        print(f"\n✗ Error: {e}")
        # Clean up cookies file
        if cookies_file_path and os.path.exists(cookies_file_path):
            os.remove(cookies_file_path)
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
    print(f"🌐 Browser: {BROWSER}")
    
    # Get playlists using best available method
    playlists = None
    
    # Method 1: Try ytmusicapi with browser headers
    if YTMUSIC_AVAILABLE and os.path.exists(BROWSER_AUTH_FILE):
        print("\n🔍 Method: ytmusicapi (Browser Headers)")
        playlists = get_playlists_ytmusicapi()
    
    # Method 2: Manual fallback if API fails
    if not playlists:
        print("\n🔍 Method: Manual playlist input")
        print("\nAutomatic discovery failed. Please provide YouTube Music playlist URLs manually.")
        print("\nYou can:")
        print("1. Copy playlist URLs from YouTube Music")
        print("2. Paste them when prompted")
        print("3. The script will download them using yt-dlp")
        
        # Manual playlist input
        playlists = []
        print("\n" + "="*60)
        print("Manual Playlist Input")
        print("="*60)
        
        while True:
            url = input("\nEnter YouTube Music playlist URL (or press Enter to finish): ").strip()
            if not url:
                break
            
            if "music.youtube.com/playlist" in url or "youtube.com/playlist" in url:
                # Extract playlist name from URL or ask for it
                playlist_name = input(f"Enter name for this playlist (or press Enter for auto-name): ").strip()
                if not playlist_name:
                    # Try to extract ID from URL for auto-naming
                    if "list=" in url:
                        playlist_id = url.split("list=")[1].split("&")[0]
                        playlist_name = f"Playlist_{playlist_id[:8]}"
                    else:
                        playlist_name = f"Playlist_{len(playlists)+1}"
                
                playlists.append({
                    'title': playlist_name,
                    'url': url,
                    'count': '?',
                    'id': url.split("list=")[1].split("&")[0] if "list=" in url else 'unknown'
                })
                print(f"✓ Added: {playlist_name}")
            else:
                print("⚠ Invalid URL. Please enter a valid YouTube Music playlist URL.")
    
    if not playlists:
        print("\n✗ No playlists provided!")
        print("\nTo find playlist URLs:")
        print("  1. Go to YouTube Music in your browser")
        print("  2. Navigate to a playlist")
        print("  3. Copy the URL from the address bar")
        print("  4. Run this script again")
        sys.exit(1)
    
    # Display playlists
    print("\n" + "="*60)
    print(f"Found {len(playlists)} playlists:")
    print("="*60)
    
    for i, pl in enumerate(playlists, 1):
        print(f"{i:2d}. {pl['title']:<40} ({pl.get('count', '?')} tracks)")
    
    # Confirm
    print("\n" + "="*60)
    confirm = input("Start downloading? (y/n): ").strip().lower()
    if confirm == 'n':
        print("Cancelled")
        sys.exit(0)
    
    # Download
    print("\n" + "="*60)
    print("Starting downloads...")
    print("="*60)
    
    successful = 0
    failed = 0
    
    for i, playlist in enumerate(playlists, 1):
        print(f"\n📦 [{i}/{len(playlists)}]")
        try:
            if download_playlist(playlist, BROWSER):
                successful += 1
            else:
                failed += 1
        except KeyboardInterrupt:
            print("\n\n⚠ Cancelled by user")
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