# Vibe sorter

Sorts liked YouTube Music tracks into playlists by how they *sound*, learning
from the playlists you already made rather than genre tags or filenames.

Nothing here writes to YouTube. Stage 1 of the plan is a probe that answers a
question worth settling before building a router: **are your playlists actually
separable by audio?**

## Why a probe first

With 25+ playlists and single-label assignment, top-1 accuracy in the 30-50%
range is the expected outcome, not a failure. An automatic sorter doesn't need
top-1 accuracy — it needs high precision on the subset it's confident about,
and everything else left alone in Liked.

The probe measures exactly that: the precision/coverage curve, and a confusion
matrix showing which playlists the audio genuinely cannot tell apart. Those
pairs are separated by *when you made them* or *who showed you the track* —
information no embedding contains. Better to know that now.

## Running it

Auth comes from the service's existing `headers_auth.json`, so authenticate
through the web UI first.

Locally (recommended — this is a one-off analysis, not a scheduled job):

```powershell
# both files: the probe reuses the service's ytmusic_auth / download helpers
.venv\Scripts\pip install -r app\requirements.txt -r app\requirements-vibe.txt
$env:VIBE_APP_ROOT = "app"
$env:YTM_AUTH_DIR  = "app/data/auth"
.venv\Scripts\python app\scripts\vibe_probe.py --sample 30
```

Needs `yt-dlp` and `ffmpeg` on PATH (you already have both).

In the container:

```bash
docker compose -f app/docker-compose.yml exec ytmusic \
  sh -c "pip install -r /app/requirements-vibe.txt && python /app/scripts/vibe_probe.py --sample 30"
```

Useful flags: `--sample N` tracks per playlist, `--backend mfcc|effnet`,
`--workers N` parallel downloads, `--refresh-library` to re-read playlists,
`--skip-fetch` to reuse only cached audio, `--target-precision 0.9`.

Roughly 25 playlists x 30 tracks = 750 snippets, about 40-60 minutes on the
first run. Everything is cached by videoId, so re-runs and backend changes are
fast.

## Output

Written to `data/vibe/reports/`:

- `probe_<backend>.md` — accuracy, precision/coverage table, per-playlist
  thresholds, most-confused pairs
- `confusion_<backend>.csv` — the full matrix

## Backends

`mfcc` (default) is librosa summary statistics: no model download, works
anywhere, hears timbre and rhythm. Its numbers are a **lower bound** — if it
already separates your playlists, a stronger model will do better; if it
doesn't, retest with `effnet` before concluding two playlists are inseparable.

`effnet` is Essentia's discogs-effnet (1280-dim), trained on music similarity.
Needs `pip install essentia-tensorflow` and the model file:

```bash
curl -o app/data/vibe/discogs-effnet-bs64-1.pb \
  https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb
```

## The nightly run

`scripts/scheduler.py` — the existing cron entry, unchanged — now runs three
steps in order:

1. **Retrain** (`vibe_train.py --refresh-library`). Re-reads playlists, so every
   track placed from the review queue since last night becomes a training
   label. This is the whole feedback loop, and it needs no separate store:
   picking a playlist in the UI *is* the label.
2. **Sort** (`vibe_route.py --execute --max-new-audio 50`). Files confident
   tracks, queues the rest. The cap bounds the nightly download; a backlog
   drains over several nights instead of one run fetching thousands.
3. **Download** — unchanged. It collects whatever is in the playlists, so
   anything filed in step 2 arrives the same night.

Order matters: sorting after downloading would leave every newly filed track
waiting a full day. Sorter failures are logged and stepped over — a stale model
or expired auth must never stop the downloader collecting what's already filed.

Env knobs: `VIBE_SORTER=false` disables steps 1-2; `VIBE_MAX_NEW_AUDIO` changes
the nightly fetch cap.

## Modules

| file | role |
|---|---|
| `config.py` | paths and settings, all env-overridable |
| `library.py` | YTM playlists + liked tracks -> `library.json`, membership -> labels |
| `audio.py` | yt-dlp 60s mono 16kHz snippet cache, keyed by videoId |
| `embed.py` | pluggable embedding backends, cached per backend |
| `probe.py` | sample -> fetch -> embed -> cross-validate -> report |

Still to build, only if the probe says it's worth it: `train.py` (fit and
persist per-playlist thresholds), `route.py` (apply, dry-run by default,
append-only decision ledger), and a scheduler entrypoint.

## Design rules for the eventual router

1. **Add-only.** Never remove from a playlist, never unlike. The worst case is
   a track in the wrong place, fixed by hand — never silent data loss.
2. **Per-playlist thresholds, tuned for precision.** Below threshold, a track
   stays in Liked. Low coverage is correct behaviour, not a bug.
3. **Corrections are free training data.** Playlists are re-read every run, so
   a track you move by hand becomes a negative example next cycle.

## Data and secrets

Everything lands in `app/data/vibe/`, covered by the existing `app/data/*`
gitignore rule — including the generated `cookies.txt`, which carries
account-wide session tokens. Do not move this data outside `app/data/`.
