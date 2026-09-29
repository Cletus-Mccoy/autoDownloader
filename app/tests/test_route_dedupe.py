"""#596: the nightly sort must not put back what a dedupe decision removed."""
import json

import numpy as np
import pytest

from vibe import route
from vibe.removals import dedupe_blocks


class _Pipe:
    """Module-level so joblib can pickle it: 0.9 ONE, 0.1 TWO."""
    def predict_proba(self, X):
        return np.tile([0.9, 0.1], (len(X), 1))


def _tracks(*ids):
    return [{"videoId": v, "title": v, "artist": "a", "duration_seconds": 200} for v in ids]


def _lib():
    return {"playlists": [{"id": "p1", "title": "ONE", "tracks": _tracks("keep")},
                          {"id": "p2", "title": "TWO", "tracks": _tracks("b1")}],
            # loser: tier-C loser, liked, in no playlist. moved: a tier-B
            # removal from ONE that later lost its other placement too.
            "liked": _tracks("keep", "b1", "loser", "moved", "fresh")}


def _ledger(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def vibe_dirs(tmp_path, monkeypatch):
    from vibe import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "EMBED_DIR", str(tmp_path / "embeddings"))
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(config, "REPORT_DIR", str(tmp_path / "reports"))
    config.ensure_dirs()
    return tmp_path


def test_dedupe_blocks_reads_ledger_and_decisions(tmp_path):
    ledger = tmp_path / "removals.jsonl"
    _ledger(ledger,
            {"tier": "C", "playlist": "28. X", "videoId": "wT6", "reason": "kept copy Zq6"},
            {"tier": "B", "playlist": "TWO", "videoId": "b1"},
            {"playlist": "ONE", "videoId": "manual"})  # not a dedupe removal
    decisions = tmp_path / "dupes_decisions.json"
    decisions.write_text(json.dumps({
        "C:k1,l1,l2": {"action": "resolve", "keep": "k1"},
        "C:s1,s2": {"action": "skip"},
    }))
    dropped, pairs = dedupe_blocks(str(ledger), str(decisions))
    assert dropped == {"wT6", "l1", "l2"}
    assert pairs == {("28. X", "wT6"), ("TWO", "b1")}


def test_dedupe_blocks_without_files(tmp_path):
    assert dedupe_blocks(str(tmp_path / "nope.jsonl"), str(tmp_path / "nope.json")) == (set(), set())


def test_decide_never_offers_a_blocked_playlist():
    bundle = {"pipeline": _Pipe(), "classes": ["ONE", "TWO"]}
    thresholds = {"ONE": {"threshold": 0.5}, "TWO": {"threshold": 0.05}}
    vectors = {"moved": np.ones(4), "fresh": np.ones(4)}
    placements, queued = route.decide(vectors, _tracks("moved", "fresh"), bundle,
                                      thresholds, 3, blocked={("ONE", "moved")})
    by_id = {p["videoId"]: p["playlist"] for p in placements}
    assert by_id == {"moved": "TWO", "fresh": "ONE"}
    assert not queued


def test_route_skips_dedupe_losers(vibe_dirs, monkeypatch, capsys):
    import joblib
    joblib.dump({"pipeline": _Pipe(), "classes": ["ONE", "TWO"],
                 "backend": "effnet", "trained_at": "t", "n_train": 4,
                 "top1": 1.0, "top3": 1.0}, vibe_dirs / "model.joblib")
    (vibe_dirs / "thresholds.json").write_text(json.dumps(
        {"thresholds": {"ONE": {"threshold": 0.8}, "TWO": {"threshold": None}}}))
    _ledger(vibe_dirs / "reports" / "removals.jsonl",
            {"tier": "C", "playlist": "ONE", "videoId": "loser", "source": "ui"},
            {"tier": "B", "playlist": "ONE", "videoId": "moved", "source": "ui"})

    monkeypatch.setattr(route.library, "load_library", lambda refresh=False: _lib())
    fetched = []
    monkeypatch.setattr(route.audio, "fetch_many",
                        lambda tracks, **k: fetched.extend(t["videoId"] for t in tracks) or {})
    monkeypatch.setattr(route.embed, "embed_tracks",
                        lambda paths, **k: {v: np.ones(4) for v in ("moved", "fresh")})
    monkeypatch.setattr("sys.argv", ["vibe_route.py", "--no-refresh-library"])

    route.main()

    out = capsys.readouterr().out
    assert "loser" not in fetched
    assert "1 skipped: removed as a duplicate upload" in out
    # fresh is placed in ONE; moved may not go back to ONE and TWO has no
    # threshold, so it waits in the queue.
    assert "1 confident placement(s), 1 queued for review" in out
    assert "ONE  <-  a — fresh" in out
