"""Take back tracks placed by playlists that no longer qualify.

The honest evaluation can revoke a playlist's right to place tracks unattended
— because it turned out to be fitting noise rather than learning the playlist.
That fixes the future, but it leaves behind whatever it already filed. HARD
TECH measured 33% honest precision and UNSORTED TECH 60%; anything they placed
was a coin flip dressed up as a decision.

This finds those placements, removes them, and lets them fall back to unsorted
so the next run puts them in the review queue for you to judge.

Deliberately narrow: it only touches tracks this tool placed *automatically*,
recorded in decisions.jsonl, into a playlist whose threshold has since been
withdrawn. Anything you filed by hand is never touched, and neither is
anything placed by a playlist that still qualifies.

    python app/scripts/vibe_recall.py            # dry run
    python app/scripts/vibe_recall.py --execute  # actually removes
"""

import argparse
import datetime
import json
import os

from ytmusic_auth import headers_to_ytmusic

from . import config, library

LEDGER_FILE = "recalls.jsonl"


def untrusted_playlists():
    path = os.path.join(config.DATA_DIR, "thresholds.json")
    if not os.path.exists(path):
        raise SystemExit("No thresholds.json — train the sorter first.")
    with open(path, encoding="utf-8") as f:
        thresholds = json.load(f)["thresholds"]
    return {name: rule for name, rule in thresholds.items()
            if rule.get("threshold") is None}


def auto_placements():
    path = os.path.join(config.REPORT_DIR, "decisions.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("kind") == "auto" and d.get("playlist"):
                out.append(d)
    return out


def find_recalls(lib):
    """Auto placements into playlists that have since lost their threshold."""
    untrusted = untrusted_playlists()
    placements = {p["title"]: {t["videoId"]: t.get("setVideoId")
                               for t in p["tracks"]}
                  for p in lib["playlists"]}

    # Later decisions win: a track placed then moved by hand shouldn't be
    # judged on the automatic decision that came first.
    latest = {}
    for d in auto_placements():
        latest[d["videoId"]] = d

    recalls, already_gone = [], 0
    for video_id, d in latest.items():
        playlist = d["playlist"]
        if playlist not in untrusted:
            continue
        members = placements.get(playlist, {})
        if video_id not in members:
            already_gone += 1  # you moved or removed it yourself
            continue
        recalls.append({
            "videoId": video_id,
            "playlist": playlist,
            "setVideoId": members[video_id],
            "artist": d.get("artist"),
            "title": d.get("title"),
            "confidence": d.get("confidence"),
            "reason": untrusted[playlist].get("revoked")
                      or "no longer reaches the precision target",
        })
    recalls.sort(key=lambda r: (r["playlist"], r["title"] or ""))
    return recalls, already_gone


def execute(recalls, lib):
    ytmusic = headers_to_ytmusic()
    library.check_auth(ytmusic)
    ids = {p["title"]: p["id"] for p in lib["playlists"]}

    by_playlist = {}
    for r in recalls:
        by_playlist.setdefault(r["playlist"], []).append(r)

    ledger_path = os.path.join(config.REPORT_DIR, LEDGER_FILE)
    done = failed = 0
    with open(ledger_path, "a", encoding="utf-8") as ledger:
        for playlist, items in sorted(by_playlist.items()):
            playlist_id = ids.get(playlist)
            videos = [{"videoId": i["videoId"], "setVideoId": i["setVideoId"]}
                      for i in items if i["setVideoId"]]
            if not playlist_id or not videos:
                print(f"  ✗ {playlist}: missing ids, skipped")
                failed += len(items)
                continue
            try:
                ytmusic.remove_playlist_items(playlist_id, videos)
            except Exception as e:
                print(f"  ✗ {playlist}: {e}")
                failed += len(items)
                continue
            done += len(videos)
            print(f"  ✓ {playlist}: recalled {len(videos)}")
            stamp = datetime.datetime.utcnow().isoformat()
            for item in items:
                ledger.write(json.dumps({**item, "recalled_at": stamp},
                                        ensure_ascii=False) + "\n")
    return done, failed, ledger_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="actually remove; otherwise dry run")
    parser.add_argument("--refresh-library", action="store_true",
                        help="re-read playlists first")
    args = parser.parse_args()

    config.ensure_dirs()
    try:
        lib = library.load_library(refresh=args.refresh_library)
    except library.AuthError as e:
        raise SystemExit(f"\n{e}")

    recalls, already_gone = find_recalls(lib)
    if already_gone:
        print(f"{already_gone} earlier placement(s) already moved by hand — "
              "left alone")
    if not recalls:
        print("Nothing to recall: every automatic placement was made by a "
              "playlist that still qualifies.")
        return

    print(f"\n{len(recalls)} track(s) placed by playlists that no longer "
          "qualify:\n")
    for r in recalls:
        print(f"  {r['playlist']}")
        print(f"      {r['artist']} — {r['title']}  "
              f"(placed at {r['confidence']})")
        print(f"      {r['reason']}")
    print("\nRemoving these returns them to unsorted, so the next sorter run "
          "puts them in the review queue.")

    if not args.execute:
        print("\nDRY RUN — nothing changed. Re-run with --execute to apply.")
        return

    print()
    done, failed, ledger_path = execute(recalls, lib)
    print(f"\nRecalled {done}, failed {failed}")
    print(f"Ledger: {ledger_path}")


if __name__ == "__main__":
    main()
