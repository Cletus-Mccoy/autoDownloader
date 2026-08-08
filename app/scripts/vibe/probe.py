"""Separability probe: are your playlists actually distinct vibes?

Samples N tracks per playlist, fetches snippets, embeds them, and cross-
validates a classifier on playlist membership. It writes nothing to YouTube.

The output that matters is not accuracy. With 25+ playlists, top-1 accuracy in
the 30-50% range is expected and fine. What matters is:

  * the precision/coverage curve — how much of your Liked pile can be routed
    at ~90% precision, which is the whole basis for an automatic sorter;
  * the confusion matrix — which playlists the model cannot tell apart. Those
    pairs are distinct memories rather than distinct sounds, and no amount of
    modelling will separate them.

Run via: python app/scripts/vibe_probe.py --sample 30
"""

import argparse
import csv
import os
import random

import numpy as np

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.neighbors import NearestCentroid
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import audio, config, embed, library

PROB_GRID = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
MARGIN_GRID = [0.00, 0.10, 0.20]
MIN_CLASS_FOR_CV = 5


def sample_per_playlist(rows, per_playlist):
    """Take up to `per_playlist` tracks from each label, deterministically."""
    by_label = {}
    for row in rows:
        by_label.setdefault(row["label"], []).append(row)

    rng = random.Random(config.RANDOM_SEED)
    sampled = []
    for label in sorted(by_label):
        tracks = sorted(by_label[label], key=lambda r: r["videoId"])
        if len(tracks) > per_playlist:
            tracks = rng.sample(tracks, per_playlist)
        sampled.extend(tracks)
    return sampled


def build_matrix(rows, vectors):
    """Rows that survived audio+embedding, as (X, y, kept_rows)."""
    kept = [r for r in rows if r["videoId"] in vectors]
    if not kept:
        raise RuntimeError("no tracks survived audio fetch + embedding")
    X = np.vstack([vectors[r["videoId"]] for r in kept])
    y = np.array([r["label"] for r in kept])
    return X, y, kept


def drop_thin_classes(X, y, kept, minimum=MIN_CLASS_FOR_CV):
    labels, counts = np.unique(y, return_counts=True)
    thin = [(l, int(c)) for l, c in zip(labels, counts) if c < minimum]
    if thin:
        keep_mask = np.isin(y, [l for l, c in zip(labels, counts) if c >= minimum])
        X, y = X[keep_mask], y[keep_mask]
        kept = [r for r, m in zip(kept, keep_mask) if m]
    return X, y, kept, thin


def oof_probabilities(X, y, folds):
    """Out-of-fold class probabilities — every prediction is on unseen data."""
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=4000, class_weight="balanced"),
    )
    cv = StratifiedKFold(n_splits=folds, shuffle=True,
                         random_state=config.RANDOM_SEED)
    proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")
    classes = np.unique(y)
    return proba, classes


def centroid_baseline(X, y, folds):
    """Nearest-class-centroid accuracy — the floor a real model must beat."""
    cv = StratifiedKFold(n_splits=folds, shuffle=True,
                         random_state=config.RANDOM_SEED)
    model = make_pipeline(StandardScaler(), NearestCentroid())
    predicted = cross_val_predict(model, X, y, cv=cv)
    return float((predicted == y).mean())


def top_k_accuracy(proba, classes, y, k):
    order = np.argsort(-proba, axis=1)[:, :k]
    hits = [y[i] in classes[order[i]] for i in range(len(y))]
    return float(np.mean(hits))


def coverage_table(proba, classes, y):
    """Precision achievable at each (probability, margin) operating point."""
    ordered = np.sort(proba, axis=1)
    top_p = ordered[:, -1]
    margin = top_p - ordered[:, -2] if proba.shape[1] > 1 else top_p
    predicted = classes[np.argmax(proba, axis=1)]
    correct = predicted == y

    table = []
    for p_min in PROB_GRID:
        for m_min in MARGIN_GRID:
            accept = (top_p >= p_min) & (margin >= m_min)
            n = int(accept.sum())
            table.append({
                "p_min": p_min,
                "margin_min": m_min,
                "coverage": n / len(y),
                "accepted": n,
                "precision": float(correct[accept].mean()) if n else float("nan"),
            })
    return table


def per_class_thresholds(proba, classes, y, target_precision, min_accepted=5):
    """Lowest per-playlist cutoff that still hits the precision target.

    Reported per playlist because playlists differ wildly: a distinctive one
    may route at 0.45, an ambiguous one may never reach the target at all —
    and those simply never get routed automatically.
    """
    predicted = classes[np.argmax(proba, axis=1)]
    top_p = proba.max(axis=1)

    results = []
    for i, label in enumerate(classes):
        support = int((y == label).sum())
        picked = predicted == label
        chosen = None
        for t in np.arange(0.30, 0.96, 0.05):
            accept = picked & (top_p >= t)
            n = int(accept.sum())
            if n < min_accepted:
                continue
            precision = float((y[accept] == label).mean())
            if precision >= target_precision:
                chosen = {"threshold": round(float(t), 2), "precision": precision,
                          "accepted": n, "recall": n / support if support else 0.0}
                break
        results.append({
            "playlist": label,
            "support": support,
            **(chosen or {"threshold": None, "precision": float("nan"),
                          "accepted": 0, "recall": 0.0}),
        })
    return results


