# Plan: autoDownloader → self-hosted web music player

## Decisions
- **The player is the main entrypoint** (`/`). The current dashboard is retired as a landing page.
- **Downloading and sorting are functions inside the player**, not a separate admin site:
  - Downloads: a "Downloads" view in the player (YT Music auth, playlist selection, run/stop, schedule, logs, single-URL download).
  - Sorting: an "Import & Organize" function (rename/move into `Artist/Album/NN - Title.ext` from tags, duplicate detection, tag fixes).
- Still open (defaults assumed): LAN trust vs. shared password (default: optional shared password via env var), mount an existing NAS music folder read-only as a library root (default: yes), tag editing deferred past v1.

## Current state
- Single Flask file (`app/app.py`), no DB; state is JSON files in `/app/data`, music tree in `/app/downloads`.
- `download.py` runs yt-dlp per YT Music playlist; auth via pasted headers; cron scheduling.
- Already present: mutagen metadata endpoint, ID3 cover-art endpoint.
- Missing: audio streaming, player UI, library index, local import.
- Deploy: Docker via `make push` (from repo root); user runs `docker login`/push and tells the homelab session to deploy.

## Architecture
Single container, Flask + SQLite index (`/app/data/library.db`), server-rendered templates + vanilla JS.
Library roots: `/app/downloads` (YT), `/app/music` (imports / mounted collection).

```
Browser (player UI: <audio>, MediaSession, queue, search, playlists, downloads, import)
   │  JSON + Range-streamed audio
Flask app: /api/library/*  /api/stream/<id>  /api/import/*  /api/downloads/* (existing, moved under player UI)
   │
SQLite index  +  filesystem roots
```

## Phases

### 1. Library index
- `app/scripts/library.py`: tables `tracks`, `playlists`, `playlist_tracks` (+ optional `play_history`).
- Incremental scanner (skip unchanged mtime/size, remove deleted, filename fallback `Artist - Title`).
- Triggers: startup, after each download run, after import, `POST /api/library/rescan`; background thread + `/api/library/scan-status`.
- Art extraction beyond ID3 (FLAC, MP4, Vorbis) with thumbnail cache.
- Replace `os.walk` in `get_files` / `get_download_size` / `downloads_metadata` with index queries.

### 2. Streaming + library API
- `GET /api/stream/<id>` via `send_file(conditional=True)` (Range/seek), path-traversal guard.
- `/api/library/tracks|artists|albums`, `/api/art/<id>?size=`, FTS5 search.
- Local playlist CRUD (`/api/playlists/local`, separate from existing YT `/api/playlists`).
- SQLite WAL; keep DB on local volume if bind-mount locking misbehaves (cf. `_persist_run` atomic writes).

### 3. Player UI (main entrypoint)
- `/` = player: sidebar (Library, Artists, Albums, Playlists, **Downloads**, **Import & Organize**, Settings), main list/grid with search, persistent bottom bar.
- Queue (next/add/shuffle/repeat), seek, volume, Media Session API, keyboard shortcuts, queue/position restored from `localStorage`.
- Responsive for phone on LAN.
- Existing dashboard functionality (auth, playlist selection, run/stop, cron, logs, run history) is re-homed into the Downloads view; old routes kept as API.

### 4. Downloads inside the player
- Downloads view: auth status/headers paste, playlist selection, run now/stop with live log stream, cron editor, run history.
- Single URL/track download box.
- Library auto-rescans on completion; new tracks appear live.
- Fix: honor `YT_DLP_QUALITY`/`YT_DLP_CODEC` (currently hardcoded in postprocessor args); "clear downloads" must also purge index rows.

### 5. Import & Organize (sorting)
- Ingest: browser upload (files or folder via `webkitdirectory`, multipart, progress, extension validation, sanitized names) → `/app/music`; or scan a mounted folder (read-only root) via Settings.
- Organize: copy/move into `Artist/Album/NN - Title.ext` from tags; apply to downloads too (optional); duplicate detection (tags + duration/hash); preview-before-apply with dry-run; fix missing tags from filename.
- Optional later: tag editor (title/artist/album/art) via mutagen.

### 6. Security and ops
- Optional shared password/token via env var (`before_request`); upload size cap.
- Run under gunicorn instead of Flask dev server.
- Dockerfile: copy new templates/static, add gunicorn; compose volumes `./music:/app/music`, `./data:/app/data`, `./downloads:/app/downloads`.

### 7. Tests and rollout
- pytest: scanner (generated audio fixtures), library/search/playlist/import APIs, Range streaming (206), path traversal, organize dry-run.
- `make push` from repo root; user does docker login/push, then tells homelab session to deploy.
- Back up `/app/data` before first deploy; rollback = delete `library.db` (music files untouched except explicit organize/tag actions).

## Build order
1. Phases 1–2 (index + stream API)
2. Phase 3 (player UI as `/`)
3. Phase 4 (downloads in player) 
4. Phase 5 (import, then organize)
5. Phase 6–7 (auth, gunicorn, tests, deploy), then polish (playlists, tag editing, gapless, lyrics)

