#!/bin/sh
# Generates a few tagged sample tracks (some with cover art) inside the dev folders. Runs in the image (has ffmpeg).
set -e
mk() { # file artist album title track hz art(0/1)
  dir=$(dirname "$1"); mkdir -p "$dir"
  if [ "$7" = 1 ]; then
    ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=$6:duration=${DUR:-45}" -f lavfi -i "color=c=0x$(printf '%06x' $(( $6 * 977 % 16777215 ))):s=400x400" \
      -map 0 -map 1 -frames:v 1 -c:a libmp3lame -id3v2_version 3 -metadata:s:v title="Cover" -metadata:s:v comment="Cover (front)" \
      -metadata title="$4" -metadata artist="$2" -metadata album="$3" -metadata track="$5" "$1"
  else
    ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=$6:duration=${DUR:-45}" -metadata title="$4" -metadata artist="$2" -metadata album="$3" -metadata track="$5" "$1"
  fi
}
D=/app/downloads/Sample_Playlist; M=/app/music
mk "$D/Aurora Tones - Dawn.mp3"        "Aurora Tones" "Sunrise EP" "Dawn"        1 261 1
mk "$D/Aurora Tones - Midday.mp3"      "Aurora Tones" "Sunrise EP" "Midday"      2 329 1
mk "$D/Aurora Tones - Dusk.mp3"        "Aurora Tones" "Sunrise EP" "Dusk"        3 392 1
mk "$M/Low Hum/Night/01 Static.flac"   "Low Hum"      "Night"      "Static"      1 110 0
mk "$M/Low Hum/Night/02 Drift.flac"    "Low Hum"      "Night"      "Drift"       2 146 0
# duplicate across two download playlists (remix vs original, same artist)
mk "/app/downloads/40._EUROTRANCE/BL3SS - Craving 4 U [DAIRE Remix].mp3" "BL3SS, CamrinWatsin" "" "Craving 4 U [DAIRE Remix] (feat. bbyclose)" 0 300 0
mk "/app/downloads/62._SPEED_GARAGE/BL3SS - Craving 4 U.mp3"             "BL3SS, CamrinWatsin" "" "Craving 4 U (feat. bbyclose)"             0 301 0
# tagged files in a messy local layout, for the organizer
mk "$M/_unsorted/dl_0001.flac"     "Glass Piano" "Quiet Hours" "Opening" 1 200 0
mk "$M/_unsorted/old/x.flac"       "Glass Piano" "Quiet Hours" "Closing" 2 210 0
mk "$M/Untagged - Mystery Track.mp3"   ""             ""           ""            0 523 0
# untagged: strip tags so the filename fallback is exercised
ffmpeg -loglevel error -y -i "$M/Untagged - Mystery Track.mp3" -map_metadata -1 -c copy "$M/tmp.mp3" && mv "$M/tmp.mp3" "$M/Untagged - Mystery Track.mp3"
echo "seeded:"; find /app/downloads /app/music -type f | sort
