"""Which tracks sound most like each playlist, and which least.

An effnet embedding is a one-way fingerprint, so there is no synthesising a
playlist's "average sound". The useful equivalent is the medoid: normalise
every vector, take the playlist's centroid, and rank its tracks by cosine
similarity to it. The top few are what the playlist sounds like when it is
most itself; the bottom few are the tracks it shares least with — the same
outliers the remodel step proposes moving, seen from the playlist's side.

The snippets of those tracks already exist in the audio cache, so the stats
page can play them.

Stays numpy-only on purpose: the web app calls this on request and must not
drag in the training stack.
"""

import numpy as np


def _single_label(lib, exclude=()):
    """{playlist: [track]} for tracks that sit in exactly one playlist."""
    patterns = [p.lower() for p in exclude]
    membership = {}
    for pl in lib.get("playlists", []):
        title = pl.get("title") or ""
        if any(p in title.lower() for p in patterns):
            continue
        for track in pl.get("tracks", []):
            membership.setdefault(track["videoId"], []).append((title, track))
    grouped = {}
    for entries in membership.values():
        titles = {t for t, _ in entries}
        if len(titles) != 1:
            continue
        title, track = entries[0]
        grouped.setdefault(title, []).append(track)
    return grouped


def typical_tracks(lib, vectors, exclude=(), n=3, min_tracks=2):
    """Per playlist: cohesion, the n most typical and n least typical tracks.

    vectors: {videoId: 1-d array}. Playlists with fewer than min_tracks
    embedded tracks are skipped — a centroid of one track is that track.
    """
    out = {}
    for title, tracks in _single_label(lib, exclude).items():
        rows = [t for t in tracks if t["videoId"] in vectors]
        if len(rows) < min_tracks:
            continue
        M = np.vstack([np.asarray(vectors[t["videoId"]], dtype=np.float32)
                       for t in rows])
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        M = M / norms
        centroid = M.mean(axis=0)
        centroid /= (np.linalg.norm(centroid) or 1.0)
        sims = M @ centroid
        order = np.argsort(-sims)

        def entry(i):
            t = rows[i]
            return {"videoId": t["videoId"], "artist": t.get("artist"),
                    "title": t.get("title"),
                    "similarity": round(float(sims[i]), 3)}

        k = min(n, len(rows))
        out[title] = {
            "count": len(rows),
            "cohesion": round(float(sims.mean()), 3),
            "typical": [entry(i) for i in order[:k]],
            "atypical": [entry(i) for i in order[::-1][:k]],
        }
    return out
