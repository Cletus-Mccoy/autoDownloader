"""Fit the classifier and pick a confidence threshold per playlist.

Two outputs, both consumed by route.py:

    model.joblib      the fitted pipeline plus its class list
    thresholds.json   per-playlist confidence needed to place a track
                      unattended, and the measured precision at that point

A playlist with no threshold never routes automatically — its tracks always go
to the review queue instead. That's the intended behaviour for the playlists
that genuinely overlap, not a gap to paper over.

Run via: python app/scripts/vibe_train.py
"""

import argparse
import datetime
import json
import os

import joblib
import numpy as np

from . import config, library, model, probe, sweep


def train(backend, exclude, target_precision, min_tracks=None, refresh=False):
    if refresh:
        # Re-read playlists first: every track you placed from the review queue
        # is a new label, and picking it up is the whole feedback loop.
        library.load_library(refresh=True)
    X, y, stats, sizes = sweep.load_cached(backend, exclude, min_tracks)
    X, y, _, thin = probe.drop_thin_classes(X, y, list(range(len(y))))
    if thin:
        print("Too few usable tracks to learn: "
              + ", ".join(f"{l} ({c})" for l, c in thin))

    print(f"Training on {len(y)} tracks across {len(np.unique(y))} playlists")

    proba, classes = model.out_of_fold_proba(X, y)
    predicted = classes[np.argmax(proba, axis=1)]
    top1 = float((predicted == y).mean())
    top3 = probe.top_k_accuracy(proba, classes, y, min(3, len(classes)))

    rows = probe.per_class_thresholds(proba, classes, y, target_precision)
    thresholds = {r["playlist"]: {"threshold": r["threshold"],
                                  "precision": r["precision"],
                                  "recall": r["recall"],
                                  "support": r["support"]}
                  for r in rows}

    fitted = model.build().fit(X, y)

    routable = [r for r in rows if r["threshold"] is not None]
    auto = sum(sizes.get(r["playlist"], 0) * r["recall"] for r in routable)
    total = sum(sizes.get(r["playlist"], 0) for r in rows)

    print(f"top-1 {top1:.1%}, top-3 {top3:.1%}")
    print(f"{len(routable)}/{len(classes)} playlists can place unattended "
          f"at >= {target_precision:.0%} precision")
    print(f"expected ~{auto / total:.0%} of tracks placed automatically, "
          "the rest queued for review")

    return fitted, classes, thresholds, {
        "backend": backend,
        "trained_at": datetime.datetime.utcnow().isoformat(),
        "n_train": int(len(y)),
        "top1": top1,
        "top3": top3,
        "target_precision": target_precision,
        "exclude": list(exclude or []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="effnet")
    parser.add_argument("--exclude", nargs="*", default=["recap", "hotlist"],
                        metavar="SUBSTRING")
    parser.add_argument("--target-precision", type=float, default=0.90)
    parser.add_argument("--min-tracks", type=int, default=None)
    parser.add_argument("--refresh-library", action="store_true",
                        help="re-read playlists first, so decisions made "
                             "in the review queue become training labels")
    args = parser.parse_args()

    config.ensure_dirs()
    fitted, classes, thresholds, meta = train(
        args.backend, args.exclude, args.target_precision, args.min_tracks,
        refresh=args.refresh_library)

    model_path = os.path.join(config.DATA_DIR, "model.joblib")
    joblib.dump({"pipeline": fitted, "classes": list(classes), **meta},
                model_path)

    thresholds_path = os.path.join(config.DATA_DIR, "thresholds.json")
    with open(thresholds_path, "w", encoding="utf-8") as f:
        json.dump({"thresholds": thresholds, **meta}, f, indent=2,
                  ensure_ascii=False)

    print(f"\nModel:      {model_path}")
    print(f"Thresholds: {thresholds_path}")


if __name__ == "__main__":
    main()
