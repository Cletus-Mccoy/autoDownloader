@echo off
REM YouTube Music Playlist Downloader - Setup and Run Script
REM This script will:
REM 1. Set up ytmusicapi authentication using Chrome
REM 2. Run the playlist downloader

echo ============================================================
echo YouTube Music Playlist Downloader Setup and Run
echo ============================================================
echo.

REM Add Node.js to PATH for yt-dlp JavaScript runtime support
set "NODE_PATH=C:\Program Files\nodejs"
if not exist "%NODE_PATH%\node.exe" (
    set "NODE_PATH=C:\Program Files (x86)\nodejs"
)
if not exist "%NODE_PATH%\node.exe" (
    set "NODE_PATH=%PROGRAMFILES%\nodejs"
)
if not exist "%NODE_PATH%\node.exe" (
    echo Warning: Node.js not found in standard locations
    echo yt-dlp may have issues with JavaScript challenges
    echo Install Node.js from https://nodejs.org/
) else (
    echo Node.js found at: %NODE_PATH%
    set "PATH=%NODE_PATH%;%PATH%"
)

REM Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python and try again
    pause
    exit /b 1
)

echo Python detected: 
python --version

echo.
echo ============================================================
echo Step 1: Installing required packages
echo ============================================================

REM Install required packages with all dependencies for JavaScript solving
echo Installing ytmusicapi, yt-dlp and JavaScript runtime dependencies...
pip install -r requirements.txt
pip install --upgrade yt-dlp[default]
pip install --upgrade yt-dlp-ejs js2py phantomjs-binary

REM Verify yt-dlp installation
echo.
echo Verifying yt-dlp installation...
yt-dlp --version
if %errorlevel% neq 0 (
    echo Error: yt-dlp installation failed
    pause
    exit /b 1
)

if %errorlevel% neq 0 (
    echo Error: Failed to install packages
    pause
    exit /b 1
)

echo.
echo Packages installed successfully!

echo.
echo ============================================================
echo Step 2: YouTube Music Authentication Setup
echo ============================================================

REM Check if browser.json already exists
if exist "browser.json" (
    echo Browser authentication found (browser.json)
    set /p "overwrite=Do you want to recreate authentication? (y/n): "
    if /i not "%overwrite%"=="y" (
        goto :skip_auth
    )
) else (
    echo No browser authentication found
    set /p "setup_auth=Do you want to set up authentication? (y/n): "
    if /i not "%setup_auth%"=="y" (
        goto :skip_auth
    )
)

echo.
echo Setting up YouTube Music authentication...
echo.
echo CRITICAL: Follow these steps exactly (from official ytmusicapi docs):
echo.
echo 1. Open YouTube Music: https://music.youtube.com IN FIREFOX
echo 2. Make sure you are logged in to YOUR account
echo 3. Press F12 (Developer Tools) and select "Network" tab
echo 4. Filter by "/browse" using the search bar
echo 5. Scroll down or click "Library" button to trigger a browse request
echo 6. Find a POST request to music.youtube.com with "browse" in the URL
echo 7. Verify: Status 200, Method POST, Domain music.youtube.com
echo 8. Right-click ^> Copy ^> Copy request headers
echo 9. Paste ALL headers when prompted (Firefox headers only!)
echo.
echo Press any key when you have the correct POST /browse headers ready...
pause >nul

ytmusicapi browser

if %errorlevel% equ 0 (
    echo.
    echo ✓ Authentication setup successful!
) else (
    echo.
    echo ⚠ Authentication setup may have failed
    echo Check that you used headers from a POST /browse request
)

:skip_auth
echo.
echo Authentication setup completed!

echo.
echo ============================================================
echo Step 3: Running the Playlist Downloader
echo ============================================================

echo Starting the YouTube Music Playlist Downloader...
echo.

REM Run the downloader script
python yt_music_playlists.py

echo.
echo ============================================================
echo Download process completed!
echo ============================================================

pause