"""Where your playlists blur into each other, and which ones hold two sounds.

The classifier answers "where does this new track go". This answers a
different question for the person curating: which playlists are really the
same genre, which tracks sit on a border, and which playlist is secretly two.

Everything here is geometry on the effnet embeddings, with no training: each
playlist is its centroid on the unit sphere, and a track "belongs" to the
playlist whose centroid it is nearest to. The one subtlety is the track's own
playlist. Its centroid contains the track, so it would always look closer to
home than it is; the own-playlist similarity is therefore computed with the
track left out of its own centroid, the same discipline the out-of-fold
classifier uses.

Nearest-centroid is a cruder judge than the logistic model, which is the point:
it measures how the playlists are laid out in sound, not how the model happens
to carve them up, so a high overlap here is a property of the playlists.

Stays numpy-only: the web app calls this on request and must not drag in the
training stack.
"""

import numpy as np

from .typical import _single_label

# A track whose best other playlist is within this of its own is "on the
# border": a different random seed of the same library could flip it.
BORDER = 0.03
# Playlists below this are too small for a centroid to mean anything.
MIN_TRACKS = 5
# Split analysis needs enough tracks for two clusters to each be a playlist.
MIN_SPLIT_TRACKS = 20


def _unit(M):
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return M / norms


def _groups(lib, vectors, exclude, min_tracks):
    """[(title, [track], unit matrix)] for playlists with enough embedded tracks."""
    out = []
    for title, tracks in sorted(_single_label(lib, exclude).items()):
        rows = [t for t in tracks if t["videoId"] in vectors]
        if len(rows) < min_tracks:
            continue
        M = _unit(np.vstack([np.asarray(vectors[t["videoId"]], dtype=np.float32)
                             for t in rows]))
        out.append((title, rows, M))
    return out


def _leaf_order(D):
    """Leaf order of an average-linkage tree over distance matrix D.

    Putting similar playlists next to each other turns the heatmap's overlap
    from scattered dots into blocks along the diagonal, which is what makes
    "these five are really one family" visible at a glance. k is a few dozen,
    so exact average linkage over member sets is cheap and clearer than an
    incremental update formula.
    """
    clusters = [[i] for i in range(len(D))]
    while len(clusters) > 1:
        best = None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                d = float(D[np.ix_(clusters[x], clusters[y])].mean())
                if best is None or d < best[0]:
                    best = (d, x, y)
        _, x, y = best
        merged = clusters[x] + clusters[y]
        clusters = [c for n, c in enumerate(clusters) if n not in (x, y)]
        clusters.append(merged)
    return clusters[0]


def analyse(lib, vectors, exclude=()):
    """The playlist-level overlap picture.

    Returns None when fewer than two playlists are usable. Otherwise:
      playlists  names in display order (similar playlists adjacent)
      stats      per playlist: size, cohesion, how many tracks sit nearer
                 another playlist ("bleed"), and its nearest neighbour
      matrix     matrix[i][j] = share of playlist i's tracks nearest to j
      centroid_similarity  cosine between playlist centroids
      pairs      the most entangled playlist pairs, ranked, with a verdict
      tracks     every track that is misfiled or on a border, for review
    """
    groups = _groups(lib, vectors, exclude, MIN_TRACKS)
    k = len(groups)
    if k < 2:
        return None

    names = [g[0] for g in groups]
    sums = np.vstack([g[2].sum(axis=0) for g in groups])
    sizes = np.array([len(g[1]) for g in groups], dtype=np.float32)
    centroids = _unit(sums)
    sum_sq = (sums * sums).sum(axis=1)

    counts = np.zeros((k, k), dtype=np.float32)
    flagged = []
    cohesion = np.zeros(k, dtype=np.float32)

    for i, (title, rows, M) in enumerate(groups):
        S = M @ centroids.T                       # n × k, own column biased
        dots = M @ sums[i]                        # x·sum_own, includes x·x = 1
        # ||sum - x||² = ||sum||² - 2·x·sum + 1
        loo_norm = np.sqrt(np.maximum(sum_sq[i] - 2 * dots + 1, 1e-12))
        own = (dots - 1) / loo_norm               # cosine to the centroid without x
        S[:, i] = own
        cohesion[i] = float((M @ centroids[i]).mean())

        best_other_idx = np.argmax(np.where(np.arange(k) == i, -np.inf, S), axis=1)
        best_other = S[np.arange(len(rows)), best_other_idx]
        nearest = np.where(best_other > own, best_other_idx, i)
        for j in nearest:
            counts[i, j] += 1

        margin = own - best_other                 # negative: nearer elsewhere
        for t, row in enumerate(rows):
            if margin[t] > BORDER:
                continue
            flagged.append({
                "videoId": row["videoId"], "artist": row.get("artist"),
                "title": row.get("title"), "current": title,
                "own": round(float(own[t]), 3),
                "other": names[best_other_idx[t]],
                "other_sim": round(float(best_other[t]), 3),
                "margin": round(float(margin[t]), 3),
                "kind": "misfiled" if margin[t] < 0 else "border",
            })

    matrix = counts / sizes[:, None]
    csim = centroids @ centroids.T

    order = _leaf_order(1.0 - csim)
    pairs = []
    for a in range(k):
        for b in range(a + 1, k):
            # Mutual confusion: of both playlists' tracks, how many sit in the
            # other's territory. One-sided is a big playlist swallowing a small
            # one, which is a different story from two that are interchangeable.
            a_to_b, b_to_a = float(matrix[a, b]), float(matrix[b, a])
            mutual = (counts[a, b] + counts[b, a]) / (sizes[a] + sizes[b])
            pairs.append({
                "a": names[a], "b": names[b],
                "a_to_b": round(a_to_b, 3), "b_to_a": round(b_to_a, 3),
                "mutual": round(float(mutual), 3),
                "similarity": round(float(csim[a, b]), 3),
                "verdict": _verdict(a_to_b, b_to_a, float(csim[a, b])),
            })
    pairs.sort(key=lambda p: (-p["mutual"], -p["similarity"]))

    flagged.sort(key=lambda t: t["margin"])
    stats = []
    for i, name in enumerate(names):
        others = csim[i].copy()
        others[i] = -np.inf
        near = int(np.argmax(others))
        stats.append({
            "playlist": name, "tracks": int(sizes[i]),
            "cohesion": round(float(cohesion[i]), 3),
            "bleed": round(float(1 - matrix[i, i]), 3),
            "nearest": names[near],
            "nearest_similarity": round(float(csim[i, near]), 3),
        })

    return {
        "playlists": [names[i] for i in order],
        "stats": {s["playlist"]: s for s in stats},
        "matrix": {names[i]: {names[j]: round(float(matrix[i, j]), 3)
                              for j in range(k) if matrix[i, j] > 0}
                   for i in range(k)},
        "centroid_similarity": {names[i]: {names[j]: round(float(csim[i, j]), 3)
                                           for j in range(k) if j != i}
                                for i in range(k)},
        "pairs": pairs[:40],
        "tracks": flagged,
    }


