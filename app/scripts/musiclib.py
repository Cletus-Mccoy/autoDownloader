"""SQLite-backed music library index.

The filesystem stays the source of truth; the DB is a rebuildable cache of
tags so the player can list/search/stream without re-reading files.

Tag editing (planned): `read_tags` has a write-side counterpart to be added
here (`write_tags`) — rows are keyed by (root, relpath) so ids survive a
rescan after an edit, and `scan` re-reads any file whose mtime changed.
"""

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager

from mutagen import File as MutagenFile
from mutagen.id3 import ID3

MUSIC_EXTENSIONS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".wma"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    root         TEXT NOT NULL,
    relpath      TEXT NOT NULL,
    source       TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    artist       TEXT NOT NULL DEFAULT '',
    album        TEXT NOT NULL DEFAULT '',
    album_artist TEXT NOT NULL DEFAULT '',
    track_no     INTEGER,
    disc_no      INTEGER,
    year         INTEGER,
    duration     INTEGER NOT NULL DEFAULT 0,
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0,
    has_art      INTEGER NOT NULL DEFAULT 0,
    tagged       INTEGER NOT NULL DEFAULT 1,
    added_at     REAL NOT NULL,
    search       TEXT NOT NULL DEFAULT '',
    ext_provider TEXT,
    ext_id       TEXT,
    UNIQUE (root, relpath)
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks (artist);
CREATE INDEX IF NOT EXISTS idx_tracks_album  ON tracks (album);
CREATE INDEX IF NOT EXISTS idx_tracks_ext    ON tracks (ext_provider, ext_id);

-- kind: 'local' (user-made, editable) | 'folder' (derived from a download folder, rebuilt on scan).
-- provider/remote_id are reserved for linking to YT Music or other services (see PLAN_music_player.md).
CREATE TABLE IF NOT EXISTS playlists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'local',
    folder     TEXT,
    provider   TEXT,
    remote_id  TEXT,
    created_at REAL NOT NULL,
    UNIQUE (kind, folder)
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id)    ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, track_id)
);
"""

SORTS = {
    "title":    ["title COLLATE NOCASE", "id"],
    "artist":   ["artist COLLATE NOCASE", "album COLLATE NOCASE", "disc_no", "track_no", "title COLLATE NOCASE"],
    "album":    ["album COLLATE NOCASE", "disc_no", "track_no", "title COLLATE NOCASE"],
    "added":    ["added_at", "id"],
    "duration": ["duration", "id"],
}
DEFAULT_DIR = {"added": "desc"}   # newest first unless asked otherwise

_INT_RE = re.compile(r"\d+")
_scan_state = {}          # db_path -> status dict
_scan_state_lock = threading.Lock()



_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _clean_part(part, fallback):
    return _UNSAFE.sub("_", part or "").strip(" .")[:120] or fallback


def _first_int(value):
    m = _INT_RE.search(value or "")
    return int(m.group()) if m else None


def _tag(audio, key):
    try:
        vals = audio.get(key)
    except Exception:
        return ""
    return str(vals[0]).strip() if vals else ""


def _has_art(path, audio):
    try:
        if any(k.startswith("APIC") for k in ID3(path)):
            return True
    except Exception:
        pass
    raw = MutagenFile(path)
    if raw is None:
        return False
    if getattr(raw, "pictures", None):                 # FLAC / Ogg FLAC
        return True
    tags = getattr(raw, "tags", None)
    if tags is not None and hasattr(tags, "get"):
        try:
            if tags.get("covr") or tags.get("metadata_block_picture"):  # MP4 / Vorbis
                return True
        except Exception:
            pass
    return False


_ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
               "tracknumber": "TRCK", "discnumber": "TPOS", "date": "TDRC"}


def _raw_id3_tag(raw, key):
    """Easy mode only covers MP3/FLAC/MP4/Ogg; WAV/AIFF carry plain ID3 frames, so read those directly."""
    try:
        frame = raw.tags.get(_ID3_FRAMES[key])
        return str(frame.text[0]).strip() if frame is not None and frame.text else ""
    except Exception:
        return ""


def read_tags(path):
    """Return a dict of normalised tags for one file (never raises)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    info = {"title": stem, "artist": "", "album": "", "album_artist": "",
            "track_no": None, "disc_no": None, "year": None,
            "duration": 0, "has_art": 0, "tagged": 0}
    # yt-dlp output is "Artist - Title"; use as fallback when tags are empty
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        info["artist"], info["title"] = a.strip(), t.strip()
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return info
    if audio is None:
        return info
    # "tagged" = the file itself carries a title and artist (not just our filename guess)
    raw = None
    if not getattr(audio, "tags", None) or "title" not in (audio.keys() if hasattr(audio, "keys") else []):
        try:
            raw = MutagenFile(path)       # non-easy view, for formats easy mode can't map (WAV/AIFF ID3)
        except Exception:
            raw = None

    def tag(key):
        return _tag(audio, key) or (_raw_id3_tag(raw, key) if raw is not None else "")

    info["tagged"] = int(bool(tag("title") and tag("artist")))
    info["title"] = tag("title") or info["title"]
    info["artist"] = tag("artist") or info["artist"]
    info["album"] = tag("album")
    info["album_artist"] = tag("albumartist")
    info["track_no"] = _first_int(tag("tracknumber"))
    info["disc_no"] = _first_int(tag("discnumber"))
    info["year"] = _first_int(tag("date"))
    try:
        info["duration"] = int(audio.info.length)
    except Exception:
        pass
    try:
        info["has_art"] = int(_has_art(path, audio))
    except Exception:
        pass
    return info


