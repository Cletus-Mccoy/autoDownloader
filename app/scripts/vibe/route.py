"""Sort liked tracks into playlists: confident ones now, the rest to a queue.

This is the piece the whole project exists for. A liked track that isn't in any
playlist yet gets a snippet, an embedding and a prediction. If its best
playlist clears that playlist's threshold, it's added straight away. If not, it
lands in a review queue with the three most likely playlists, and you pick one
in the web UI.

Two properties worth keeping:

  add-only          a track is never removed from a playlist and never
                    unliked. The worst case is a track in the wrong place,
                    which you fix by hand — never silent loss.
  labels are free   picking a playlist in the UI puts the track in it, and the
                    next training run reads playlists as labels. Your
                    corrections become training data with no extra plumbing.

Downloading is unchanged: once a track is in a playlist, the existing
downloader collects it on its next run.

    python app/scripts/vibe_route.py            # dry run
    python app/scripts/vibe_route.py --execute  # actually files them
"""

import argparse
import datetime
import json
import os

import joblib
import numpy as np

from ytmusic_auth import headers_to_ytmusic

from . import audio, config, embed, library

QUEUE_FILE = "sort_queue.json"
DECISIONS_FILE = "decisions.jsonl"


def queue_path():
    return os.path.join(config.DATA_DIR, QUEUE_FILE)


def load_bundle():
    path = os.path.join(config.DATA_DIR, "model.joblib")
    if not os.path.exists(path):
        raise SystemExit("No trained model. Run: "
                         "python app/scripts/vibe_train.py")
    bundle = joblib.load(path)
    thresholds_path = os.path.join(config.DATA_DIR, "thresholds.json")
    with open(thresholds_path, encoding="utf-8") as f:
        thresholds = json.load(f)["thresholds"]
    return bundle, thresholds


def unsorted_liked(lib, exclude):
    """Liked tracks that aren't in any playlist we manage."""
    patterns = [p.lower() for p in (exclude or ())]
    filed = {t["videoId"]
             for pl in lib["playlists"]
             if not any(p in pl["title"].lower() for p in patterns)
             for t in pl["tracks"]}
    return [t for t in lib["liked"] if t["videoId"] not in filed]


def decide(vectors, tracks, bundle, thresholds, shortlist):
    """Split tracks into confident placements and queue entries."""
    ids = [t["videoId"] for t in tracks if t["videoId"] in vectors]
    if not ids:
        return [], []
    X = np.vstack([vectors[v] for v in ids])
    proba = bundle["pipeline"].predict_proba(X)
    classes = np.array(bundle["classes"])
    by_id = {t["videoId"]: t for t in tracks}

    placements, queued = [], []
    for i, video_id in enumerate(ids):
        order = np.argsort(-proba[i])
        best = classes[order[0]]
        p_best = float(proba[i, order[0]])
        rule = thresholds.get(str(best), {})
        threshold = rule.get("threshold")

        options = [{"playlist": str(classes[j]),
                    "confidence": round(float(proba[i, j]), 3)}
                   for j in order[:shortlist]]
        entry = {**by_id[video_id], "options": options}

        if threshold is not None and p_best >= threshold:
            placements.append({**entry, "playlist": str(best),
                               "confidence": round(p_best, 3),
                               "threshold": threshold})
        else:
            queued.append(entry)
    return placements, queued


def log_decisions(records, kind):
    path = os.path.join(config.REPORT_DIR, DECISIONS_FILE)
    stamp = datetime.datetime.utcnow().isoformat()
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps({**record, "kind": kind, "at": stamp},
                               ensure_ascii=False) + "\n")


def place(placements, lib):
    ytmusic = headers_to_ytmusic()
    library.check_auth(ytmusic)
    ids = {p["title"]: p["id"] for p in lib["playlists"]}

    by_playlist = {}
    for item in placements:
        by_playlist.setdefault(item["playlist"], []).append(item)

    done = failed = 0
    for title, items in sorted(by_playlist.items()):
        playlist_id = ids.get(title)
        if not playlist_id:
            print(f"  ✗ {title}: unknown playlist, skipped")
            failed += len(items)
            continue
        try:
            ytmusic.add_playlist_items(
                playlist_id, [i["videoId"] for i in items], duplicates=False)
        except Exception as e:
            print(f"  ✗ {title}: {e}")
            failed += len(items)
            continue
        done += len(items)
        print(f"  ✓ {title}: added {len(items)}")
    return done, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exclude", nargs="*", default=["recap", "hotlist"],
                        metavar="SUBSTRING")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-new-audio", type=int, default=None, metavar="N",
                        help="cap new downloads this run; the rest wait for "
                             "the next one")
    parser.add_argument("--shortlist", type=int, default=3,
                        help="how many options to offer for a queued track")
    parser.add_argument("--prune-audio", action="store_true")
    parser.add_argument("--refresh-library", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true",
                        help="actually add tracks; otherwise dry run")
    args = parser.parse_args()

    config.ensure_dirs()
    bundle, thresholds = load_bundle()
    print(f"Model trained {bundle['trained_at']} on {bundle['n_train']} tracks "
          f"(top-1 {bundle['top1']:.0%}, top-3 {bundle['top3']:.0%})")

    try:
        lib = library.load_library(refresh=args.refresh_library)
    except library.AuthError as e:
        raise SystemExit(f"\n{e}")

    pending = unsorted_liked(lib, args.exclude)
    print(f"{len(pending)} liked track(s) not in any playlist")
    if not pending:
        print("Nothing to sort.")
        return

    paths = audio.fetch_many(pending, workers=args.workers,
                             max_new=args.max_new_audio)
    vectors = embed.embed_tracks(paths, backend=bundle["backend"],
                                 prune_audio=args.prune_audio)
    placements, queued = decide(vectors, pending, bundle, thresholds,
                                args.shortlist)

    print(f"\n{len(placements)} confident placement(s), "
          f"{len(queued)} queued for review")
    for item in placements[:20]:
        print(f"  {item['playlist']}  <-  {item['artist']} — {item['title']} "
              f"({item['confidence']})")
    if len(placements) > 20:
        print(f"  ... and {len(placements) - 20} more")

    if not args.execute:
        print("\nDRY RUN — nothing added, queue not written. "
              "Re-run with --execute.")
        return

    if placements:
        print()
        done, failed = place(placements, lib)
        log_decisions(placements, "auto")
        print(f"Placed {done}, failed {failed}")

    with open(queue_path(), "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.datetime.utcnow().isoformat(),
                   "tracks": queued}, f, indent=2, ensure_ascii=False)
    print(f"Review queue ({len(queued)}): {queue_path()}")
    print("Open the web UI at /sort to place them.")


if __name__ == "__main__":
    main()
