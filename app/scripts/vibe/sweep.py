"""Compare model configurations on cached embeddings.

The probe answers "are these playlists separable"; this answers "are we asking
the question well". It re-scores the same cached embeddings under different
preprocessing and classifiers, so it costs seconds and no downloads.

Scored on the objective that actually matters — not accuracy, but how many
playlists clear the precision target and how much of their material routes:

    routable    playlists reaching the target precision
    coverage    share of all tracks routed by those playlists
    top1/top3   reported for context only

Caveat: thresholds are tuned on the same out-of-fold predictions used to
score them, so absolute precision is mildly optimistic. The comparison
between configurations is still fair, since every row shares the bias.

Run via: python app/scripts/vibe_sweep.py
"""

import argparse
import os

import numpy as np

from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import Normalizer, StandardScaler

from . import config, library, probe


def load_cached(backend, exclude, min_tracks=None):
    """Every labelled track that already has an embedding for this backend."""
    lib = library.load_library(refresh=False)
    rows, stats = library.labelled_tracks(lib, min_tracks=min_tracks,
                                          exclude=exclude)

    cache_path = os.path.join(config.EMBED_DIR, f"{backend}.npz")
    if not os.path.exists(cache_path):
        raise SystemExit(f"No embedding cache at {cache_path}. Run the probe "
                         f"with --backend {backend} first.")
    with np.load(cache_path) as data:
        vectors = {k: data[k] for k in data.files}

    kept = [r for r in rows if r["videoId"] in vectors]
    X = np.vstack([vectors[r["videoId"]] for r in kept])
    y = np.array([r["label"] for r in kept])
    sizes = {p["title"]: len(p["tracks"]) for p in lib["playlists"]}
    return X, y, stats, sizes


def configurations():
    """Each entry is (name, factory) so the pipeline is rebuilt per fold."""
    configs = []

    # Baseline: what the probe used.
    configs.append(("standardise + logreg C=1 (probe baseline)",
                    lambda: make_pipeline(
                        StandardScaler(),
                        LogisticRegression(max_iter=4000,
                                           class_weight="balanced"))))

    # L2 normalisation puts every embedding on the unit sphere, which is the
    # geometry these models are trained in — cosine, not euclidean.
    # C sweeps wide on purpose: with 1280 dims on the unit sphere the useful
    # range sits far above sklearn's default of 1.
    for c in (0.1, 1.0, 10.0, 100.0, 300.0, 1000.0, 10000.0):
        configs.append((f"l2norm + logreg C={c}",
                        lambda c=c: make_pipeline(
                            Normalizer(),
                            LogisticRegression(C=c, max_iter=4000,
                                               class_weight="balanced"))))

    for c in (1.0, 10.0):
        configs.append((f"standardise + logreg C={c}",
                        lambda c=c: make_pipeline(
                            StandardScaler(),
                            LogisticRegression(C=c, max_iter=4000,
                                               class_weight="balanced"))))

    # 1280 dims against a few hundred samples per class invites overfitting;
    # PCA trades detail for a better-conditioned problem.
    for n in (64, 128, 256):
        configs.append((f"l2norm + pca{n} + logreg C=10",
                        lambda n=n: make_pipeline(
                            Normalizer(), PCA(n_components=n, whiten=True),
                            LogisticRegression(C=10.0, max_iter=4000,
                                               class_weight="balanced"))))

    for k in (5, 15):
        configs.append((f"l2norm + knn k={k} (cosine)",
                        lambda k=k: make_pipeline(
                            Normalizer(),
                            KNeighborsClassifier(n_neighbors=k, metric="cosine",
                                                 weights="distance"))))

    configs.append(("l2norm + mlp(256)",
                    lambda: make_pipeline(
                        Normalizer(),
                        MLPClassifier(hidden_layer_sizes=(256,), max_iter=1200,
                                      random_state=config.RANDOM_SEED))))

    return configs


def library_coverage(thresholds, sizes):
    """Expected share of the real library that routes automatically.

    The naive metric — accepted / dataset size — can't be compared across
    samples with different class balance: a 30-per-class sample and a 16-to-200
    one weight the same playlist differently. Weighting each playlist's
    measured recall by its true size in the library removes that dependence,
    so numbers from different sampling regimes mean the same thing.
    """
    total = sum(sizes.get(t["playlist"], 0) for t in thresholds)
    if not total:
        return 0.0
    routed = sum(sizes.get(t["playlist"], 0) * t["recall"]
                 for t in thresholds if t["threshold"] is not None)
    return routed / total


def cap_per_class(X, y, cap, seed):
    """Subsample each class to at most `cap` rows, deterministically."""
    if cap is None:
        return X, y
    rng = np.random.default_rng(seed)
    keep = []
    for label in np.unique(y):
        idx = np.flatnonzero(y == label)
        if len(idx) > cap:
            idx = rng.choice(idx, cap, replace=False)
        keep.extend(idx.tolist())
    keep = np.array(sorted(keep))
    return X[keep], y[keep]


