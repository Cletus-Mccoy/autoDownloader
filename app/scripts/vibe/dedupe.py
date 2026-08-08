"""Find duplicate tracks across and within playlists.

Three kinds, in descending order of certainty:

  A  same videoId, listed twice in one playlist   — certain, always a mistake
  B  same videoId, in several playlists           — certain, but which one wins
                                                    is your call
  C  same song, different videoId                 — a re-upload or second copy,
                                                    matched on title, so it's a
                                                    judgement call

This module only reports. Removing a track from a playlist is destructive and
irreversible from here, so nothing is written to YouTube Music.

Run via: python app/scripts/vibe_dedupe.py
"""

import argparse
import os
import re
import unicodedata

from . import config, library

# Square brackets carry tags rather than song identity: "[House]", "[Free DL]",
# "[NCS Release]". Parentheses usually carry remix info, which IS identity —
# "(Han Conscious Remix)" is a different track — so they're kept.
_BRACKETS = re.compile(r"\[[^\]]*\]")
_NOISE = re.compile(
    r"\b(official\s+(music\s+)?video|official\s+audio|official"
    r"|music\s+video|lyrics?|lyric\s+video|visuali[sz]er"
    r"|hd|hq|4k|free\s+download|free\s+dl|out\s+now|premiere)\b"
)
_NONWORD = re.compile(r"[^a-z0-9]+")


def song_key(title):
    """Normalised title used to match re-uploads of the same song."""
    text = unicodedata.normalize("NFKD", title or "").lower()
    text = _BRACKETS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    return _NONWORD.sub(" ", text).strip()


def find_duplicates(lib, exclude=None):
    patterns = [p.lower() for p in (exclude or ())]
    playlists = [p for p in lib["playlists"]
                 if not any(pat in p["title"].lower() for pat in patterns)]

    within, seen_in_playlist = [], {}
    placements = {}          # videoId -> [(playlist title, track)]
    for pl in playlists:
        counts = {}
        for track in pl["tracks"]:
            counts.setdefault(track["videoId"], []).append(track)
            placements.setdefault(track["videoId"], []).append((pl["title"], track))
        for video_id, entries in counts.items():
            if len(entries) > 1:
                # Keep every entry: each placement has its own setVideoId, and
                # removing a copy needs the specific one.
                within.append({"playlist": pl["title"], "videoId": video_id,
                               "track": entries[0], "entries": entries,
                               "count": len(entries)})
        seen_in_playlist[pl["title"]] = counts

    across = []
    for video_id, entries in placements.items():
        titles = sorted({title for title, _ in entries})
        if len(titles) > 1:
            across.append({"videoId": video_id, "track": entries[0][1],
                           "playlists": titles})

    # Tier C: same song under a different videoId. The key must include the
    # artist — generic titles like "Closer", "Fantasy" and "Vision" are shared
    # by completely unrelated tracks, and matching on title alone produced far
    # more false pairs than real re-uploads.
    by_song = {}
    for video_id, entries in placements.items():
        track = entries[0][1]
        key = song_key(f"{track.get('artist') or ''} {track.get('title') or ''}")
        if not key:
            continue
        by_song.setdefault(key, {})[video_id] = {
            "track": track,
            "playlists": sorted({t for t, _ in entries}),
        }

    reuploads = []
    for key, copies in by_song.items():
        if len(copies) > 1:
            reuploads.append({"key": key, "copies": copies})
    reuploads.sort(key=lambda g: -len(g["copies"]))

    return {"within": within, "across": across, "reuploads": reuploads,
            "placements": placements,
            "playlists": [p["title"] for p in playlists]}


def _label(track):
    return f"{track.get('artist') or '?'} — {track.get('title') or '?'}"


def write_report(path, dupes):
    lines = []
    add = lines.append

    add("# Duplicate tracks\n")
    add(f"- Same track listed twice in one playlist: **{len(dupes['within'])}**")
    add(f"- Same track in several playlists: **{len(dupes['across'])}**")
    add(f"- Same song under different video IDs: **{len(dupes['reuploads'])}** "
        "groups\n")
    add("Nothing here has been changed on YouTube Music.\n")

    add("## A. Listed twice in the same playlist\n")
    if dupes["within"]:
        add("Unambiguous — the playlist contains the identical track more "
            "than once.\n")
        add("| playlist | track | times |")
        add("|---|---|---|")
        for row in sorted(dupes["within"], key=lambda r: r["playlist"]):
            add(f"| {row['playlist']} | {_label(row['track'])} | {row['count']} |")
    else:
        add("None.")
    add("")

    add("## B. Same track in several playlists\n")
    if dupes["across"]:
        add("One track claimed by more than one vibe. These are dropped from "
            "classifier training because their label is ambiguous, so each one "
            "resolved is a track the model can learn from.\n")
        add("| track | playlists |")
        add("|---|---|")
        for row in sorted(dupes["across"], key=lambda r: _label(r["track"]).lower()):
            add(f"| {_label(row['track'])} | {' + '.join(row['playlists'])} |")
    else:
        add("None.")
    add("")

    add("## C. Same song, different video ID\n")
    if dupes["reuploads"]:
        add("Matched on normalised artist + title, so review before acting. "
            "Remixes stay distinct — \"(Han Conscious Remix)\" is a different "
            "track — but a re-upload by the same channel will show up here.\n")
        for group in dupes["reuploads"]:
            first = next(iter(group["copies"].values()))["track"]
            add(f"\n**{_label(first)}**\n")
            add("| videoId | artist / channel | in playlists |")
            add("|---|---|---|")
            for video_id, info in group["copies"].items():
                add(f"| `{video_id}` | {info['track'].get('artist') or '?'} "
                    f"| {', '.join(info['playlists'])} |")
    else:
        add("None.")
    add("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-library", action="store_true",
                        help="re-fetch playlists from YouTube Music")
    parser.add_argument("--exclude", nargs="*", default=None,
                        metavar="SUBSTRING",
                        help="skip playlists whose title contains any of these")
    args = parser.parse_args()

    config.ensure_dirs()
    try:
        lib = library.load_library(refresh=args.refresh_library)
    except library.AuthError as e:
        raise SystemExit(f"\n{e}")

    dupes = find_duplicates(lib, exclude=args.exclude)
    path = os.path.join(config.REPORT_DIR, "duplicates.md")
    write_report(path, dupes)

    print(f"\nScanned {len(dupes['playlists'])} playlists")
    print(f"  A. twice in one playlist : {len(dupes['within'])}")
    print(f"  B. across playlists      : {len(dupes['across'])}")
    print(f"  C. re-uploads (by title) : {len(dupes['reuploads'])} groups")
    print(f"\nReport: {path}")
    print("Nothing was changed on YouTube Music.")


if __name__ == "__main__":
    main()
