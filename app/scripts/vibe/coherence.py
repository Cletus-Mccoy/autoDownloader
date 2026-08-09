"""How tight is each playlist, and what is it sitting on top of?

When a playlist can't be learned, there are two very different reasons, and
they need opposite fixes:

  diffuse     the playlist is a grab-bag — its tracks don't sound like each
              other. No classifier can learn it because there's nothing
              consistent to learn. The fix is splitting it, or accepting that
              it's a bucket rather than a vibe.

  crowded     the playlist is perfectly coherent but sits on top of a
              neighbour, so the model can't tell which of the two a track
              belongs to. The fix is merging them, or accepting the overlap.

Both look identical from the classifier's side — low precision — which is why
this measures the embeddings directly, with no model involved.

  tightness   mean cosine similarity between a playlist's tracks. High means
              the playlist sounds like one thing.
  nearest     the rival playlist its tracks most resemble.
  margin      tightness minus that resemblance. Negative means a playlist's
              tracks look more like the rival's tracks than like each other.
  wrong side  share of its tracks already closer to the rival than to home.

Run via: python app/scripts/vibe_coherence.py
"""

import argparse
import os

import numpy as np

from . import config, library, sweep


def _unit(X):
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.maximum(norms, 1e-12)


def analyse(X, y):
    """Per-playlist tightness, nearest neighbour, and margin.

    Everything is measured track-to-track. An earlier version compared mean
    pairwise similarity within a playlist against centroid-to-centroid
    similarity between playlists, which is not a fair comparison: averaging
    cancels the noise in a centroid, so centroids always look closer to each
    other than individual tracks do. Every margin came out negative as an
    artefact of that, telling you nothing.

    Here both halves are means over track pairs, so the margin means what it
    says — positive is a playlist whose tracks resemble each other more than
    they resemble the nearest other playlist's tracks.
    """
    X = _unit(X)
    labels = np.unique(y)
    sims = X @ X.T
    index = {l: np.flatnonzero(y == l) for l in labels}

    rows = []
    for label in labels:
        own = index[label]
        n = len(own)
        block = sims[np.ix_(own, own)]
        tightness = float((block.sum() - np.trace(block)) / max(n * (n - 1), 1))

        others = []
        for other in labels:
            if other == label:
                continue
            cross = sims[np.ix_(own, index[other])]
            others.append((other, float(cross.mean())))
        nearest, closeness = max(others, key=lambda kv: kv[1])

        # Per-track: does this track look more like its own playlist, or like
        # the nearest rival? The share that fails is the misfiled-looking tail.
        own_mean = (block.sum(axis=1) - 1.0) / max(n - 1, 1)
        rival_mean = sims[np.ix_(own, index[nearest])].mean(axis=1)
        rows.append({
            "playlist": label,
            "n": int(n),
            "tightness": tightness,
            "nearest": nearest,
            "closeness": closeness,
            "margin": tightness - closeness,
            "wrong_side": float((own_mean < rival_mean).mean()),
        })

    rows.sort(key=lambda r: r["margin"])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="effnet")
    parser.add_argument("--exclude", nargs="*", default=["recap", "hotlist"],
                        metavar="SUBSTRING")
    args = parser.parse_args()

    config.ensure_dirs()
    X, y, _stats, _sizes = sweep.load_cached(args.backend, args.exclude)
    rows = analyse(X, y)

    tight = np.median([r["tightness"] for r in rows])
    print(f"\n{len(y)} tracks, {len(rows)} playlists. "
          f"Median tightness {tight:.3f}.\n")
    print(f"{'playlist':<36} {'n':>4} {'tight':>6} {'margin':>7} {'wrong':>6}  nearest")
    print("-" * 100)
    for r in rows:
        flag = " diffuse" if r["tightness"] < tight * 0.85 else ""
        print(f"{r['playlist'][:36]:<36} {r['n']:>4} {r['tightness']:>6.3f} "
              f"{r['margin']:>+7.3f} {r['wrong_side']:>5.0%}  "
              f"{r['nearest'][:26]}{flag}")

    print("\nHow to read this:")
    print("  margin   how much more a playlist's tracks resemble each other")
    print("           than the nearest rival's tracks. Negative means the")
    print("           rival is a better fit than home.")
    print("  wrong    share of tracks already closer to the rival.")
    print("  diffuse  low tightness — a grab-bag. But note that diffuse and")
    print("           unlearnable are different things: a mixed playlist with")
    print("           no near neighbour is still easy to pick out.")

    path = os.path.join(config.REPORT_DIR, "coherence.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Playlist coherence\n\n")
        f.write("| playlist | tracks | tightness | margin | nearest | closeness |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['playlist']} | {r['n']} | {r['tightness']:.3f} "
                    f"| {r['margin']:+.3f} | {r['nearest']} "
                    f"| {r['closeness']:.3f} |\n")
    print(f"\nReport: {path}")


if __name__ == "__main__":
    main()
