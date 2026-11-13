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

from . import audio, config, embed, library, model, probe, sweep


def _nan_to_none(obj):
    if isinstance(obj, float):
        return obj if obj == obj and abs(obj) != float("inf") else None
    if isinstance(obj, dict):
        return {k: _nan_to_none(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_nan_to_none(v) for v in obj]
    return obj


def nested_evaluation(X, y, target_precision, outer_folds=5, build=None):
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

    build = build or model.build
    cv = StratifiedKFold(n_splits=outer_folds, shuffle=True,
                         random_state=config.RANDOM_SEED)
    stats = {label: {"accepted": 0, "correct": 0, "support": 0, "folds": 0}
             for label in np.unique(y)}

    for train_idx, test_idx in cv.split(np.zeros(len(y)), y):
        X_tr, y_tr = X[train_idx], y[train_idx]
        inner_proba, inner_classes = model.out_of_fold_proba(X_tr, y_tr, build=build)
        chosen = {r["playlist"]: r["threshold"]
                  for r in probe.per_class_thresholds(
                      inner_proba, inner_classes, y_tr, target_precision)
                  if r["threshold"] is not None}
        for label in chosen:
            stats[label]["folds"] += 1

        fitted = build().fit(X_tr, y_tr)
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


def ensure_embeddings(lib, backend, exclude, min_tracks=None, workers=3,
                      max_new=None, prune_audio=False):
    """Fetch and embed every labelled track that has no vector yet.

    Training used to assume the probe had already built the embedding cache.
    In the container nobody ever ran the probe, so the nightly retrain exited
    with "No embedding cache" every night from the day it shipped, and the
    sorter never placed a single track. Labels come from the playlists, so
    the training set changes every night anyway; fetching what's missing is
    part of training, not a one-off someone has to remember.

    max_new bounds a first run on an empty cache: it drains over several
    nights rather than pulling every labelled track at once, and training
    proceeds on whatever is embedded so far.

    Returns the number of labelled tracks still without a vector.
    """
    rows, _ = library.labelled_tracks(lib, min_tracks=min_tracks,
                                      exclude=exclude)
    have = embed.cached_ids(backend)
    missing = [r for r in rows if r["videoId"] not in have]
    print(f"Embeddings ({backend}): {len(rows) - len(missing)} of "
          f"{len(rows)} labelled tracks cached")
    if not missing:
        return 0

    paths = audio.fetch_many(missing, workers=workers, max_new=max_new)
    if paths:
        embed.embed_tracks(paths, backend=backend, prune_audio=prune_audio)
    have = embed.cached_ids(backend)
    still_missing = sum(1 for r in missing if r["videoId"] not in have)
    if still_missing:
        print(f"  {still_missing} labelled track(s) still unembedded; "
              "training on the rest")
    return still_missing


def evaluate(X, y, sizes, target_precision, nested=False, min_folds=4,
             tolerance=0.05, build=None):
    """Score one candidate model the way it would be used: thresholds per
    playlist, the nested gate, and the share of tracks it would place.
    Returns a dict with everything train() writes, plus the fitted model."""
    build = build or model.build
    proba, classes = model.out_of_fold_proba(X, y, build=build)
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
        nested_rows = nested_evaluation(X, y, target_precision, build=build)

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
            # Tolerance, because the honest precision is itself a measurement
            # on a few hundred held-out tracks. Demanding the point estimate
            # clear the target exactly revoked Feeling Good! at 89% and SPEED
            # GARAGE at 87% — both stable in 5 folds out of 5 — which throws
            # away good automation over sampling noise. Instability is the
            # real disqualifier; being a couple of points under is not.
            unstable = r["folds_qualifying"] < min_folds
            imprecise = (r["precision"] is None
                         or r["precision"] < target_precision - tolerance)
            if unstable or imprecise:
                thresholds[playlist]["threshold"] = None
                thresholds[playlist]["revoked"] = (
                    ("unstable: " if unstable else "imprecise: ")
                    + f"{r['folds_qualifying']}/{r['outer_folds']} folds, "
                    + ("no held-out placements" if r["precision"] is None
                       else f"{r['precision']:.0%} honest precision"))
                revoked.append(playlist)
        if revoked:
            print(f"Revoked unattended placement for {len(revoked)} playlist(s) "
                  f"that only looked good on their own data:")
            for playlist in revoked:
                print(f"  {playlist} — {thresholds[playlist]['revoked']}")

    fitted = build().fit(X, y)

    # Read back from `thresholds`, not `rows` — the nested gate may have
    # revoked some, and reporting the pre-gate count would overstate what the
    # sorter will actually do.
    routable = [r for r in rows
                if thresholds[r["playlist"]]["threshold"] is not None]
    auto = sum(sizes.get(r["playlist"], 0) * r["recall"] for r in routable)
    total = sum(sizes.get(r["playlist"], 0) for r in rows)
    auto_share = auto / total if total else 0.0

    print(f"top-1 {top1:.1%}, top-3 {top3:.1%}")
    print(f"{len(routable)}/{len(classes)} playlists can place unattended "
          f"at >= {target_precision:.0%} precision")
    print(f"expected ~{auto_share:.0%} of tracks placed automatically, "
          "the rest queued for review")

    honest = None
    if nested_rows:
        stable = [r for r in nested_rows if r["folds_qualifying"] == r["outer_folds"]]
        scored = [r for r in nested_rows if r["precision"] is not None]
        pooled_accepted = sum(r["accepted"] for r in scored)
        pooled_correct = sum(r["precision"] * r["accepted"] for r in scored)
        honest = pooled_correct / pooled_accepted if pooled_accepted else None
        print("\nHonest (nested) estimate:")
        print(f"  {len(stable)} playlist(s) qualify in all "
              f"{nested_rows[0]['outer_folds']} folds — the rest are unstable")
        if pooled_accepted:
            print(f"  measured precision on held-out data: "
                  f"{honest:.1%} (target was {target_precision:.0%})")
        print(f"  {'playlist':<38} {'folds':>5} {'prec':>6} {'recall':>7}")
        for r in nested_rows:
            if not r["folds_qualifying"]:
                continue
            prec = f"{r['precision']:.0%}" if r["precision"] is not None else "—"
            print(f"  {r['playlist'][:38]:<38} "
                  f"{r['folds_qualifying']}/{r['outer_folds']:<3} "
                  f"{prec:>6} {r['recall']:>6.0%}")

    return {"fitted": fitted, "classes": classes, "thresholds": thresholds,
            "nested": nested_rows, "top1": top1, "top3": top3,
            "routable": len(routable), "auto_share": auto_share,
            "honest_precision": honest, "n_train": int(len(y))}


# The piece model takes over only once nearly every training track has piece
# vectors (the backlog drains over several nights), and only when it would
# place more tracks unattended without losing precision.
PIECES_ENABLED = os.getenv("VIBE_PIECES", "true").lower() == "true"
PIECES_MIN_COVERAGE = float(os.getenv("VIBE_PIECES_MIN_COVERAGE", "0.95"))
PIECES_MAX_NEW = int(os.getenv("VIBE_PIECES_MAX_NEW", "400"))


def better(candidate, current, target_precision, tolerance):
    """Whether the piece model should replace the one-vector model."""
    if candidate["auto_share"] <= current["auto_share"]:
        return False
    honest = candidate["honest_precision"]
    if current["honest_precision"] is not None:   # nested ran: hold the bar
        if honest is None or honest < target_precision - tolerance:
            return False
    return True


def _summary(result):
    return {k: result[k] for k in ("top1", "top3", "routable", "auto_share",
                                   "honest_precision", "n_train")}


def train(backend, exclude, target_precision, min_tracks=None, refresh=False,
          nested=False, min_folds=4, tolerance=0.05, workers=3, max_new=None,
          prune_audio=False, pieces_enabled=None, pieces_max_new=None):
    # Re-read playlists first: every track you placed from the review queue
    # is a new label, and picking it up is the whole feedback loop.
    lib = library.load_library(refresh=refresh)
    ensure_embeddings(lib, backend, exclude, min_tracks, workers=workers,
                      max_new=max_new, prune_audio=prune_audio)
    X, y, stats, sizes = sweep.load_cached(backend, exclude, min_tracks)
    keep_rows = sweep.load_cached_rows(backend, exclude, min_tracks)
    X, y, kept, thin = probe.drop_thin_classes(X, y, list(range(len(y))))
    keep_rows = [keep_rows[i] for i in kept]
    if thin:
        print("Too few usable tracks to learn: "
              + ", ".join(f"{l} ({c})" for l, c in thin))

    print(f"Training on {len(y)} tracks across {len(np.unique(y))} playlists")
    current = evaluate(X, y, sizes, target_precision, nested, min_folds, tolerance)
    chosen, chosen_backend = current, backend
    candidates = {backend: _summary(current)}

    pieces_enabled = PIECES_ENABLED if pieces_enabled is None else pieces_enabled
    if pieces_enabled:
        from . import pieces
        cap = PIECES_MAX_NEW if pieces_max_new is None else pieces_max_new
        vectors = pieces.ensure(keep_rows, max_new=cap)
        have = [i for i, r in enumerate(keep_rows) if r["videoId"] in vectors]
        coverage = len(have) / len(keep_rows) if keep_rows else 0.0
        print(f"\nPiece vectors for {len(have)} of {len(keep_rows)} training "
              f"tracks ({coverage:.0%}; the piece model is tried from "
              f"{PIECES_MIN_COVERAGE:.0%})")
        if coverage >= PIECES_MIN_COVERAGE:
            print(f"Evaluating the piece model ({pieces.BACKEND}, plus artist vote)")
            Xp = model.track_inputs(vectors, [keep_rows[i] for i in have])
            candidate = evaluate(Xp, y[have], sizes, target_precision, nested,
                                 min_folds, tolerance, build=model.piece_build)
            candidates[pieces.BACKEND] = _summary(candidate)
            if better(candidate, current, target_precision, tolerance):
                chosen, chosen_backend = candidate, pieces.BACKEND
            print(f"\nUsing {chosen_backend}: places ~{chosen['auto_share']:.0%} "
                  f"unattended (one-vector model ~{current['auto_share']:.0%}, "
                  f"piece model ~{candidate['auto_share']:.0%})")

    return chosen["fitted"], chosen["classes"], chosen["thresholds"], chosen["nested"], {
        "backend": chosen_backend,
        # The one-vector cache stays the source for the stats, overlap and
        # typical-track views, whichever model routes.
        "vectors_backend": backend,
        "candidates": candidates,
        "trained_at": datetime.datetime.utcnow().isoformat(),
        "n_train": chosen["n_train"],
        "top1": chosen["top1"],
        "top3": chosen["top3"],
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
    parser.add_argument("--precision-tolerance", type=float, default=0.05,
                        metavar="P",
                        help="how far under the target a playlist's honest "
                             "precision may fall before losing unattended "
                             "placement (default 0.05)")
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
    parser.add_argument("--workers", type=int, default=3,
                        help="parallel yt-dlp downloads for unembedded "
                             "labelled tracks (default 3)")
    parser.add_argument("--max-new-audio", type=int, default=None, metavar="N",
                        help="cap snippet downloads this run; the rest wait "
                             "for the next one")
    parser.add_argument("--prune-audio", action="store_true",
                        help="delete each snippet once its vector is cached")
    parser.add_argument("--no-pieces", action="store_false", dest="pieces",
                        default=None,
                        help="skip the piece model (9 loudest pieces of the "
                             "whole track, plus the artist vote)")
    parser.add_argument("--pieces-max-new", type=int, default=None, metavar="N",
                        help="cap tracks given piece vectors this run "
                             f"(default {PIECES_MAX_NEW}, env VIBE_PIECES_MAX_NEW)")
    args = parser.parse_args()

    config.ensure_dirs()
    try:
        fitted, classes, thresholds, nested_rows, meta = train(
            args.backend, args.exclude, args.target_precision, args.min_tracks,
            refresh=args.refresh_library, nested=args.nested,
            min_folds=args.min_folds, tolerance=args.precision_tolerance,
            workers=args.workers, max_new=args.max_new_audio,
            prune_audio=args.prune_audio, pieces_enabled=args.pieces,
            pieces_max_new=args.pieces_max_new)
    except library.AuthError as e:
        raise SystemExit(f"\n{e}")
    if nested_rows:
        meta["nested"] = nested_rows

    model_path = os.path.join(config.DATA_DIR, "model.joblib")
    joblib.dump({"pipeline": fitted, "classes": list(classes), **meta},
                model_path)

    thresholds_path = os.path.join(config.DATA_DIR, "thresholds.json")
    with open(thresholds_path, "w", encoding="utf-8") as f:
        # allow_nan=False: a playlist with zero accepted tracks has NaN
        # precision, and bare NaN in the file is not JSON — the web app's
        # consumers would choke on it. None is the honest value.
        json.dump(_nan_to_none({"thresholds": thresholds, **meta}), f,
                  indent=2, ensure_ascii=False, allow_nan=False)

    print(f"\nModel:      {model_path}")
    print(f"Thresholds: {thresholds_path}")


if __name__ == "__main__":
    main()
