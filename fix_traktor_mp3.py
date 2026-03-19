"""
Fix MP3 Files for Traktor DJ Software Compatibility

This script re-encodes existing MP3 files to ensure they work properly in Traktor.
Common issues fixed:
- Variable bitrate -> Constant bitrate (320kbps CBR)
- Incorrect sample rate -> 44.1kHz stereo
- Metadata corruption
- Audio timing issues

Requirements:
- ffmpeg (download from https://ffmpeg.org/download.html)
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

# Configuration
MUSIC_DIR = r"C:\Users\kaspe\Desktop\MUSIC\TEST_1"  # Change this to your music directory
TARGET_BITRATE = "320k"  # 320kbps constant bitrate
TARGET_SAMPLE_RATE = "44100"  # 44.1kHz
BACKUP_ORIGINALS = True  # Set to False to overwrite originals without backup


def check_ffmpeg():
    """Check if ffmpeg is available"""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("✓ FFmpeg is available")
            return True
        else:
            print("❌ FFmpeg not working properly")
            return False
    except FileNotFoundError:
        print("❌ FFmpeg not found!")
        print("\nTo install FFmpeg:")
        print("1. Download from https://ffmpeg.org/download.html")
        print("2. Extract and add to your PATH environment variable")
        print("3. Or install via package manager (e.g., chocolatey: choco install ffmpeg)")
        return False
    except Exception as e:
        print(f"❌ Error checking FFmpeg: {e}")
        return False


def analyze_mp3_file(file_path):
    """Analyze MP3 file properties"""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 
            'format=bit_rate,sample_rate,duration', 
            '-show_entries', 'stream=channels,codec_name',
            '-of', 'csv=p=0', str(file_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            stream_info = lines[0].split(',') if len(lines) > 0 else ['', '']
            format_info = lines[1].split(',') if len(lines) > 1 else ['', '', '']
            
            return {
                'channels': stream_info[0] if len(stream_info) > 0 else 'unknown',
                'codec': stream_info[1] if len(stream_info) > 1 else 'unknown',
                'bit_rate': format_info[0] if len(format_info) > 0 else '0',
                'sample_rate': format_info[1] if len(format_info) > 1 else '0',
                'duration': format_info[2] if len(format_info) > 2 else '0'
            }
    except Exception as e:
        print(f"    ⚠ Error analyzing {file_path.name}: {e}")
    
    return None


def needs_fixing(file_info):
    """Determine if file needs to be re-encoded for Traktor"""
    if not file_info:
        return True, "Could not analyze file"
    
    issues = []
    
    # Check bitrate
    try:
        bit_rate = int(file_info.get('bit_rate', '0'))
        if bit_rate != 320000:  # Not 320kbps
            issues.append(f"bitrate: {bit_rate//1000}kbps → 320kbps")
    except (ValueError, TypeError):
        issues.append("bitrate: unknown → 320kbps")
    
    # Check sample rate
    try:
        sample_rate = int(file_info.get('sample_rate', '0'))
        if sample_rate != 44100:  # Not 44.1kHz
            issues.append(f"sample rate: {sample_rate}Hz → 44100Hz")
    except (ValueError, TypeError):
        issues.append("sample rate: unknown → 44100Hz")
    
    # Check channels
    channels = file_info.get('channels', 'unknown')
    if channels != '2':
        issues.append(f"channels: {channels} → stereo")
    
    return len(issues) > 0, "; ".join(issues)


def fix_mp3_file(file_path):
    """Fix MP3 file for Traktor compatibility"""
    print(f"  🔧 Processing: {file_path.name}")
    
    # Analyze current file
    file_info = analyze_mp3_file(file_path)
    needs_fix, reason = needs_fixing(file_info)
    
    if not needs_fix:
        print(f"    ✓ Already compatible")
        return True
    
    print(f"    📝 Issues: {reason}")
    
    # Create backup if requested
    backup_path = None
    if BACKUP_ORIGINALS:
        backup_path = file_path.with_suffix('.original.mp3')
        try:
            shutil.copy2(file_path, backup_path)
            print(f"    💾 Backup created: {backup_path.name}")
        except Exception as e:
            print(f"    ❌ Failed to create backup: {e}")
            return False
    
    # Create temporary output file
    temp_path = file_path.with_suffix('.temp.mp3')
    
    try:
        # Build ffmpeg command for Traktor-optimized encoding
        cmd = [
            'ffmpeg', '-i', str(file_path),
            '-c:a', 'libmp3lame',           # Use LAME MP3 encoder
            '-b:a', TARGET_BITRATE,         # Constant bitrate
            '-ac', '2',                     # Stereo
            '-ar', TARGET_SAMPLE_RATE,      # 44.1kHz sample rate
            '-avoid_negative_ts', 'make_zero',  # Fix timing issues
            '-map_metadata', '0',           # Preserve metadata
            '-id3v2_version', '3',          # Use ID3v2.3 (more compatible)
            '-write_id3v1', '1',            # Also write ID3v1 for compatibility
            '-y',                           # Overwrite output file
            str(temp_path)
        ]
        
        # Run conversion
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode == 0 and temp_path.exists():
            # Verify the output file
            new_info = analyze_mp3_file(temp_path)
            if new_info:
                # Replace original with fixed version
                temp_path.replace(file_path)
                print(f"    ✅ Fixed successfully")
                
                # Remove backup if user doesn't want it
                if backup_path and backup_path.exists() and not BACKUP_ORIGINALS:
                    backup_path.unlink()
                
                return True
            else:
                print(f"    ❌ Output file verification failed")
                temp_path.unlink()  # Clean up
                return False
        else:
            print(f"    ❌ Conversion failed")
            if result.stderr:
                print(f"    Error: {result.stderr[:200]}")
            if temp_path.exists():
                temp_path.unlink()  # Clean up
            return False
    
    except subprocess.TimeoutExpired:
        print(f"    ⏰ Conversion timeout")
        if temp_path.exists():
            temp_path.unlink()
        return False
    except Exception as e:
        print(f"    ❌ Unexpected error: {e}")
        if temp_path.exists():
            temp_path.unlink()
        return False


def fix_directory(directory):
    """Fix all MP3 files in directory and subdirectories"""
    directory_path = Path(directory)
    
    if not directory_path.exists():
        print(f"❌ Directory not found: {directory}")
        return 0, 0
    
    print(f"\n🔍 Scanning {directory} for MP3 files...")
    
    # Find all MP3 files
    mp3_files = list(directory_path.rglob("*.mp3"))
    
    if not mp3_files:
        print("No MP3 files found.")
        return 0, 0
    
    print(f"Found {len(mp3_files)} MP3 files")
    
    if BACKUP_ORIGINALS:
        print("📁 Backups will be created (.original.mp3)")
    else:
        print("⚠ Original files will be overwritten (no backups)")
    
    # Confirm
    confirm = input("\nProceed with fixing files? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        return 0, 0
    
    # Process files
    fixed_count = 0
    failed_count = 0
    
    print(f"\n🔧 Processing {len(mp3_files)} files...")
    print("=" * 60)
    
    for i, mp3_file in enumerate(mp3_files, 1):
        print(f"\n[{i}/{len(mp3_files)}]")
        
        try:
            if fix_mp3_file(mp3_file):
                fixed_count += 1
            else:
                failed_count += 1
        except KeyboardInterrupt:
            print("\n\n⚠ Cancelled by user")
            break
        except Exception as e:
            print(f"    ❌ Unexpected error: {e}")
            failed_count += 1
    
    return fixed_count, failed_count


def main():
    """Main function"""
    print("=" * 60)
    print("MP3 Traktor Compatibility Fixer")
    print("=" * 60)
    
    # Check ffmpeg
    if not check_ffmpeg():
        sys.exit(1)
    
    # Use global MUSIC_DIR as starting point
    music_directory = MUSIC_DIR
    
    # Configuration display
    print(f"\n📁 Music directory: {music_directory}")
    print(f"🎵 Target format: {TARGET_BITRATE} CBR, {TARGET_SAMPLE_RATE}Hz, stereo")
    print(f"💾 Create backups: {'Yes' if BACKUP_ORIGINALS else 'No'}")
    
    # Allow custom directory
    custom_dir = input(f"\nUse different directory? (Enter path or press Enter for default): ").strip()
    if custom_dir:
        music_directory = custom_dir
    
    # Process files
    fixed, failed = fix_directory(music_directory)
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    print(f"Files processed: {fixed + failed}")
    print(f"Successfully fixed: {fixed}")
    if failed > 0:
        print(f"Failed: {failed}")
    
    if fixed > 0:
        print(f"\n✅ {fixed} files have been optimized for Traktor!")
        print("Your MP3 files should now load and play properly in Traktor.")
    
    print("\n💡 Tips for Traktor:")
    print("- Restart Traktor after fixing files")
    print("- Re-analyze tracks in Traktor for best results")
    print("- Check that file paths don't contain special characters")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)