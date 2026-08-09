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


def nested_evaluation(X, y, target_precision, outer_folds=5):
    """Honest precision: thresholds chosen on data they are never scored on.

    The simple estimate picks each playlist's threshold on the same
    out-of-fold predictions it then measures — so it selects whatever cutoff
    happened to look best on that exact data, and reports the number it was
    selected for. That flatters it.

    Here the threshold is chosen inside each outer training split and applied
    to the held-out split, which never influenced it. The result is what you'd
    actually get on new tracks.

    Also counts how many outer folds gave each playlist a threshold at all.
    A playlist that qualifies in 5 folds out of 5 is genuinely routable; one
    that qualifies in 2 is noise, and the earlier sweep suggested there is a
    fair amount of that.
    """
    from sklearn.model_selection import StratifiedKFold

    cv = StratifiedKFold(n_splits=outer_folds, shuffle=True,
                         random_state=config.RANDOM_SEED)
    stats = {label: {"accepted": 0, "correct": 0, "support": 0, "folds": 0}
             for label in np.unique(y)}

    for train_idx, test_idx in cv.split(X, y):
        X_tr, y_tr = X[train_idx], y[train_idx]
        inner_proba, inner_classes = model.out_of_fold_proba(X_tr, y_tr)
        chosen = {r["playlist"]: r["threshold"]
                  for r in probe.per_class_thresholds(
                      inner_proba, inner_classes, y_tr, target_precision)
                  if r["threshold"] is not None}
        for label in chosen:
            stats[label]["folds"] += 1

        fitted = model.build().fit(X_tr, y_tr)
        proba = fitted.predict_proba(X[test_idx])
        classes = np.array(fitted.classes_)
        y_test = y[test_idx]

        for label in np.unique(y):
            stats[label]["support"] += int((y_test == label).sum())

        top = classes[np.argmax(proba, axis=1)]
        top_p = proba.max(axis=1)
        for i, predicted in enumerate(top):
            threshold = chosen.get(predicted)
            if threshold is None or top_p[i] < threshold:
                continue
            stats[predicted]["accepted"] += 1
            stats[predicted]["correct"] += int(y_test[i] == predicted)

    rows = []
    for label, s in stats.items():
        rows.append({
            "playlist": label,
            "folds_qualifying": s["folds"],
            "outer_folds": outer_folds,
            "accepted": s["accepted"],
            "precision": (s["correct"] / s["accepted"]) if s["accepted"] else None,
            "recall": (s["accepted"] / s["support"]) if s["support"] else 0.0,
        })
    rows.sort(key=lambda r: (-r["folds_qualifying"], -(r["recall"] or 0)))
    return rows


def train(backend, exclude, target_precision, min_tracks=None, refresh=False,
          nested=False, min_folds=4):
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

    nested_rows = None
    if nested:
        print("Nested evaluation (thresholds chosen on data they aren't "
              "scored on)...")
        nested_rows = nested_evaluation(X, y, target_precision)

        # Gate on the honest numbers. A playlist that qualifies on full data
        # but fails when measured on data its threshold never saw was fitting
        # noise, and letting it place tracks unattended is worse than leaving
        # them in the queue: HARD TECH scored 33% precision this way, and
        # UNSORTED TECH 62%, against a 90% target.
        revoked = []
        for r in nested_rows:
            playlist = r["playlist"]
            if thresholds.get(playlist, {}).get("threshold") is None:
                continue
            unstable = r["folds_qualifying"] < min_folds
            imprecise = (r["precision"] is None
                         or r["precision"] < target_precision)
            if unstable or imprecise:
                thresholds[playlist]["threshold"] = None
                thresholds[playlist]["revoked"] = (
                    f"{r['folds_qualifying']}/{r['outer_folds']} folds, "
                    + ("no held-out placements" if r["precision"] is None
                       else f"{r['precision']:.0%} honest precision"))
                revoked.append(playlist)
        if revoked:
            print(f"Revoked unattended placement for {len(revoked)} playlist(s) "
                  f"that only looked good on their own data:")
            for playlist in revoked:
                print(f"  {playlist} — {thresholds[playlist]['revoked']}")

    fitted = model.build().fit(X, y)

    # Read back from `thresholds`, not `rows` — the nested gate may have
    # revoked some, and reporting the pre-gate count would overstate what the
    # sorter will actually do.
    routable = [r for r in rows
                if thresholds[r["playlist"]]["threshold"] is not None]
    auto = sum(sizes.get(r["playlist"], 0) * r["recall"] for r in routable)
    total = sum(sizes.get(r["playlist"], 0) for r in rows)

    print(f"top-1 {top1:.1%}, top-3 {top3:.1%}")
    print(f"{len(routable)}/{len(classes)} playlists can place unattended "
          f"at >= {target_precision:.0%} precision")
    print(f"expected ~{auto / total:.0%} of tracks placed automatically, "
          "the rest queued for review")

    if nested_rows:
        stable = [r for r in nested_rows if r["folds_qualifying"] == r["outer_folds"]]
        scored = [r for r in nested_rows if r["precision"] is not None]
        pooled_accepted = sum(r["accepted"] for r in scored)
        pooled_correct = sum(r["precision"] * r["accepted"] for r in scored)
        print("\nHonest (nested) estimate:")
        print(f"  {len(stable)} playlist(s) qualify in all "
              f"{nested_rows[0]['outer_folds']} folds — the rest are unstable")
        if pooled_accepted:
            print(f"  measured precision on held-out data: "
                  f"{pooled_correct / pooled_accepted:.1%} "
                  f"(target was {target_precision:.0%})")
        print(f"  {'playlist':<38} {'folds':>5} {'prec':>6} {'recall':>7}")
        for r in nested_rows:
            if not r["folds_qualifying"]:
                continue
            prec = f"{r['precision']:.0%}" if r["precision"] is not None else "—"
            print(f"  {r['playlist'][:38]:<38} "
                  f"{r['folds_qualifying']}/{r['outer_folds']:<3} "
                  f"{prec:>6} {r['recall']:>6.0%}")

    return fitted, classes, thresholds, nested_rows, {
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
    parser.add_argument("--min-folds", type=int, default=4, metavar="N",
                        help="with --nested, a playlist must qualify in at "
                             "least N of 5 outer folds to place unattended "
                             "(default 4)")
    parser.add_argument("--nested", action="store_true",
                        help="also measure precision with nested CV, where "
                             "thresholds are picked on data they are not "
                             "scored on. Slower, but the honest number")
    parser.add_argument("--refresh-library", action="store_true",
                        help="re-read playlists first, so decisions made "
                             "in the review queue become training labels")
    args = parser.parse_args()

    config.ensure_dirs()
    fitted, classes, thresholds, nested_rows, meta = train(
        args.backend, args.exclude, args.target_precision, args.min_tracks,
        refresh=args.refresh_library, nested=args.nested,
        min_folds=args.min_folds)
    if nested_rows:
        meta["nested"] = nested_rows

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