def score(model, X, y, folds, target):
    cv = StratifiedKFold(n_splits=folds, shuffle=True,
                         random_state=config.RANDOM_SEED)
    proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")
    classes = np.unique(y)
    predicted = classes[np.argmax(proba, axis=1)]

    thresholds = probe.per_class_thresholds(proba, classes, y, target)
    routable = [t for t in thresholds if t["threshold"] is not None]

    return {
        "top1": float((predicted == y).mean()),
        "top3": probe.top_k_accuracy(proba, classes, y, min(3, len(classes))),
        "routable": len(routable),
        "n_classes": len(classes),
        "coverage": sum(t["accepted"] for t in routable) / len(y),
        "thresholds": thresholds,
        "playlists": sorted(t["playlist"] for t in routable),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="effnet")
    parser.add_argument("--exclude", nargs="*", default=["recap", "hotlist"],
                        metavar="SUBSTRING")
    parser.add_argument("--target-precision", type=float, default=0.90)
    parser.add_argument("--min-tracks", type=int, default=None)
    parser.add_argument("--caps", nargs="*", type=int, default=None,
                        metavar="N",
                        help="instead of the full config sweep, test whether "
                             "more data per playlist helps: cap each class at "
                             "each N and report the same configs. 0 means no cap")
    args = parser.parse_args()

    X, y, stats, sizes = load_cached(args.backend, args.exclude, args.min_tracks)
    counts = np.bincount(np.unique(y, return_inverse=True)[1])
    X, y, _, thin = probe.drop_thin_classes(X, y, list(range(len(y))))
    folds = max(2, min(5, int(np.bincount(
        np.unique(y, return_inverse=True)[1]).min())))

    print(f"{len(y)} tracks, {len(np.unique(y))} playlists, {folds}-fold CV, "
          f"target precision {args.target_precision:.0%}")
    if thin:
        print(f"dropped thin: {', '.join(f'{l} ({c})' for l, c in thin)}")
    print(f"smallest class {counts.min()}, largest {counts.max()}\n")

    if args.caps:
        print("Does more data per playlist help? libcov weights each "
              "playlist's recall by its real size, so caps are comparable.\n")
        print(f"{'cap':>5} {'n':>6} {'config':<26} {'routable':>9} "
              f"{'libcov':>7} {'top1':>6} {'top3':>6}")
        for cap in args.caps:
            cap = None if cap == 0 else cap
            Xc, yc = cap_per_class(X, y, cap, config.RANDOM_SEED)
            f = max(2, min(5, int(np.bincount(
                np.unique(yc, return_inverse=True)[1]).min())))
            for name, factory in configurations():
                if "logreg C=10.0" not in name and "logreg C=100.0" not in name:
                    continue
                row = score(factory(), Xc, yc, f, args.target_precision)
                print(f"{str(cap or 'all'):>5} {len(yc):>6} {name:<26} "
                      f"{row['routable']:>4}/{row['n_classes']:<4} "
                      f"{library_coverage(row['thresholds'], sizes):>6.1%} "
                      f"{row['top1']:>5.1%} {row['top3']:>5.1%}")
        return

    results = []
    for name, factory in configurations():
        try:
            row = score(factory(), X, y, folds, args.target_precision)
        except Exception as e:
            print(f"  {name}: failed ({type(e).__name__}: {e})")
            continue
        results.append((name, row))
        print(f"  {row['routable']:2d}/{row['n_classes']} routable  "
              f"libcov {library_coverage(row['thresholds'], sizes):5.1%}  "
              f"top1 {row['top1']:5.1%}  top3 {row['top3']:5.1%}   {name}")

    print("\n" + "=" * 74)
    print("RANKED by routable playlists, then coverage")
    print("=" * 74)
    results.sort(key=lambda r: (-r[1]["routable"], -r[1]["coverage"]))
    for name, row in results[:6]:
        print(f"{row['routable']:2d}/{row['n_classes']}  cov {row['coverage']:5.1%}  "
              f"top1 {row['top1']:5.1%}  {name}")

    best_name, best = results[0]
    baseline = next(r for n, r in results if "probe baseline" in n)
    print(f"\nBest: {best_name}")
    print(f"  routable {baseline['routable']} -> {best['routable']}, "
          f"coverage {baseline['coverage']:.1%} -> {best['coverage']:.1%}, "
          f"top1 {baseline['top1']:.1%} -> {best['top1']:.1%}")
    gained = set(best["playlists"]) - set(baseline["playlists"])
    if gained:
        print(f"  newly routable: {', '.join(sorted(gained))}")


if __name__ == "__main__":
    main()
