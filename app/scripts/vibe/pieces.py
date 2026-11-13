"""The 9 loudest 10-second pieces of the whole track, one effnet vector each.

The original backend hears one minute from the middle of a track. Measured on
the labelled library (2,203 tracks, 22 playlists, the production gate), hearing
the whole track as pieces and scoring each piece did better:

    middle minute, one vector per track        top-1 57.1  auto-placed 18.7%
    9 loudest pieces, combined per track       top-1 60.7  auto-placed 21.8%
    ... plus the artist vote (model.PieceModel) top-1 63.2  auto-placed 23.2%

at the same ~89% precision. "Loudest" because a breakdown or a quiet intro of
a hard track should not get a vote: the 9 are picked from 18 evenly spaced
candidates by RMS, and beat 9 evenly spaced pieces in every combination tried.

Audio comes from the downloads first (the full MP3s the downloader already
keeps, matched by the watch URL in their tags), so the library costs no
YouTube requests. Only tracks that are not downloaded yet, mostly new likes,
are fetched whole, embedded, and deleted again.

Vectors are cached in embeddings/effnet-loud9.npz as a (9, 1280) array per
videoId; shorter tracks have fewer rows.
"""

import os
import subprocess

import numpy as np

from . import config

BACKEND = "effnet-loud9"
PIECES = 9
PIECE_SECONDS = 10
TRIM = 0.03              # lead-in and fade-out, never a piece
CHECKPOINT_EVERY = 50

# The effnet graph runs in batches of 64 patches of 128 mel frames, hopping 62.
# Calling it once per piece pads each piece to a full batch (6x slower), so the
# pieces are joined and each piece keeps the patches that lie wholly inside it.
_HOP, _PATCH, _PATCH_HOP = 256, 128, 62

FULL_AUDIO_DIR = os.path.join(config.DATA_DIR, "full_audio")


def cache_path():
    return os.path.join(config.EMBED_DIR, f"{BACKEND}.npz")


def cached_ids():
    if not os.path.exists(cache_path()):
        return set()
    with np.load(cache_path()) as data:
        return set(data.files)


def load_cache():
    if not os.path.exists(cache_path()):
        return {}
    with np.load(cache_path()) as data:
        return {k: data[k] for k in data.files}


def select(audio, sr=config.SAMPLE_RATE, pieces=PIECES, seconds=PIECE_SECONDS):
    """The loudest `pieces` windows of `seconds`, in time order.

    Candidates are 2 x pieces windows spread evenly over the trimmed track;
    the loudest by RMS are kept. A track shorter than one window is one piece.
    """
    audio = np.asarray(audio, dtype=np.float32)
    trim = int(len(audio) * TRIM)
    audio = audio[trim:len(audio) - trim] if trim else audio
    n = sr * seconds
    if len(audio) <= n:
        return [audio]
    starts = np.linspace(0, len(audio) - n, pieces * 2).astype(int)
    rms = np.array([np.sqrt(np.mean(audio[s:s + n] ** 2)) for s in starts])
    keep = np.sort(starts[np.argsort(-rms, kind="stable")[:pieces]])
    return [audio[s:s + n] for s in keep]


def _effnet_pieces(audio_pieces):
    from .embed import _load_effnet
    model = _load_effnet()
    if len({len(p) for p in audio_pieces}) != 1:
        return np.stack([np.asarray(model(p)).mean(0) for p in audio_pieces])
    frames = np.asarray(model(np.concatenate(audio_pieces)))
    per = len(audio_pieces[0]) // _HOP
    starts = np.arange(len(frames)) * _PATCH_HOP
    out = []
    for j in range(len(audio_pieces)):
        inside = (starts >= j * per) & (starts + _PATCH <= (j + 1) * per)
        out.append(frames[inside].mean(0) if inside.any() else frames.mean(0))
    return np.stack(out)


