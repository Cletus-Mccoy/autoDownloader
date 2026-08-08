"""Remove duplicate tracks from playlists, under review.

Two steps, deliberately separated so nothing is deleted without you seeing it:

    plan    writes cleanup_review.csv listing every proposed removal
    apply   executes it — dry-run unless you pass --execute

Tier A (the same videoId listed twice in one playlist) needs no decision, so
those rows arrive pre-filled. Tiers B and C arrive with a blank `keep` column;
**a blank row is skipped**, so an unreviewed file removes only tier A.

Every executed removal is appended to removals.jsonl with enough detail to put
a track back by hand.
"""

import argparse
import csv
import datetime
import json
import os

from ytmusic_auth import headers_to_ytmusic

from . import config, dedupe, library

REVIEW_FILE = "cleanup_review.csv"
LEDGER_FILE = "removals.jsonl"
FIELDS = ["tier", "key", "artist", "title", "choices", "keep", "note"]


def _label(track):
    return f"{track.get('artist') or '?'} — {track.get('title') or '?'}"


def _require_set_video_ids(dupes):
    """setVideoId is only captured by newer fetches; without it nothing works."""
    for entries in dupes["placements"].values():
        for _, track in entries:
            if track.get("setVideoId"):
                return
    raise SystemExit(
        "No setVideoId found in the cached library — removal needs it.\n"
        "Re-fetch with: python app/scripts/vibe_cleanup.py plan --refresh-library"
    )


def build_rows(lib, dupes):
    """Review rows for tiers B and C. Tier A is derived at apply time."""
    rows = []

    for entry in sorted(dupes["across"], key=lambda r: _label(r["track"]).lower()):
        choices = entry["playlists"]
        rows.append({
            "tier": "B",
            "key": entry["videoId"],
            "artist": entry["track"].get("artist") or "",
            "title": entry["track"].get("title") or "",
            "choices": " | ".join(f"{i}) {c}" for i, c in enumerate(choices, 1)),
            "keep": "",
            "note": "keep it in which playlist? removed from the others",
        })

    for group in dupes["reuploads"]:
        copies = list(group["copies"].items())
        all_playlists = {p for _, info in copies for p in info["playlists"]}
        same_playlist = len(all_playlists) == 1
        first = copies[0][1]["track"]
        rows.append({
            "tier": "C",
            "key": ",".join(video_id for video_id, _ in copies),
            "artist": first.get("artist") or "",
            "title": first.get("title") or "",
            "choices": " | ".join(
                f"{i}) {video_id} in {', '.join(info['playlists'])}"
                for i, (video_id, info) in enumerate(copies, 1)),
            "keep": "",
            "note": ("redundant copies in one playlist" if same_playlist else
                     "WARNING: copies live in different playlists — removing "
                     "the loser drops the song from that playlist entirely"),
        })

    return rows


