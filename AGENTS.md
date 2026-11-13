# AGENTS.md

These instructions apply to every coding agent working in this repo. Claude Code loads them through the `@AGENTS.md` import in `CLAUDE.md`.

The owner is a hobbyist, not a git user. Drive git, tests, image builds and the board end to end. Check live state yourself with the read-only endpoints below. Don't ask the owner to run a command you could run.

## What this is

autoDownloader is a self-hosted YouTube Music service in one Docker container:

- **Player.** The web music player is the entrypoint (`/`) and plays the downloaded library.
- **Downloader.** It downloads the owner's YouTube Music playlists with yt-dlp, every night at 03:00.
- **Vibe sorter.** It files liked tracks into the owner's playlists by how they sound, learning from the playlists the owner already made.

## Layout

| Path | What |
|---|---|
| `app/app.py` | Flask app: player, downloads, auth, sorter pages and APIs. The music library API is the blueprint in `app/musiclib_api.py`. |
| `app/templates/` | `player.html` (entrypoint); `sort*.html` are the sorter pages, loaded in an iframe inside the player; `crontab` |
| `app/scripts/scheduler.py` | Nightly pipeline. Order matters: retrain the sorter, sort liked tracks, find misfiled tracks, then download playlists. Sorter failures are logged and stepped over, so downloads still run. |
| `app/scripts/download.py` | yt-dlp per playlist |
| `app/scripts/ytmusic_auth.py` | Loading auth headers, merged with the yt-dlp cookie jar |
| `app/scripts/vibe/` | Sorter package. Read its `README.md`. `app/scripts/vibe_*.py` are the command-line entry points. |
| `app/tests/` | pytest suite |
| `PLAN_music_player.md` | Plan and decisions for the player |
| `archive/` | Old code, not used |

State lives in JSON files under `/app/data` (there's no database). The data folders `app/data`, `app/downloads` and `app/music` are git-ignored.

## Build, test, run

- **Tests:** `make test` runs pytest inside the Docker image. This is the gate, because there is no CI. Some cron and download tests fail on plain Windows Python both with and without changes, so trust the Docker run.
- **Local sandbox:** `make dev` serves http://localhost:8081 with data in `./dev`. It never touches the NAS. `make dev-seed` adds sample tracks; `make dev-down` stops it.
- **Publish:** `wsl make push` runs the tests, builds with `--no-cache`, then pushes `cletusmccoy/auto-downloader:latest` to Docker Hub. Docker Hub login is set up in WSL Ubuntu.
- **Docker from Windows:** run docker and make through `wsl`.

## Live service

- **Where:** the `downloader` container on the TrueNAS host boxcart-v2, at https://downcart.kadanskonsult.be.
- **Read-only checks:**
  - `/auth/status`
  - `/api/runs` (the last 50 runs; `status` and `log_name`)
  - `/api/logs`
  - `/logs/<log_name>`
  - `/api/next-run`
- **Deploy:** after pushing a new image, redeploy through the deploy runner in the sibling repo `../homelab-inventory`:
  - dry run: `platform/scripts/runner/deploy.py run downloader boxcart-v2`
  - real deploy: add `--apply` to the same command
  - The direct playbook is `platform/scripts/run-playbook.sh playbooks/deploy-services.yml -l boxcart-v2 -e deploy_service=downloader -e deploy_force=true`.
  - Follow that repo's AGENTS.md for its worktree and token rules.

## YouTube Music auth

- **How it works:** in the player's Downloads view, the owner pastes request headers from a signed-in music.youtube.com tab. `_normalize_headers_raw` in `app.py` accepts three formats: plain `Name: value` lines, Chrome DevTools' alternating name and value lines, and Firefox's Copy Request Headers. ytmusicapi then writes `data/auth/headers_auth.json`.
- **CRLF trap:** a browser submits the paste box with CRLF line endings. ytmusicapi splits on `\n` only, so a stray `\r` ends up in every header value and the UI reports "Credentials expired". The normalizer strips it, and `load_headers()` repairs a stored file in place.
- **Cookie jar:** the yt-dlp jar `data/auth/cookies.txt` is folded into the headers when the jar is newer than the headers file, by file modification time. Keep that ordering intact.
- **OAuth:** the code is there but hidden, because Google rejects custom OAuth clients.

## Vibe sorter

- **Model:** discogs-effnet embeddings (CPU, essentia-tensorflow) and a classifier per playlist. It runs on 60 s preview snippets, capped at `VIBE_MAX_NEW_AUDIO` new snippets per night.
- **Goal:** the owner has 25+ playlists, and each track goes into exactly one. Optimise **precision on the tracks the sorter files automatically**, not overall accuracy. Tracks it isn't confident about stay in Liked.
- **Gate:** a playlist is only routed automatically when its nested-CV precision is near 90%.
- **Playlist names:** they carry numbered genre prefixes such as `28. UNSORTED TECH`. **Never infer a playlist's role from its name.** UNSORTED TECH is a real vibe, not a holding bin.
- **GPU work** for experiments goes through the Turnstile queue (turnstile.kadanskonsult.be; read its `/agents.md`). Never run GPU work outside the queue. Run CPU experiments at idle priority with a few threads, because the machine is shared.

## Git and board

- **Repo:** the Forgejo instance at https://git.kadanskonsult.be/cletus-mccoy/autoDownloader, remote `origin`. The old GitHub copy is remote `github`. The owner's git credentials can push feature branches.
- **Branches:** work on `<type>/<slug>` branches and merge to `main` through PRs on the forge. Don't push `main` unless asked.
- **Board:** track work on **Ticky**, board **AutoDownloader**, keeping cards current as you go. Use the CLI `platform/scripts/ticky/ticky.sh --board AutoDownloader` from `../homelab-inventory`. Run it in WSL with the Ansible venv `~/.venv/ansible/bin` on PATH.
- **Card rules:**
  - Card text must not contain `$`, backticks, double quotes or curly braces, and must not contain local file paths.
  - Start each card with the handoff header: Branches / Base / PRs / State / Verify / Next.
  - Never move your own work to Done; a second runner verifies it.

## Secrets

- **Cookies and headers:** never commit, print or paste cookies, auth headers, `headers_auth.json` or `cookies.txt`. They hold account-wide Google session tokens. A leaked `browser.json` in 2026-08 forced a history purge.
- **Tokens:** the owner types tokens in (Turnstile, the LLM gateway) or keeps them in user-only files outside the Syncthing-synced `Documents/Visual Studio Code` folder. Never echo them.