def extract_art(path):
    """Return (bytes, mime) for embedded cover art, or None."""
    try:
        tags = ID3(path)
        for key in tags:
            if key.startswith("APIC"):
                return tags[key].data, tags[key].mime
    except Exception:
        pass
    try:
        raw = MutagenFile(path)
    except Exception:
        return None
    if raw is None:
        return None
    pics = getattr(raw, "pictures", None)
    if pics:
        return pics[0].data, pics[0].mime or "image/jpeg"
    tags = getattr(raw, "tags", None)
    if tags is not None and hasattr(tags, "get"):
        try:
            covr = tags.get("covr")
            if covr:
                c = covr[0]
                mime = "image/png" if getattr(c, "imageformat", 0) == 14 else "image/jpeg"
                return bytes(c), mime
        except Exception:
            pass
    return None


class Library:
    def __init__(self, db_path, roots):
        """roots: list of (absolute_dir, source_label) tuples."""
        self.db_path = db_path
        self.roots = [(os.path.abspath(p), s) for p, s in roots]
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(tracks)")}
            if "tagged" not in cols:      # DB from before the tagged flag: add it and force a re-read
                c.execute("ALTER TABLE tracks ADD COLUMN tagged INTEGER NOT NULL DEFAULT 1")
                c.execute("UPDATE tracks SET mtime=0")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # some bind mounts reject WAL; default journal still works
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── paths ────────────────────────────────────────────────────────────
    def resolve(self, row):
        """Absolute path for a track row, or None if it escapes its root."""
        root = next((r for r, _ in self.roots if r == row["root"]), None)
        if root is None:
            return None
        full = os.path.realpath(os.path.join(root, row["relpath"]))
        if os.path.commonpath([os.path.realpath(root), full]) != os.path.realpath(root):
            return None
        return full

    # ── scanning ─────────────────────────────────────────────────────────
    def scan(self):
        """Incremental scan of all roots. Returns {added, updated, removed, total}."""
        stats = {"added": 0, "updated": 0, "removed": 0}
        now = time.time()
        with self._conn() as c:
            known_roots = {r for r, _ in self.roots}
            for (stale,) in c.execute("SELECT DISTINCT root FROM tracks").fetchall():
                if stale not in known_roots:
                    n = c.execute("DELETE FROM tracks WHERE root=?", (stale,)).rowcount
                    stats["removed"] += n
            for root, source in self.roots:
                existing = {
                    r["relpath"]: (r["size"], r["mtime"])
                    for r in c.execute("SELECT relpath,size,mtime FROM tracks WHERE root=?", (root,))
                }
                seen = set()
                for dirpath, dirnames, files in os.walk(root):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]   # .trash etc.
                    for fname in files:
                        if os.path.splitext(fname)[1].lower() not in MUSIC_EXTENSIONS:
                            continue
                        full = os.path.join(dirpath, fname)
                        rel = os.path.relpath(full, root).replace(os.sep, "/")
                        seen.add(rel)
                        try:
                            st = os.stat(full)
                        except OSError:
                            continue
                        if existing.get(rel) == (st.st_size, st.st_mtime):
                            continue
                        t = read_tags(full)
                        vals = dict(t, source=source, size=st.st_size, mtime=st.st_mtime,
                                    search=" ".join((t["title"], t["artist"], t["album"], rel)).lower())
                        if rel in existing:
                            sets = ",".join(f"{k}=?" for k in vals)
                            c.execute(f"UPDATE tracks SET {sets} WHERE root=? AND relpath=?",
                                      [*vals.values(), root, rel])
                            stats["updated"] += 1
                        else:
                            cols = ["root", "relpath", "added_at", *vals]
                            c.execute(f"INSERT INTO tracks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                      [root, rel, now, *vals.values()])
                            stats["added"] += 1
                for rel in set(existing) - seen:
                    c.execute("DELETE FROM tracks WHERE root=? AND relpath=?", (root, rel))
                    stats["removed"] += 1
            self._sync_folder_playlists(c)
            stats["total"] = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        return stats

    def _sync_folder_playlists(self, c):
        """Every top-level folder of a download root is a read-only playlist (what the downloader creates)."""
        now = time.time()
        folders = {}
        for r in c.execute("SELECT id, relpath FROM tracks WHERE source='ytmusic' AND relpath LIKE '%/%' "
                           "ORDER BY relpath COLLATE NOCASE"):
            folders.setdefault(r["relpath"].split("/", 1)[0], []).append(r["id"])
        for folder, ids in folders.items():
            c.execute("INSERT OR IGNORE INTO playlists (name, kind, folder, created_at) VALUES (?,?,?,?)",
                      (folder.replace("_", " "), "folder", folder, now))
            pid = c.execute("SELECT id FROM playlists WHERE kind='folder' AND folder=?", (folder,)).fetchone()[0]
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
            c.executemany("INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?,?,?)",
                          [(pid, tid, i) for i, tid in enumerate(ids)])
        keep = list(folders)
        marks = ",".join("?" * len(keep))
        c.execute(f"DELETE FROM playlists WHERE kind='folder'" + (f" AND folder NOT IN ({marks})" if keep else ""), keep)

    def scan_async(self):
        """Start a background scan unless one is running. Returns True if started."""
        with _scan_state_lock:
            st = _scan_state.get(self.db_path)
            if st and st["running"]:
                return False
            st = _scan_state[self.db_path] = {"running": True, "started": time.time(),
                                              "finished": None, "result": None, "error": None}

        def work():
            try:
                st["result"] = self.scan()
            except Exception as e:  # surface in scan-status rather than killing the thread silently
                st["error"] = str(e)
            finally:
                st["finished"] = time.time()
                st["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def scan_status(self):
        return dict(_scan_state.get(self.db_path) or {"running": False, "result": None, "error": None})

    # ── queries ──────────────────────────────────────────────────────────
    def list_tracks(self, q="", artist=None, album=None, album_artist=None,
                    source=None, sort="artist", page=1, limit=50, credit=None, direction=None, needs_tags=False):
        where, args = [], []
        for term in q.lower().split():
            where.append("search LIKE ? ESCAPE '\\'")
            args.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        for col, val in (("artist", artist), ("album", album),
                         ("album_artist", album_artist), ("source", source)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        if needs_tags:
            where.append("tagged=0")
        if credit is not None:  # same display name artists()/albums() group by
            where.append("COALESCE(NULLIF(album_artist,''),NULLIF(artist,''),'Unknown Artist')=?")
            args.append(credit)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sort = sort if sort in SORTS else "artist"
        desc = (direction or DEFAULT_DIR.get(sort, "asc")) == "desc"
        order = ", ".join(c + (" DESC" if desc else "") for c in SORTS[sort])
        limit = max(1, min(limit, 500))
        page = max(1, page)
        with self._conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM tracks {clause}", args).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM tracks {clause} ORDER BY {order} LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit]).fetchall()
        return {"tracks": [self.public(r) for r in rows], "total": total, "page": page,
                "pages": max(1, (total + limit - 1) // limit), "limit": limit}

    def get_track(self, track_id):
        with self._conn() as c:
            return c.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()

    def artists(self):
        with self._conn() as c:
            rows = c.execute(
                "SELECT COALESCE(NULLIF(album_artist,''),NULLIF(artist,''),'Unknown Artist') AS name, "
                "COUNT(*) AS tracks, COUNT(DISTINCT album) AS albums FROM tracks "
                "GROUP BY name COLLATE NOCASE ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def albums(self, artist=None):
        sql = ("SELECT album, COALESCE(NULLIF(album_artist,''),NULLIF(artist,''),'Unknown Artist') AS artist, "
               "COUNT(*) AS tracks, MIN(year) AS year, "
               "MIN(CASE WHEN has_art=1 THEN id END) AS art_track_id, MIN(id) AS first_track_id "
               "FROM tracks WHERE album != '' ")
        args = []
        if artist is not None:
            sql += "AND COALESCE(NULLIF(album_artist,''),NULLIF(artist,''),'Unknown Artist')=? "
            args.append(artist)
        sql += "GROUP BY album, artist ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]


    # ── folders / roots overview ─────────────────────────────────────────
    def roots_info(self):
        out = []
        with self._conn() as c:
            for root, source in self.roots:
                rows = c.execute("SELECT relpath, size FROM tracks WHERE root=?", (root,)).fetchall()
                folders = {}
                for r in rows:
                    top = r["relpath"].split("/", 1)[0] if "/" in r["relpath"] else "(top level)"
                    folders[top] = folders.get(top, 0) + 1
                out.append({"path": root, "source": source, "exists": os.path.isdir(root),
                            "tracks": len(rows), "size": sum(r["size"] for r in rows),
                            "folders": [{"name": n, "tracks": k} for n, k in
                                        sorted(folders.items(), key=lambda x: (-x[1], x[0].lower()))[:200]]})
        return out

    # ── organize (sorter) ────────────────────────────────────────────────
    @property
    def _organize_log(self):
        return os.path.join(os.path.dirname(self.db_path) or ".", "organize_log.json")

    def organize_preview(self, source="import"):
        """Plan moves into Artist/Album/NN - Title.ext. Pure planning; touches nothing."""
        moves, conflicts, ok, untagged = [], [], 0, 0
        with self._conn() as c:
            rows = c.execute("SELECT id, root, relpath, title, artist, album, album_artist, track_no, tagged "
                             "FROM tracks WHERE source=? ORDER BY relpath", (source,)).fetchall()
            existing = {(r["root"], r["relpath"].lower()) for r in c.execute("SELECT root, relpath FROM tracks")}
        taken = set()
        for r in rows:
            who = r["album_artist"] or r["artist"]
            if not r["tagged"] or not who:
                untagged += 1
                continue
            ext = os.path.splitext(r["relpath"])[1].lower()
            title = _clean_part(r["title"], "Untitled")
            if r["album"]:
                name = f"{r['track_no']:02d} - {title}" if r["track_no"] else title
                rel = f"{_clean_part(who, 'Unknown Artist')}/{_clean_part(r['album'], 'Unknown Album')}/{name}{ext}"
            else:
                rel = f"{_clean_part(who, 'Unknown Artist')}/{title}{ext}"
            if rel.lower() == r["relpath"].lower():
                ok += 1
                continue
            key = (r["root"], rel.lower())
            entry = {"id": r["id"], "root": r["root"], "from": r["relpath"], "to": rel}
            if key in existing or key in taken or os.path.exists(os.path.join(r["root"], rel)):
                conflicts.append(entry)
                continue
            taken.add(key)
            moves.append(entry)
        return {"moves": moves, "conflicts": conflicts, "already_ok": ok, "untagged": untagged}

    def _move_row(self, c, root, src_rel, dst_rel):
        src, dst = os.path.join(root, src_rel), os.path.join(root, dst_rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        r = c.execute("SELECT title, artist, album FROM tracks WHERE root=? AND relpath=?", (root, src_rel)).fetchone()
        search = " ".join((r["title"], r["artist"], r["album"], dst_rel)).lower() if r else dst_rel.lower()
        # in-place UPDATE keeps the track id, so playlists/history survive the move
        c.execute("UPDATE tracks SET relpath=?, search=? WHERE root=? AND relpath=?", (dst_rel, search, root, src_rel))
        d = os.path.dirname(src)
        while d != root and d.startswith(root) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)

    def organize_apply(self, source="import"):
        plan = self.organize_preview(source)
        done, errors = [], []
        with self._conn() as c:
            for m in plan["moves"]:
                try:
                    self._move_row(c, m["root"], m["from"], m["to"])
                    done.append(m)
                except Exception as e:
                    errors.append({**m, "error": str(e)})
        if done:
            with open(self._organize_log, "w") as f:
                json.dump({"time": time.time(), "source": source, "moves": done}, f)
        return {"moved": len(done), "errors": errors, "conflicts": len(plan["conflicts"])}

    def organize_undo(self):
        try:
            with open(self._organize_log) as f:
                log = json.load(f)
        except (OSError, ValueError):
            return {"restored": 0, "errors": [], "nothing_to_undo": True}
        restored, errors = 0, []
        with self._conn() as c:
            for m in reversed(log["moves"]):
                try:
                    if os.path.exists(os.path.join(m["root"], m["to"])) and not os.path.exists(os.path.join(m["root"], m["from"])):
                        self._move_row(c, m["root"], m["to"], m["from"])
                        restored += 1
                    else:
                        errors.append({**m, "error": "file moved or target occupied"})
                except Exception as e:
                    errors.append({**m, "error": str(e)})
        os.remove(self._organize_log)
        return {"restored": restored, "errors": errors}

    def organize_can_undo(self):
        return os.path.exists(self._organize_log)

    # ── playlists ────────────────────────────────────────────────────────
    def playlists(self):
        with self._conn() as c:
            rows = c.execute(
                "SELECT p.id, p.name, p.kind, COUNT(pt.track_id) AS tracks, "
                "COALESCE(SUM(t.duration),0) AS duration, "
                "(SELECT t2.id FROM playlist_tracks pt2 JOIN tracks t2 ON t2.id=pt2.track_id "
                " WHERE pt2.playlist_id=p.id AND t2.has_art=1 ORDER BY pt2.position LIMIT 1) AS art_track_id "
                "FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id "
                "LEFT JOIN tracks t ON t.id=pt.track_id GROUP BY p.id "
                "ORDER BY (p.kind='local') DESC, p.name COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def get_playlist(self, pid):
        with self._conn() as c:
            r = c.execute("SELECT id, name, kind FROM playlists WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def playlist_tracks(self, pid):
        with self._conn() as c:
            rows = c.execute("SELECT t.* FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id "
                             "WHERE pt.playlist_id=? ORDER BY pt.position", (pid,)).fetchall()
        return [self.public(r) for r in rows]

    def create_playlist(self, name):
        name = (name or "").strip()[:120]
        if not name:
            raise ValueError("Name required")
        with self._conn() as c:
            return c.execute("INSERT INTO playlists (name, kind, created_at) VALUES (?, 'local', ?)",
                             (name, time.time())).lastrowid

    def _local(self, c, pid):
        r = c.execute("SELECT kind FROM playlists WHERE id=?", (pid,)).fetchone()
        if r is None:
            raise LookupError("No such playlist")
        if r["kind"] != "local":
            raise PermissionError("Download-folder playlists are read-only")

    def rename_playlist(self, pid, name):
        name = (name or "").strip()[:120]
        if not name:
            raise ValueError("Name required")
        with self._conn() as c:
            self._local(c, pid)
            c.execute("UPDATE playlists SET name=? WHERE id=?", (name, pid))

    def delete_playlist(self, pid):
        with self._conn() as c:
            self._local(c, pid)
            c.execute("DELETE FROM playlists WHERE id=?", (pid,))

    def add_to_playlist(self, pid, track_ids):
        """Append tracks (skipping ones already present). Returns number added."""
        with self._conn() as c:
            self._local(c, pid)
            pos = c.execute("SELECT COALESCE(MAX(position),-1) FROM playlist_tracks WHERE playlist_id=?",
                            (pid,)).fetchone()[0]
            added = 0
            for tid in track_ids:
                if not c.execute("SELECT 1 FROM tracks WHERE id=?", (tid,)).fetchone():
                    continue
                cur = c.execute("INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) "
                                "VALUES (?,?,?)", (pid, tid, pos + 1))
                if cur.rowcount:
                    pos += 1
                    added += 1
            return added

    def remove_from_playlist(self, pid, track_id):
        with self._conn() as c:
            self._local(c, pid)
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (pid, track_id))

    def stats(self):
        with self._conn() as c:
            r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) sz, COALESCE(SUM(duration),0) dur, "
                          "COALESCE(SUM(tagged=0),0) untagged FROM tracks").fetchone()
            sources = {x["source"]: x["n"] for x in c.execute("SELECT source, COUNT(*) n FROM tracks GROUP BY source")}
        return {"tracks": r["n"], "size": r["sz"], "duration": r["dur"],
                "needs_tags": r["untagged"], "sources": sources}

    @staticmethod
    def public(row):
        return {k: row[k] for k in ("id", "title", "artist", "album", "album_artist", "track_no",
                                    "disc_no", "year", "duration", "size", "has_art", "source",
                                    "relpath", "added_at")}


def default_library(data_dir="/app/data", download_dir="/app/downloads", music_dir="/app/music"):
    """The one place that decides library roots, shared by the web app and the cron scheduler
    (a scan with a different root list would delete the other roots' rows)."""
    roots = [(download_dir, "ytmusic")]
    if os.path.isdir(music_dir):
        roots.append((music_dir, "import"))
    return Library(os.path.join(data_dir, "library.db"), roots)