def write_review(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_review(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def tier_a_removals(lib, dupes, playlist_ids):
    """Every copy after the first, where a playlist lists one track twice."""
    removals = []
    for row in dupes["within"]:
        for extra in row["entries"][1:]:
            removals.append({
                "tier": "A",
                "playlist": row["playlist"],
                "playlistId": playlist_ids.get(row["playlist"]),
                "videoId": row["videoId"],
                "setVideoId": extra.get("setVideoId"),
                "label": _label(row["track"]),
                "reason": "listed twice in this playlist",
            })
    return removals


def reviewed_removals(rows, dupes, playlist_ids):
    """Turn filled-in review rows into removals. Blank `keep` is skipped."""
    removals, skipped = [], 0

    for row in rows:
        choice = (row.get("keep") or "").strip()
        if not choice:
            skipped += 1
            continue

        if row["tier"] == "B":
            video_id = row["key"]
            placements = dupes["placements"].get(video_id, [])
            titles = sorted({title for title, _ in placements})
            keeper = _resolve(choice, titles)
            if keeper is None:
                print(f"  ? unreadable keep={choice!r} for {row['title']}")
                continue
            for title, track in placements:
                if title == keeper:
                    continue
                removals.append({
                    "tier": "B",
                    "playlist": title,
                    "playlistId": playlist_ids.get(title),
                    "videoId": video_id,
                    "setVideoId": track.get("setVideoId"),
                    "label": _label(track),
                    "reason": f"kept in {keeper}",
                })

        elif row["tier"] == "C":
            video_ids = row["key"].split(",")
            keeper = _resolve(choice, video_ids)
            if keeper is None:
                print(f"  ? unreadable keep={choice!r} for {row['title']}")
                continue
            for video_id in video_ids:
                if video_id == keeper:
                    continue
                for title, track in dupes["placements"].get(video_id, []):
                    removals.append({
                        "tier": "C",
                        "playlist": title,
                        "playlistId": playlist_ids.get(title),
                        "videoId": video_id,
                        "setVideoId": track.get("setVideoId"),
                        "label": _label(track),
                        "reason": f"kept copy {keeper}",
                    })

    return removals, skipped


def _resolve(choice, options):
    """Accept either a 1-based index or the option text itself."""
    if choice.isdigit():
        index = int(choice)
        return options[index - 1] if 1 <= index <= len(options) else None
    for option in options:
        if option.strip().lower() == choice.lower():
            return option
    return None


def execute(removals, ledger_path):
    ytmusic = headers_to_ytmusic()
    library.check_auth(ytmusic)

    by_playlist = {}
    for item in removals:
        by_playlist.setdefault(item["playlist"], []).append(item)

    done = failed = 0
    with open(ledger_path, "a", encoding="utf-8") as ledger:
        for title, items in sorted(by_playlist.items()):
            playlist_id = items[0]["playlistId"]
            videos = [{"videoId": i["videoId"], "setVideoId": i["setVideoId"]}
                      for i in items if i["setVideoId"]]
            if not playlist_id or not videos:
                print(f"  ✗ {title}: missing playlistId or setVideoId, skipped")
                failed += len(items)
                continue
            try:
                ytmusic.remove_playlist_items(playlist_id, videos)
            except Exception as e:
                print(f"  ✗ {title}: {e}")
                failed += len(items)
                continue
            done += len(videos)
            print(f"  ✓ {title}: removed {len(videos)}")
            stamp = datetime.datetime.utcnow().isoformat()
            for item in items:
                ledger.write(json.dumps({**item, "removed_at": stamp},
                                        ensure_ascii=False) + "\n")

    return done, failed


def _load(args):
    lib = library.load_library(refresh=args.refresh_library)
    dupes = dedupe.find_duplicates(lib, exclude=args.exclude)
    playlist_ids = {p["title"]: p["id"] for p in lib["playlists"]}
    return lib, dupes, playlist_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--refresh-library", action="store_true",
                        help="re-fetch playlists (needed to capture setVideoId)")
    parser.add_argument("--exclude", nargs="*", default=None,
                        metavar="SUBSTRING", help="playlists to skip")
    parser.add_argument("--execute", action="store_true",
                        help="actually remove; without it, apply is a dry run")
    parser.add_argument("--skip-tier-a", action="store_true",
                        help="leave the in-playlist duplicates alone")
    args = parser.parse_args()

    config.ensure_dirs()
    review_path = os.path.join(config.REPORT_DIR, REVIEW_FILE)

    try:
        lib, dupes, playlist_ids = _load(args)
    except library.AuthError as e:
        raise SystemExit(f"\n{e}")

    if args.command == "plan":
        _require_set_video_ids(dupes)
        rows = build_rows(lib, dupes)
        write_review(review_path, rows)
        print(f"\nTier A (no decision needed): "
              f"{sum(r['count'] - 1 for r in dupes['within'])} removals")
        print(f"Tier B rows to review: {len(dupes['across'])}")
        print(f"Tier C rows to review: {len(dupes['reuploads'])}")
        print(f"\nReview file: {review_path}")
        print("Fill the `keep` column (a number from `choices`). Blank rows "
              "are skipped.")
        print("Then: python app/scripts/vibe_cleanup.py apply")
        return

    _require_set_video_ids(dupes)
    removals = [] if args.skip_tier_a else tier_a_removals(lib, dupes, playlist_ids)
    reviewed, skipped = reviewed_removals(read_review(review_path), dupes,
                                          playlist_ids)
    removals.extend(reviewed)

    if not removals:
        print("Nothing to remove.")
        return

    by_tier = {}
    for item in removals:
        by_tier[item["tier"]] = by_tier.get(item["tier"], 0) + 1
    print(f"\n{len(removals)} removals "
          + ", ".join(f"tier {t}: {n}" for t, n in sorted(by_tier.items())))
    print(f"{skipped} review row(s) left blank and skipped\n")
    for item in sorted(removals, key=lambda i: (i["playlist"], i["label"])):
        print(f"  {item['playlist']}  <-  {item['label']}  ({item['reason']})")

    if not args.execute:
        print("\nDRY RUN — nothing changed. Re-run with --execute to apply.")
        return

    ledger_path = os.path.join(config.REPORT_DIR, LEDGER_FILE)
    print()
    done, failed = execute(removals, ledger_path)
    print(f"\nRemoved {done}, failed {failed}")
    print(f"Ledger: {ledger_path}")


if __name__ == "__main__":
    main()