def decode(path, sr=config.SAMPLE_RATE):
    """Any container ffmpeg reads -> mono float32 at sr. essentia's own loader
    in the image refuses some codecs (opus in webm), and this is the decode the
    measurements were made with."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                          "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def embed_file(path):
    """(pieces, 1280) float32 for one audio file, or None if too short."""
    audio = decode(path)
    if audio.size < config.SAMPLE_RATE * 2:
        return None
    vectors = _effnet_pieces(select(audio))
    return vectors.astype(np.float32) if np.isfinite(vectors).all() else None


def downloaded_files(base=None):
    """{videoId: path} for every downloaded track, from the tags yt-dlp wrote."""
    import housekeeping
    base = base or housekeeping.BASE_DIR
    if not os.path.isdir(base):
        return {}
    found = {}
    for folder, disk in housekeeping.index_folders(base).items():
        for vid, names in disk["files"].items():
            found.setdefault(vid, os.path.join(base, folder, names[0]))
    return found


def fetch_full(video_id, cookies_path=None):
    """Download one whole track as m4a for embedding. Returns the path or None."""
    from .audio import yt_dlp_binary
    os.makedirs(FULL_AUDIO_DIR, exist_ok=True)
    out = os.path.join(FULL_AUDIO_DIR, f"{video_id}.m4a")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    cmd = [yt_dlp_binary(), "-f", "bestaudio/best", "--no-playlist",
           "-x", "--audio-format", "m4a",
           "--output", os.path.join(FULL_AUDIO_DIR, "%(id)s.%(ext)s"),
           "--extractor-args", "youtube:player_client=web,web_music,android",
           "--no-warnings", "--sleep-interval", "1", "--max-sleep-interval", "3",
           f"https://music.youtube.com/watch?v={video_id}"]
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


def ensure(tracks, max_new=None, fetch=True, sources=None, embed_fn=None, fetch_fn=None):
    """Embed the tracks that have no piece vectors yet. Returns {videoId: (n, 1280)}
    for every track that has them afterwards.

    Downloads are used first; with fetch, the rest are downloaded whole, embedded
    and deleted. max_new caps the tracks embedded this run, so a large backlog
    (the whole library, the first time) drains over several nights.
    """
    embed_fn = embed_fn or embed_file
    cache = load_cache()
    wanted = [t for t in tracks if t["videoId"] not in cache]
    if wanted:
        sources = downloaded_files() if sources is None else sources
        # Downloaded tracks first: they cost no YouTube request.
        wanted.sort(key=lambda t: t["videoId"] not in sources)
        deferred = 0
        if max_new is not None and len(wanted) > max_new:
            deferred, wanted = len(wanted) - max_new, wanted[:max_new]
        local = sum(1 for t in wanted if t["videoId"] in sources)
        print(f"Pieces ({BACKEND}): {len(tracks) - len(wanted) - deferred} cached, "
              f"{len(wanted)} to embed ({local} from downloads)"
              + (f", {deferred} deferred to a later run" if deferred else ""))

        cookies = None
        if fetch and any(t["videoId"] not in sources for t in wanted):
            if fetch_fn is None:
                from download import get_cookie_header, write_cookies_file
                cookie = get_cookie_header()
                cookies = write_cookies_file(cookie) if cookie else None
        fetch_fn = fetch_fn or fetch_full

        def save():
            config.ensure_dirs()
            tmp = cache_path() + ".tmp.npz"
            np.savez_compressed(tmp, **cache)
            os.replace(tmp, cache_path())

        failed = 0
        for i, t in enumerate(wanted, 1):
            vid = t["videoId"]
            path, temporary = sources.get(vid), False
            if path is None and fetch:
                path, temporary = fetch_fn(vid, cookies), True
            if path is None:
                failed += 1
                continue
            try:
                vectors = embed_fn(path)
            except Exception as e:
                print(f"  ✗ {vid}: {e}")
                vectors = None
            finally:
                if temporary:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if vectors is None:
                failed += 1
            else:
                cache[vid] = vectors
            if i % CHECKPOINT_EVERY == 0:
                save()
                print(f"  [{i}/{len(wanted)}] embedded")
        save()
        if failed:
            print(f"  {failed} track(s) could not be embedded")
    return {t["videoId"]: cache[t["videoId"]] for t in tracks if t["videoId"] in cache}