def confused_pairs(matrix, classes, top_n=15):
    normalised = matrix / np.maximum(matrix.sum(axis=1, keepdims=True), 1)
    pairs = []
    for i in range(len(classes)):
        for j in range(len(classes)):
            if i != j and normalised[i, j] > 0:
                pairs.append((classes[i], classes[j], float(normalised[i, j]),
                              int(matrix[i, j])))
    pairs.sort(key=lambda p: -p[2])
    return pairs[:top_n]


def write_confusion_csv(matrix, classes, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["actual\\predicted", *classes])
        for label, row in zip(classes, matrix):
            writer.writerow([label, *row.tolist()])


def write_report(path, ctx):
    lines = []
    add = lines.append

    add("# Playlist separability probe\n")
    add(f"- Backend: `{ctx['backend']}`")
    add(f"- Tracks scored: **{ctx['n_tracks']}** across "
        f"**{ctx['n_classes']}** playlists ({ctx['folds']}-fold CV)")
    add(f"- Sampled up to {ctx['per_playlist']} tracks per playlist")
    add(f"- Top-1 accuracy: **{ctx['top1']:.1%}** "
        f"(random guess {1 / ctx['n_classes']:.1%}, "
        f"centroid baseline {ctx['centroid']:.1%})")
    add(f"- Top-3 accuracy: **{ctx['top3']:.1%}**\n")

    if ctx["backend"] == "mfcc":
        add("> These are handcrafted timbre/rhythm features — a lower bound. "
            "Poor separation here is worth re-testing with the `effnet` "
            "backend before concluding two playlists are inseparable.\n")

    add("## Routing: precision vs coverage\n")
    add("The operating point for automatic sorting. Coverage is the share of "
        "tracks confident enough to place; precision is how many of those "
        "land in the right playlist. Everything below the cutoff stays in "
        "Liked, untouched.\n")
    add("| min prob | min margin | coverage | accepted | precision |")
    add("|---|---|---|---|---|")
    for row in ctx["coverage"]:
        precision = "—" if np.isnan(row["precision"]) else f"{row['precision']:.1%}"
        add(f"| {row['p_min']:.2f} | {row['margin_min']:.2f} | "
            f"{row['coverage']:.1%} | {row['accepted']} | {precision} |")
    add("")

    best = ctx["best_point"]
    if best:
        add(f"**Best point at >= {ctx['target']:.0%} precision:** "
            f"prob >= {best['p_min']:.2f}, margin >= {best['margin_min']:.2f} "
            f"routes **{best['coverage']:.1%}** of tracks at "
            f"**{best['precision']:.1%}** precision.\n")
    else:
        add(f"**No global operating point reaches {ctx['target']:.0%} "
            "precision.** Per-playlist thresholds below are the fallback; if "
            "those are empty too, the embeddings aren't carrying your sense "
            "of vibe and the backend needs upgrading before this is usable.\n")

    add("## Per-playlist thresholds\n")
    add("Playlists with no threshold never reach the precision target — they "
        "would stay manual.\n")
    add("| playlist | tracks | threshold | precision | auto-routed |")
    add("|---|---|---|---|---|")
    for row in sorted(ctx["thresholds"], key=lambda r: -r["recall"]):
        if row["threshold"] is None:
            add(f"| {row['playlist']} | {row['support']} | — | — | 0% |")
        else:
            add(f"| {row['playlist']} | {row['support']} | {row['threshold']:.2f} "
                f"| {row['precision']:.0%} | {row['recall']:.0%} |")
    add("")

    add("## Most confused playlist pairs\n")
    add("Read this as a map of your library: high numbers mean the two "
        "playlists sound alike and are separated by something the audio "
        "doesn't contain.\n")
    add("| actual | predicted as | share | n |")
    add("|---|---|---|---|")
    for actual, predicted, share, n in ctx["pairs"]:
        add(f"| {actual} | {predicted} | {share:.0%} | {n} |")
    add("")

    add("## Per-playlist classification report\n")
    add("```")
    add(ctx["report"])
    add("```\n")

    add("## Data notes\n")
    add(f"- Playlists too small (< {ctx['min_tracks']} tracks), excluded: "
        f"{', '.join(ctx['too_small']) or 'none'}")
    add(f"- Tracks in more than one playlist, dropped as ambiguous: "
        f"{ctx['n_ambiguous']}")
    if ctx["thin"]:
        add("- Playlists dropped after audio failures (too few usable tracks): "
            + ", ".join(f"{l} ({c})" for l, c in ctx["thin"]))
    add(f"- Snippets unavailable or unembeddable: {ctx['n_lost']}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=30,
                        help="tracks sampled per playlist (default 30)")
    parser.add_argument("--backend", default="mfcc", choices=sorted(embed.BACKENDS),
                        help="embedding backend (default mfcc)")
    parser.add_argument("--workers", type=int, default=3,
                        help="parallel yt-dlp downloads (default 3)")
    parser.add_argument("--refresh-library", action="store_true",
                        help="re-fetch playlists from YouTube Music")
    parser.add_argument("--min-tracks", type=int, default=None,
                        help="minimum playlist size to include")
    parser.add_argument("--target-precision", type=float, default=0.90,
                        help="precision the routing thresholds aim for")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="use only snippets already cached")
    args = parser.parse_args()

    config.ensure_dirs()

    try:
        lib = library.load_library(refresh=args.refresh_library)
    except library.AuthError as e:
        raise SystemExit(f"\n{e}")
    rows, stats = library.labelled_tracks(lib, min_tracks=args.min_tracks)
    min_tracks = args.min_tracks or config.MIN_TRACKS_PER_PLAYLIST

    print(f"\n{len(stats['playlists_kept'])} playlists with >= {min_tracks} "
          f"tracks, {stats['total_labelled']} labelled tracks, "
          f"{len(stats['ambiguous_tracks'])} dropped as ambiguous "
          f"(in multiple playlists)")
    if stats["playlists_too_small"]:
        print(f"Too small to learn: {', '.join(stats['playlists_too_small'])}")
    if not stats["playlists_kept"]:
        raise SystemExit("No playlists large enough to probe.")

    sampled = sample_per_playlist(rows, args.sample)
    print(f"Sampled {len(sampled)} tracks\n")

    if args.skip_fetch:
        paths = {t["videoId"]: audio.snippet_path(t["videoId"])
                 for t in sampled if audio.is_cached(t["videoId"])}
        print(f"Using {len(paths)} cached snippets (--skip-fetch)")
    else:
        paths = audio.fetch_many(sampled, workers=args.workers)

    vectors = embed.embed_tracks(paths, backend=args.backend)

    X, y, kept = build_matrix(sampled, vectors)
    X, y, kept, thin = drop_thin_classes(X, y, kept)
    if thin:
        print(f"Dropped {len(thin)} playlist(s) with too few usable tracks: "
              + ", ".join(f"{l} ({c})" for l, c in thin))

    classes_present = np.unique(y)
    if len(classes_present) < 2:
        raise SystemExit("Need at least 2 playlists with usable audio.")

    min_count = int(np.bincount(np.unique(y, return_inverse=True)[1]).min())
    folds = max(2, min(5, min_count))
    print(f"\nCross-validating {len(y)} tracks over {len(classes_present)} "
          f"playlists ({folds}-fold)...")

    proba, classes = oof_probabilities(X, y, folds)
    centroid = centroid_baseline(X, y, folds)
    predicted = classes[np.argmax(proba, axis=1)]

    top1 = float((predicted == y).mean())
    top3 = top_k_accuracy(proba, classes, y, min(3, len(classes)))
    coverage = coverage_table(proba, classes, y)
    thresholds = per_class_thresholds(proba, classes, y, args.target_precision)
    matrix = confusion_matrix(y, predicted, labels=classes)

    qualifying = [r for r in coverage
                  if not np.isnan(r["precision"])
                  and r["precision"] >= args.target_precision]
    best_point = max(qualifying, key=lambda r: r["coverage"]) if qualifying else None

    confusion_path = os.path.join(config.REPORT_DIR,
                                  f"confusion_{args.backend}.csv")
    report_path = os.path.join(config.REPORT_DIR,
                               f"probe_{args.backend}.md")
    write_confusion_csv(matrix, classes, confusion_path)
    write_report(report_path, {
        "backend": args.backend,
        "n_tracks": len(y),
        "n_classes": len(classes),
        "folds": folds,
        "per_playlist": args.sample,
        "top1": top1,
        "top3": top3,
        "centroid": centroid,
        "target": args.target_precision,
        "coverage": coverage,
        "best_point": best_point,
        "thresholds": thresholds,
        "pairs": confused_pairs(matrix, classes),
        "report": classification_report(y, predicted, labels=classes,
                                        zero_division=0),
        "min_tracks": min_tracks,
        "too_small": stats["playlists_too_small"],
        "n_ambiguous": len(stats["ambiguous_tracks"]),
        "thin": thin,
        "n_lost": len(sampled) - len(kept),
    })

    routable = [r for r in thresholds if r["threshold"] is not None]
    print("\n" + "=" * 60)
    print("PROBE RESULT")
    print("=" * 60)
    print(f"Top-1 {top1:.1%} | top-3 {top3:.1%} | "
          f"random {1 / len(classes):.1%} | centroid {centroid:.1%}")
    if best_point:
        print(f"At >= {args.target_precision:.0%} precision: routes "
              f"{best_point['coverage']:.1%} of tracks "
              f"(prob >= {best_point['p_min']:.2f}, "
              f"margin >= {best_point['margin_min']:.2f})")
    else:
        print(f"No global operating point reaches "
              f"{args.target_precision:.0%} precision")
    print(f"{len(routable)}/{len(classes)} playlists have a usable threshold")
    print(f"\nReport:    {report_path}")
    print(f"Confusion: {confusion_path}")


if __name__ == "__main__":
    main()
