"""Put downloads back where their playlist is, and quarantine what is truly spare.

yt-dlp only ever adds. A track moved between playlists — through the misfiled
queue or by hand in YouTube Music, this year or last — stays in the old
playlist's folder and is fetched again into the new one. Tracks removed as
duplicates stay forever. A deleted playlist leaves its whole folder behind.

The ruling question for every file is: where does this track live *now*?
That is answered from the library itself, not from our decision ledgers, so
moves that predate the sorter are handled exactly like last night's.

  relocate    the track is in another playlist and that folder lacks it,
              so the file is moved there and both archives are corrected.
              No re-download, and the file survives even if YouTube has
              since taken the video down.
  spare       the track is in another playlist that already has the file,
              so this copy is redundant.
  double      the same videoId saved twice in one folder.
  removed     the track is in no playlist, and our own ledger records us
              removing it (a resolved duplicate, a recalled placement).
  unexplained the track is in no playlist and nothing explains why. This is
              the case that matters: a video taken down by YouTube and
              dropped from the playlist listing looks exactly like this, and
              the download may be the only copy left. KEPT unless you pass
              --include-unexplained.
  missing     the playlist still wants the track, the archive says it was
              downloaded, and the file is gone. The archive entry is pruned
              so the next run fetches it again.
  ghost       a folder whose playlist no longer exists. Needs --include-ghosts.

Nothing is ever deleted. Spares go to downloads/.orphans/<timestamp>/, which
is a rename on the same filesystem: instant, and undoable by moving them back.

    python housekeeping.py plan
    python housekeeping.py apply --execute
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from download import BASE_DIR, folder_name

VIBE_DIR = os.getenv("VIBE_DATA_DIR", "/app/data/vibe")
LIBRARY_FILE = os.path.join(VIBE_DIR, "library.json")
LEDGERS = [os.path.join(VIBE_DIR, "reports", n)
           for n in ("removals.jsonl", "recalls.jsonl", "moves.jsonl")]
QUARANTINE = os.path.join(BASE_DIR, ".orphans")
AUDIO_EXT = (".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav")
ARCHIVE = "downloaded.txt"
_WATCH = re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})")


def video_id(path):
    """The YouTube id a download came from, or None if it can't be read.

    --embed-metadata writes the watch URL into the file, so this is exact.
    A file we cannot identify is never touched.
    """
    try:
        from mutagen import File as MutagenFile
        tags = MutagenFile(path)
        if tags is None:
            return None
        for key in ("TXXX:purl", "TXXX:comment", "purl", "comment", "WOAS"):
            value = tags.get(key)
            if value:
                found = _WATCH.search(str(value))
                if found:
                    return found.group(1)
        for value in tags.values():
            found = _WATCH.search(str(value))
            if found:
                return found.group(1)
    except Exception:
        return None
    return None


def read_archive(path):
    ids = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[0] == "youtube":
                    ids.append(parts[1])
    except OSError:
        pass
    return ids


def write_archive(path, ids):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(f"youtube {v}\n" for v in ids))
    os.replace(tmp, path)


def deliberately_removed():
    """videoIds our own tools took out of a playlist. Evidence, not guesswork."""
    ids = set()
    for path in LEDGERS:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("videoId"):
                        ids.add(row["videoId"])
        except OSError:
            continue
    return ids


def index_folders(base):
    """{folder: {videoId: filename}} for every playlist folder on disk."""
    index = {}
    for entry in sorted(os.listdir(base)):
        folder = os.path.join(base, entry)
        if not os.path.isdir(folder) or entry.startswith("."):
            continue
        found, unreadable = {}, []
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not name.lower().endswith(AUDIO_EXT):
                continue
            vid = video_id(path)
            if vid is None:
                unreadable.append(name)
            else:
                found.setdefault(vid, []).append(name)
        # Same track saved twice: keep the biggest file, which is the one most
        # likely to be complete, and break ties by name so a re-run agrees
        # with itself. The others are quarantined, never deleted.
        for vid, names in found.items():
            if len(names) > 1:
                names.sort(key=lambda n: (-os.path.getsize(os.path.join(folder, n)), n))
        index[entry] = {"files": found, "unreadable": unreadable}
    return index


def scan(library, base=BASE_DIR, removed_ids=None):
    removed_ids = deliberately_removed() if removed_ids is None else removed_ids
    playlists = library.get("playlists", [])
    by_folder = {folder_name(p["title"]): p for p in playlists}
    folder_of = {p["title"]: folder_name(p["title"]) for p in playlists}
    homes = {}                                   # videoId -> [playlist titles]
    for p in playlists:
        for t in p.get("tracks", []):
            homes.setdefault(t["videoId"], []).append(p["title"])

    index = index_folders(base)
    # Two folders can each hold a copy of the same track that now lives in a
    # third. Only the first is relocated; the rest are spare, or they would
    # all land in the target and leave a double behind.
    incoming = {}
    report = {"playlists": [], "ghosts": [], "unreadable": 0, "scanned": 0,
              "relocations": [], "counts": {}}

    for entry, disk in index.items():
        playlist = by_folder.get(entry)
        files = disk["files"]
        report["scanned"] += sum(len(v) for v in files.values()) + len(disk["unreadable"])
        report["unreadable"] += len(disk["unreadable"])

        if playlist is None:
            report["ghosts"].append({
                "folder": entry,
                "files": sum(len(v) for v in files.values()) + len(disk["unreadable"]),
                "bytes": sum(os.path.getsize(os.path.join(base, entry, n))
                             for names in files.values() for n in names),
            })
            continue

        members = {t["videoId"] for t in playlist.get("tracks", [])}
        buckets = {k: [] for k in ("relocate", "spare", "double",
                                   "removed", "unexplained")}

        for vid, names in files.items():
            keeper, extras = names[0], names[1:]
            for name in extras:
                buckets["double"].append({
                    "file": name, "videoId": vid,
                    "bytes": os.path.getsize(os.path.join(base, entry, name))})
            if vid in members:
                continue

            item = {"file": keeper, "videoId": vid,
                    "bytes": os.path.getsize(os.path.join(base, entry, keeper))}
            elsewhere = [t for t in homes.get(vid, []) if t != playlist["title"]]
            if elsewhere:
                target_title = sorted(elsewhere)[0]
                target = folder_of[target_title]
                item["to_title"] = target_title
                item["to"] = target
                item["ambiguous"] = len(elsewhere) > 1
                already = (vid in index.get(target, {}).get("files", {})
                           or vid in incoming.get(target, ()))
                if already:
                    buckets["spare"].append(item)
                else:
                    incoming.setdefault(target, set()).add(vid)
                    buckets["relocate"].append(item)
            elif vid in removed_ids:
                buckets["removed"].append(item)
            else:
                buckets["unexplained"].append(item)

        archive_path = os.path.join(base, entry, ARCHIVE)
        archive = read_archive(archive_path)
        missing = [v for v in archive if v in members and v not in files]

        if any(buckets.values()) or missing:
            report["playlists"].append({
                "folder": entry, "title": playlist["title"],
                "tracks": len(members),
                "files": sum(len(v) for v in files.values()),
                "archive_entries": len(archive), "missing": missing, **buckets,
            })

    for key in ("relocate", "spare", "double", "removed", "unexplained", "missing"):
        report["counts"][key] = sum(len(p[key]) for p in report["playlists"])
    return report


def human(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def print_report(report, verbose=False):
    def sample(items, fmt):
        for item in (items if verbose else items[:4]):
            print(fmt(item))
        if not verbose and len(items) > 4:
            print(f"        ... and {len(items) - 4} more")

    for p in report["playlists"]:
        print(f"\n{p['title']}  ({p['folder']})")
        print(f"  {p['tracks']} tracks in the playlist, {p['files']} files on disk")
        if p["relocate"]:
            print(f"  {len(p['relocate'])} to move to the playlist they are in now")
            sample(p["relocate"], lambda f: f"      -> {f['to_title']:<32} {f['file'][:48]}"
                   + ("   (in several playlists)" if f.get("ambiguous") else ""))
        if p["spare"]:
            print(f"  {len(p['spare'])} spare — already downloaded in their playlist")
            sample(p["spare"], lambda f: f"      {f['file'][:70]}")
        if p["double"]:
            print(f"  {len(p['double'])} double — same track saved twice here")
            sample(p["double"], lambda f: f"      {f['file'][:70]}")
        if p["removed"]:
            print(f"  {len(p['removed'])} removed by us (duplicate or recall)")
            sample(p["removed"], lambda f: f"      {f['file'][:70]}")
        if p["unexplained"]:
            print(f"  {len(p['unexplained'])} unexplained — KEPT")
            sample(p["unexplained"], lambda f: f"      {f['file'][:70]}")
        if p["missing"]:
            print(f"  {len(p['missing'])} wanted but not on disk — archive pruned "
                  "so the next run fetches them")

    if report["ghosts"]:
        print("\nFolders whose playlist no longer exists:")
        for g in report["ghosts"]:
            print(f"  {g['folder']:<34} {g['files']:>5} files  {human(g['bytes'])}")

    c = report["counts"]
    freed = sum(f["bytes"] for p in report["playlists"]
                for f in p["spare"] + p["double"] + p["removed"])
    print(f"\nScanned {report['scanned']} file(s)"
          + (f", {report['unreadable']} with no readable id (never touched)"
             if report["unreadable"] else ""))
    print(f"  relocate    {c['relocate']:>5}   moved into the right folder, nothing re-downloaded")
    print(f"  spare       {c['spare']:>5}   redundant copies of tracks that moved")
    print(f"  double      {c['double']:>5}   same track twice in one folder")
    print(f"  removed     {c['removed']:>5}   we took these out of the playlist")
    print(f"  unexplained {c['unexplained']:>5}   KEPT — may be gone from YouTube")
    print(f"  missing     {c['missing']:>5}   archive pruned so they download again")
    print(f"Reclaims {human(freed)}.")
    if report["ghosts"]:
        print(f"Ghost folders {len(report['ghosts'])}, "
              f"{human(sum(g['bytes'] for g in report['ghosts']))} "
              "(only with --include-ghosts)")


def _unique(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}.{n}{ext}"):
        n += 1
    return f"{stem}.{n}{ext}"


def apply(report, base=BASE_DIR, include_unexplained=False,
          include_ghosts=False, quarantine=None):
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest_root = os.path.join(quarantine or QUARANTINE, stamp)
    archives = {}          # folder -> list of ids, written once at the end
    ledger = []
    relocated = quarantined = 0

    def archive_for(folder):
        if folder not in archives:
            archives[folder] = read_archive(os.path.join(base, folder, ARCHIVE))
        return archives[folder]

    for p in report["playlists"]:
        folder = p["folder"]
        src_archive = archive_for(folder)

        for f in p["relocate"]:
            src = os.path.join(base, folder, f["file"])
            if not os.path.exists(src):
                continue
            target_dir = os.path.join(base, f["to"])
            os.makedirs(target_dir, exist_ok=True)
            dst = _unique(os.path.join(target_dir, f["file"]))
            shutil.move(src, dst)
            relocated += 1
            if f["videoId"] in src_archive:
                src_archive.remove(f["videoId"])
            target_archive = archive_for(f["to"])
            if f["videoId"] not in target_archive:
                target_archive.append(f["videoId"])
            ledger.append({"kind": "relocate", "videoId": f["videoId"],
                           "file": f["file"], "from": p["title"],
                           "to": f["to_title"]})

        kinds = ["spare", "double", "removed"]
        if include_unexplained:
            kinds.append("unexplained")
        for kind in kinds:
            for f in p[kind]:
                src = os.path.join(base, folder, f["file"])
                if not os.path.exists(src):
                    continue
                dest = os.path.join(dest_root, folder)
                os.makedirs(dest, exist_ok=True)
                shutil.move(src, _unique(os.path.join(dest, f["file"])))
                quarantined += 1
                # A double's id stays: the copy we kept is still here.
                if kind != "double" and f["videoId"] in src_archive:
                    src_archive.remove(f["videoId"])
                ledger.append({"kind": kind, "videoId": f["videoId"],
                               "file": f["file"], "from": p["title"]})

        for vid in p["missing"]:
            if vid in src_archive:
                src_archive.remove(vid)

    if include_ghosts:
        for g in report["ghosts"]:
            os.makedirs(dest_root, exist_ok=True)
            shutil.move(os.path.join(base, g["folder"]),
                        os.path.join(dest_root, g["folder"]))
            archives.pop(g["folder"], None)
            quarantined += g["files"]
            ledger.append({"kind": "ghost", "file": g["folder"]})

    for folder, ids in archives.items():
        path = os.path.join(base, folder, ARCHIVE)
        if os.path.isdir(os.path.dirname(path)):
            write_archive(path, ids)

    if ledger:
        os.makedirs(dest_root, exist_ok=True)
        with open(os.path.join(dest_root, "housekeeping.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False)
    return relocated, quarantined, dest_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--execute", action="store_true",
                        help="apply only: actually move; without it, a dry run")
    parser.add_argument("--include-unexplained", action="store_true",
                        help="also quarantine files whose track is in no "
                             "playlist and whose removal we cannot explain. "
                             "These may be gone from YouTube for good")
    parser.add_argument("--include-ghosts", action="store_true",
                        help="also quarantine folders whose playlist is gone")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    with open(LIBRARY_FILE, encoding="utf-8") as f:
        library = json.load(f)
    if not library.get("playlists"):
        raise SystemExit("Cached library has no playlists — refresh it first.")
    print(f"Library snapshot: {library.get('fetched_at', '?')}")

    report = scan(library)
    print_report(report, args.verbose)

    if args.command == "plan":
        print("\nA report only. `apply --execute` relocates and quarantines.")
        return
    if not args.execute:
        print("\nDRY RUN — nothing moved. Re-run apply with --execute.")
        return

    relocated, quarantined, dest = apply(
        report, include_unexplained=args.include_unexplained,
        include_ghosts=args.include_ghosts)
    print(f"\nRelocated {relocated} file(s) into the playlist they belong to")
    print(f"Quarantined {quarantined} file(s) under {dest}")
    print("Nothing was deleted; move them back if this got anything wrong.")


if __name__ == "__main__":
    main()
