"""Turn cached audio snippets into fixed-length vectors.

Two backends, selectable per run:

  mfcc    librosa summary statistics (~130-dim). No model download, no heavy
          deps beyond librosa. It hears timbre, brightness and rhythm — which
          is a real slice of "vibe" but not all of it. Treat its numbers as a
          LOWER BOUND on how separable your playlists are: if mfcc already
          separates them, a stronger embedder will do better, but a poor mfcc
          score does not prove two playlists are inseparable.

  effnet  Essentia's discogs-effnet embeddings (1280-dim), trained on music
          similarity, so it hears genre/production/instrumentation the way a
          listener groups records. Needs essentia-tensorflow plus the model
          file — see README.

Vectors are cached per backend in embeddings/{backend}.npz keyed by videoId,
so switching backends or retraining costs nothing after the first pass.
"""

import os

import numpy as np

from . import config

_effnet_model = None


def _stats(matrix):
    """Mean and std across time for a (features, frames) matrix."""
    return np.concatenate([matrix.mean(axis=1), matrix.std(axis=1)])


def _mfcc_embed(path):
    import librosa

    y, sr = librosa.load(path, sr=config.SAMPLE_RATE, mono=True)
    if y.size < sr:  # under a second of audio is not worth scoring
        return None

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
    parts = [
        _stats(mfcc),
        _stats(librosa.feature.delta(mfcc)),
        _stats(librosa.feature.chroma_cqt(y=y, sr=sr)),
        _stats(librosa.feature.spectral_contrast(y=y, sr=sr)),
        _stats(librosa.feature.spectral_centroid(y=y, sr=sr)),
        _stats(librosa.feature.spectral_bandwidth(y=y, sr=sr)),
        _stats(librosa.feature.spectral_rolloff(y=y, sr=sr)),
        _stats(librosa.feature.spectral_flatness(y=y)),
        _stats(librosa.feature.zero_crossing_rate(y=y)),
        _stats(librosa.feature.rms(y=y)),
    ]

    # Moved from librosa.beat.tempo in 0.10. Resolve the attribute rather than
    # wrapping the call, so a genuine error inside it isn't swallowed.
    tempo_fn = getattr(librosa.feature, "tempo", None) or librosa.beat.tempo
    parts.append(np.atleast_1d(tempo_fn(y=y, sr=sr)).astype(float)[:1])

    return np.concatenate(parts).astype(np.float32)


def _load_effnet():
    global _effnet_model
    if _effnet_model is not None:
        return _effnet_model

    model_file = os.getenv(
        "VIBE_EFFNET_MODEL",
        os.path.join(config.DATA_DIR, "discogs-effnet-bs64-1.pb"),
    )
    if not os.path.exists(model_file):
        raise RuntimeError(
            f"discogs-effnet model not found at {model_file}. Download it from "
            "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/"
            "discogs-effnet-bs64-1.pb or set VIBE_EFFNET_MODEL."
        )
    try:
        from essentia.standard import TensorflowPredictEffnetDiscogs
    except ImportError as e:
        raise RuntimeError(
            "essentia-tensorflow is required for the effnet backend "
            "(pip install essentia-tensorflow)"
        ) from e

    _effnet_model = TensorflowPredictEffnetDiscogs(
        graphFilename=model_file, output="PartitionedCall:1"
    )
    return _effnet_model


def _effnet_frames(path):
    from essentia.standard import MonoLoader

    model = _load_effnet()
    audio = MonoLoader(filename=path, sampleRate=config.SAMPLE_RATE,
                       resampleQuality=4)()
    if audio.size < config.SAMPLE_RATE:
        return None
    return np.asarray(model(audio))  # (frames, 1280)


def _effnet_embed(path):
    """Mean over frames — one 1280-dim vector per track."""
    frames = _effnet_frames(path)
    return None if frames is None else frames.mean(axis=0).astype(np.float32)


def _effnet_meanstd_embed(path):
    """Mean and standard deviation over frames — 2560-dim.

    Mean alone describes the average moment of a track. The std says how much
    it moves: a droning techno loop and a track with a breakdown can share a
    mean and differ entirely in how static they are, which is exactly the kind
    of distinction the confused playlist pairs turn on.
    """
    frames = _effnet_frames(path)
    if frames is None:
        return None
    return np.concatenate([frames.mean(axis=0),
                           frames.std(axis=0)]).astype(np.float32)


BACKENDS = {
    "mfcc": _mfcc_embed,
    "effnet": _effnet_embed,
    "effnet-meanstd": _effnet_meanstd_embed,
}


def embed_tracks(paths_by_id, backend="mfcc"):
    """Embed every cached snippet. Returns {videoId: vector}."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; "
                         f"choose from {sorted(BACKENDS)}")
    embed_fn = BACKENDS[backend]

    config.ensure_dirs()
    cache_path = os.path.join(config.EMBED_DIR, f"{backend}.npz")
    cache = {}
    if os.path.exists(cache_path):
        with np.load(cache_path) as data:
            cache = {k: data[k] for k in data.files}

    missing = [vid for vid in paths_by_id if vid not in cache]
    print(f"Embeddings ({backend}): {len(paths_by_id) - len(missing)} cached, "
          f"{len(missing)} to compute")

    failed = 0
    for i, video_id in enumerate(missing, 1):
        try:
            vector = embed_fn(paths_by_id[video_id])
        except Exception as e:
            print(f"  [{i}/{len(missing)}] ✗ {video_id}: {e}")
            failed += 1
            continue
        if vector is None:
            failed += 1
            continue
        cache[video_id] = vector
        if i % 25 == 0 or i == len(missing):
            print(f"  [{i}/{len(missing)}] embedded")

    if missing:
        tmp = cache_path + ".tmp.npz"
        np.savez_compressed(tmp, **cache)
        os.replace(tmp, cache_path)

    result = {vid: cache[vid] for vid in paths_by_id if vid in cache}
    if failed:
        print(f"  {failed} snippet(s) could not be embedded")
    dim = len(next(iter(result.values()))) if result else 0
    print(f"Embeddings ready for {len(result)} tracks ({dim}-dim)")
    return result
