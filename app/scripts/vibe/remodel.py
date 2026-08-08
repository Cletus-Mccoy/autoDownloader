"""Propose moving misfiled tracks between existing playlists.

Different job from routing. Routing places *new* likes; this re-examines
tracks already filed, and asks which ones sit somewhere that doesn't match how
they sound. Your playlists stay as they are — no merging, no new playlists —
only individual tracks move, so the main distinctions survive.

How a candidate is found: every track gets an out-of-fold prediction, so its
own playlist never trained on it. A track is a candidate when its current
playlist scores badly AND one other playlist scores well. That asymmetry
matters — a track the model is simply unsure about is not evidence of
misfiling, only a track it confidently places elsewhere is.

The honest limit: the model hears audio and nothing else. If a track is in a
playlist because of when you found it or who sent it, the model will call that
a mistake with total confidence. **These are hypotheses, not corrections** —
which is why the blank `action` column means "leave it alone", and why an
unreviewed file moves nothing.

    plan    writes remodel_review.csv, and a summary of where churn would land
    apply   executes it — dry run unless --execute

Run via: python app/scripts/vibe_remodel.py plan
"""

import argparse
import csv
import datetime
import json
import os

import numpy as np

from ytmusic_auth import headers_to_ytmusic

from . import config, library, model, sweep

REVIEW_FILE = "remodel_review.csv"
LEDGER_FILE = "moves.jsonl"
FIELDS = ["videoId", "artist", "title", "current", "p_current",
          "suggested", "p_suggested", "action"]
YES = {"m", "move", "y", "yes", "1", "x"}


def find_candidates(X, y, rows, max_current, min_target):
    """Tracks whose own playlist scores badly and another scores well."""
    proba, classes = model.out_of_fold_proba(X, y)
    index = {label: i for i, label in enumerate(classes)}

    candidates = []
    for i, row in enumerate(rows):
        current = row["label"]
        if current not in index:
            continue
        p_current = float(proba[i, index[current]])
        order = np.argsort(-proba[i])
        best = classes[order[0]]
        if best == current:
            continue
        p_best = float(proba[i, order[0]])
        if p_current <= max_current and p_best >= min_target:
            candidates.append({
                "videoId": row["videoId"],
                "artist": row.get("artist") or "",
                "title": row.get("title") or "",
                "current": current,
                "p_current": round(p_current, 3),
                "suggested": best,
                "p_suggested": round(p_best, 3),
                "action": "",
            })

    candidates.sort(key=lambda c: (-c["p_suggested"], c["p_current"]))
    return candidates


def churn_summary(candidates):
    pairs = {}
    for c in candidates:
        pairs[(c["current"], c["suggested"])] = \
            pairs.get((c["current"], c["suggested"]), 0) + 1
    return sorted(pairs.items(), key=lambda kv: -kv[1])


def write_review(path, candidates):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(candidates)


def read_review(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def execute(moves, lib, ledger_path):
    """Add to the target playlist, then remove from the source.

    Add first on purpose: if the add fails the track stays where it was, which
    is recoverable. Removing first would risk losing the placement entirely.
    """
    ytmusic = headers_to_ytmusic()
    library.check_auth(ytmusic)

    ids = {p["title"]: p["id"] for p in lib["playlists"]}
    set_ids = {}
    for pl in lib["playlists"]:
        for track in pl["tracks"]:
            set_ids[(pl["title"], track["videoId"])] = track.get("setVideoId")

    done = failed = 0
    with open(ledger_path, "a", encoding="utf-8") as ledger:
        for move in moves:
            target, source = move["suggested"], move["current"]
            video_id = move["videoId"]
            target_id, source_id = ids.get(target), ids.get(source)
            set_video_id = set_ids.get((source, video_id))

            if not target_id or not source_id or not set_video_id:
                print(f"  ✗ {move['title']}: missing ids, skipped")
                failed += 1
                continue
            try:
                ytmusic.add_playlist_items(target_id, [video_id],
                                           duplicates=False)
                ytmusic.remove_playlist_items(
                    source_id, [{"videoId": video_id,
                                 "setVideoId": set_video_id}])
            except Exception as e:
                print(f"  ✗ {move['title']}: {e}")
                failed += 1
                continue

            done += 1
            print(f"  ✓ {move['artist']} — {move['title']}: "
                  f"{source} -> {target}")
            ledger.write(json.dumps(
                {**move, "moved_at": datetime.datetime.utcnow().isoformat()},
                ensure_ascii=False) + "\n")

    return done, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--backend", default="effnet")
    parser.add_argument("--exclude", nargs="*", default=["recap", "hotlist"],
                        metavar="SUBSTRING")
    parser.add_argument("--max-current", type=float, default=0.10,
                        help="current playlist must score at or below this "
                             "(default 0.10)")
    parser.add_argument("--min-target", type=float, default=0.60,
                        help="suggested playlist must score at least this "
                             "(default 0.60)")
    parser.add_argument("--execute", action="store_true",
                        help="actually move; without it, apply is a dry run")
    args = parser.parse_args()

    config.ensure_dirs()
    review_path = os.path.join(config.REPORT_DIR, REVIEW_FILE)

    X, y, stats, _sizes = sweep.load_cached(args.backend, args.exclude)
    lib = library.load_library(refresh=False)
    rows, _ = library.labelled_tracks(lib, exclude=args.exclude)

    # Same filter load_cached applied, so rows line up with X row-for-row.
    cache = os.path.join(config.EMBED_DIR, f"{args.backend}.npz")
    with np.load(cache) as data:
        have = set(data.files)
    rows = [r for r in rows if r["videoId"] in have]
    if len(rows) != len(y):
        raise SystemExit(f"row mismatch: {len(rows)} rows vs {len(y)} labels")

    if args.command == "plan":
        candidates = find_candidates(X, y, rows, args.max_current,
                                     args.min_target)
        write_review(review_path, candidates)
        print(f"\n{len(candidates)} candidate move(s) out of {len(rows)} "
              f"tracks ({len(candidates) / len(rows):.1%})")
        print(f"Thresholds: current <= {args.max_current}, "
              f"suggested >= {args.min_target}\n")
        print("Where the churn would land:")
        for (src, dst), n in churn_summary(candidates)[:15]:
            print(f"  {n:4d}  {src}  ->  {dst}")
        print(f"\nReview file: {review_path}")
        print("Put 'm' in the `action` column to accept a move. Blank rows are "
              "left alone.")
        print("Then: python app/scripts/vibe_remodel.py apply")
        return

    reviewed = read_review(review_path)
    moves = [r for r in reviewed
             if (r.get("action") or "").strip().lower() in YES]
    skipped = len(reviewed) - len(moves)

    if not moves:
        print(f"No moves accepted ({skipped} row(s) left blank). "
              "Put 'm' in the `action` column for the ones you want.")
        return

    print(f"\n{len(moves)} move(s) accepted, {skipped} left alone\n")
    for move in moves:
        print(f"  {move['artist']} — {move['title']}")
        print(f"      {move['current']}  ->  {move['suggested']}  "
              f"(p {move['p_current']} -> {move['p_suggested']})")

    if not args.execute:
        print("\nDRY RUN — nothing changed. Re-run with --execute to apply.")
        return

    ledger_path = os.path.join(config.REPORT_DIR, LEDGER_FILE)
    print()
    done, failed = execute(moves, lib, ledger_path)
    print(f"\nMoved {done}, failed {failed}")
    print(f"Ledger: {ledger_path}")


if __name__ == "__main__":
    main()