## Sorting vs. playlists vs. external services (design)
- **Two separate concerns:** file layout (organize) and playlist membership. Playlists reference track IDs, never paths.
- **Organize** moves files and updates the existing DB row in place (`relpath` changes, `id` stays), journaled, with dry-run preview. Never deletes.
- **Stable remote identity:** `tracks.ext_provider` + `tracks.ext_id` (YT videoId), recorded at download time (not derived from filenames/tags).
- **Linked playlists:** `provider`, `remote_id`, sync mode = `mirror` (default, remote→local) | `two-way`/`push` (opt-in, diff preview, ytmusicapi add/remove items, only tracks with `ext_id`) | `local`.
- **Provider interface:** `list_playlists`, `get_tracks`, `download`, `add_items`, `remove_items`; YT Music first, others later.
- **Downloader change (Phase 4):** one shared pool + single archive keyed by video ID instead of per-playlist folders/`downloaded.txt`; membership comes from linked playlists. Migrate existing folders carefully.
- **Tag editing** stays on the radar: `library.py` keys rows by (root, relpath) and re-reads on mtime change, so a `write_tags` + in-place row update fits without schema changes.

## Tracks missing on a remote service
- Membership is independent of remote state; per playlist+track `remote_state` ∈ `synced | local_only | remote_only | unavailable | pending_match`.
- Local track with no remote counterpart: stays in local playlist as `local_only`; push sync skips it and lists it in the diff ("not on remote").
- Optional match: search remote by artist/title/duration, user confirms candidates; never auto-add on fuzzy match.
- Optional explicit upload (YT Music upload via ytmusicapi) as a separate opt-in action, not part of regular sync.
- Remote track unfetchable (removed/region-locked/download fails): `unavailable`, retried on later runs, shown in Downloads view.
- Mirror sync never deletes local files because the remote dropped a track (flag `remote_gone` instead).
- Sync = compute states → show diff → apply only approved changes.

## UI: "Sync issues" view (track and remedy missing remotes)
- Lives in the player sidebar (badge with issue count), next to Downloads. Built after linked playlists + `remote_state` exist (Phase 4); the Phase 3 sidebar reserves the slot.
- Lists every track/playlist pair whose `remote_state` is not `synced`, grouped by playlist and filterable by state (`local_only`, `unavailable`, `pending_match`, `remote_only`, `remote_gone`).
- Per-row actions (single and bulk):
  - `local_only`: **Find match** (shows remote candidates with artist/title/duration, user confirms), **Upload** (explicit opt-in), **Keep local** (dismiss, remembered).
  - `unavailable`: **Retry download**, **Search alternative source**, **Ignore**.
  - `remote_only`: **Download**, **Remove from remote** (confirm).
  - `remote_gone`: **Keep local** or **Re-add to remote**.
- Every action is previewed first and logged; nothing is deleted without confirmation.
- API: `GET /api/sync/issues`, `POST /api/sync/issues/<id>/resolve {action, ...}`, `GET /api/sync/issues/<id>/candidates`.

## Progress (as built)
- Done: library index, streaming API, player UI (`/`), Home start page, Downloads view, playlists (local + auto "folder" playlists from download folders), column-header sorting, Media tab (Folders / Organize / Duplicates / Needs tags), `make dev` sandbox.
- **Organize** (local media root only; download folders = playlists, untouched): plan `Artist/Album/NN - Title.ext`, in-place row update keeps track ids/playlists, never overwrites, last run undoable (`organize_log.json`).
- **Duplicates**: same first-artist + title ignoring `[remix]`/`(feat.)` markers. Resolve = keep one copy; losers move to `<root>/.trash/<stamp>/` (reversible, not indexed), local playlists switch to the kept copy, download-folder playlists lose the file. Decisions persist (`dup_decisions`); a removed copy that returns is flagged "Back again". "Keep both" is remembered; "Later →" is session-only.
- Still open: in-app upload/folder import, tag editing (`write_tags`), shared download pool + archive keyed by video id, linked remote playlists + Sync issues view, gunicorn + optional password, trash restore/empty UI, auth-status fix verified against a real session.

## Reconciliation with origin/main (the existing sorter)
- origin/main already contains the sorter (`app/scripts/vibe/`: embedding-based routing of new likes, misfiled review, duplicates queue, stats, housekeeping) with pages `/sort`, `/sort/misfiled`, `/sort/duplicates`, `/sort/stats` (all support `?embed=1`).
- This branch builds the player on top of it: `/` = player, `/downloads` = the existing dashboard (OAuth login, logs, housekeeping, clear actions).
- Player sidebar: **Sort** group (New likes / Misfiled / Duplicates / Stats, live counts from `/api/sort/*`) hosting those pages in the main pane; Home leads with the same counts.
- Local-only features added by the player (musiclib): library index, streaming, playlists (local + download folders), Media tab (Folders / Organize / Needs tags). The earlier duplicate resolver for local files was dropped in favour of the sorter's duplicates queue.
- Module is `scripts/musiclib.py` (not `library.py`) because `vibe/library.py` imports as a top-level `library`.