def _verdict(a_to_b, b_to_a, similarity):
    """A plain-language read of one playlist pair.

    Thresholds are judgement calls, deliberately coarse: the page shows the
    numbers next to the label so the label is never the only evidence.
    """
    low, high = sorted((a_to_b, b_to_a))
    if low >= 0.25 and similarity >= 0.9:
        return "interchangeable"      # both lose a quarter+ of their tracks
    if high >= 0.3 and low < 0.1:
        return "one-sided"            # one playlist sits inside the other
    if low >= 0.1 or similarity >= 0.93:
        return "entangled"
    if high >= 0.1:
        return "touching"
    return "distinct"


def _two_means(M, iterations=12):
    """Deterministic 2-means on unit vectors, seeded along the first PC.

    Returns (labels, centroid_a, centroid_b). Seeding from the sign of the
    first principal component, rather than random picks, keeps the page stable
    between reloads: the same library always splits the same way.
    """
    centred = M - M.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    labels = (centred @ vt[0] > 0).astype(int)
    for _ in range(iterations):
        if labels.min() == labels.max():
            break
        ca = _unit(M[labels == 0].mean(axis=0, keepdims=True))[0]
        cb = _unit(M[labels == 1].mean(axis=0, keepdims=True))[0]
        new = (M @ cb > M @ ca).astype(int)
        if (new == labels).all():
            break
        labels = new
    ca = _unit(M[labels == 0].mean(axis=0, keepdims=True))[0]
    cb = _unit(M[labels == 1].mean(axis=0, keepdims=True))[0]
    return labels, ca, cb


def split_summary(lib, vectors, exclude=()):
    """Per playlist: does it look like two sounds, and how clearly?

    separation is 1 - cosine(centroid A, centroid B): zero is one blob.
    balance is the smaller cluster's share: a 98/2 split is outliers, not a
    second playlist. The score multiplies them so only a split that is both
    far apart and substantial ranks high.
    """
    out = {}
    for title, rows, M in _groups(lib, vectors, exclude, MIN_SPLIT_TRACKS):
        labels, ca, cb = _two_means(M)
        n = len(rows)
        small = int(min((labels == 0).sum(), (labels == 1).sum()))
        if small == 0:
            continue
        separation = float(1 - ca @ cb)
        balance = small / n
        out[title] = {
            "tracks": n,
            "separation": round(separation, 3),
            "balance": round(balance, 3),
            "score": round(separation * min(balance * 2, 1.0), 4),
            "sizes": [int((labels == 0).sum()), int((labels == 1).sum())],
        }
    return out


def split_detail(lib, vectors, title, exclude=(), examples=4):
    """The two clusters of one playlist, with 2-D coordinates for plotting.

    Coordinates are the first two principal components of the playlist's own
    tracks: the directions in which it varies most, so a real split shows up
    as two lobes.
    """
    for name, rows, M in _groups(lib, vectors, exclude, MIN_TRACKS):
        if name != title:
            continue
        labels, ca, cb = _two_means(M) if len(rows) >= 2 else (
            np.zeros(len(rows), dtype=int), None, None)
        centred = M - M.mean(axis=0)
        _, _, vt = np.linalg.svd(centred, full_matrices=False)
        xy = centred @ vt[:2].T
        if xy.shape[1] < 2:
            xy = np.hstack([xy, np.zeros((len(rows), 1))])
        clusters = []
        for c, centre in enumerate((ca, cb)):
            idx = np.where(labels == c)[0]
            if centre is None or len(idx) == 0:
                continue
            sims = M[idx] @ centre
            ranked = idx[np.argsort(-sims)]
            clusters.append({
                "id": c, "size": int(len(idx)),
                "cohesion": round(float(sims.mean()), 3),
                "examples": [{"videoId": rows[i]["videoId"],
                              "artist": rows[i].get("artist"),
                              "title": rows[i].get("title")}
                             for i in ranked[:examples]],
            })
        return {
            "playlist": title,
            "clusters": clusters,
            "points": [{"x": round(float(xy[i, 0]), 4),
                        "y": round(float(xy[i, 1]), 4),
                        "c": int(labels[i]),
                        "videoId": rows[i]["videoId"],
                        "label": f"{rows[i].get('artist') or '?'} — "
                                 f"{rows[i].get('title') or '?'}"}
                       for i in range(len(rows))],
        }
    return None
