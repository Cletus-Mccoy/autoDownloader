@echo off
cd /d "C:\Users\kaspe\Documents\Visual Studio Code\autoDownloader"
set PYTHONIOENCODING=utf-8
call .venv\Scripts\activate.bat
python yt_music_playlists_auto.py >> log.txt 2>&1