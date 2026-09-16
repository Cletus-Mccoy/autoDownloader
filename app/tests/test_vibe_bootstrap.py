"""The sorter must build its own embedding cache and never re-fetch audio
for tracks it has already embedded."""
import numpy as np
import pytest

from vibe import embed, train, route


class _ConfidentPipe:
    """Module-level so joblib can pickle it: always 0.9 for the first class."""
    def predict_proba(self, X):
        return np.tile([0.9, 0.1], (len(X), 1))


def _lib():
    def tracks(*ids):
        return [{"videoId": v, "title": v, "artist": "a", "duration_seconds": 200}
                for v in ids]
    return {
        "playlists": [
            {"id": "p1", "title": "ONE", "tracks": tracks("a1", "a2")},
            {"id": "p2", "title": "TWO", "tracks": tracks("b1", "b2")},
        ],
        "liked": tracks("a1", "a2", "b1", "b2", "n1", "n2"),
    }


@pytest.fixture
def vibe_dirs(tmp_path, monkeypatch):
    from vibe import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "EMBED_DIR", str(tmp_path / "embeddings"))
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(config, "REPORT_DIR", str(tmp_path / "reports"))
    config.ensure_dirs()
    return tmp_path


def _seed_cache(vibe_dirs, backend, ids):
    np.savez_compressed(vibe_dirs / "embeddings" / f"{backend}.npz",
                        **{v: np.ones(4, dtype=np.float32) for v in ids})


def test_cached_ids_empty_without_cache(vibe_dirs):
    assert embed.cached_ids("effnet") == set()


def test_ensure_embeddings_fetches_only_missing(vibe_dirs, monkeypatch):
    _seed_cache(vibe_dirs, "effnet", ["a1", "b1"])
    fetched, embedded = [], []

    def fake_fetch(tracks, workers=3, max_new=None):
        fetched.extend(t["videoId"] for t in tracks)
        return {t["videoId"]: f"/audio/{t['videoId']}.m4a" for t in tracks}

    def fake_embed(paths, backend="effnet", prune_audio=False):
        embedded.extend(paths)
        _seed_cache(vibe_dirs, backend, ["a1", "b1", *paths])
        return {}

    monkeypatch.setattr(train.audio, "fetch_many", fake_fetch)
    monkeypatch.setattr(train.embed, "embed_tracks", fake_embed)

    still = train.ensure_embeddings(_lib(), "effnet", exclude=(), min_tracks=1)

    assert sorted(fetched) == ["a2", "b2"]
    assert sorted(embedded) == ["a2", "b2"]
    assert still == 0


def test_ensure_embeddings_reports_leftovers_when_fetch_fails(vibe_dirs, monkeypatch):
    monkeypatch.setattr(train.audio, "fetch_many", lambda tracks, **k: {})
    called = []
    monkeypatch.setattr(train.embed, "embed_tracks",
                        lambda *a, **k: called.append(1))

    still = train.ensure_embeddings(_lib(), "effnet", exclude=(), min_tracks=1)

    assert still == 4
    assert not called  # nothing to embed when nothing was fetched


def test_ensure_embeddings_noop_when_all_cached(vibe_dirs, monkeypatch):
    _seed_cache(vibe_dirs, "effnet", ["a1", "a2", "b1", "b2"])
    monkeypatch.setattr(train.audio, "fetch_many",
                        lambda *a, **k: pytest.fail("must not fetch"))
    assert train.ensure_embeddings(_lib(), "effnet", exclude=(), min_tracks=1) == 0


def test_embed_tracks_skips_cached_without_opening_audio(vibe_dirs, monkeypatch):
    _seed_cache(vibe_dirs, "effnet", ["n1"])
    monkeypatch.setitem(embed.BACKENDS, "effnet",
                        lambda path: pytest.fail(f"opened {path}"))

    vectors = embed.embed_tracks({"n1": None}, backend="effnet")

    assert set(vectors) == {"n1"}


def test_route_fetches_audio_only_for_unembedded(vibe_dirs, monkeypatch, capsys):
    """Dry run through route.main: n1 is embedded, n2 is not."""
    import joblib, json
    _seed_cache(vibe_dirs, "effnet", ["n1"])

    joblib.dump({"pipeline": _ConfidentPipe(), "classes": ["ONE", "TWO"],
                 "backend": "effnet", "trained_at": "t", "n_train": 4,
                 "top1": 1.0, "top3": 1.0}, vibe_dirs / "model.joblib")
    (vibe_dirs / "thresholds.json").write_text(json.dumps(
        {"thresholds": {"ONE": {"threshold": 0.8}, "TWO": {"threshold": None}}}))

    monkeypatch.setattr(route.library, "load_library", lambda refresh=False: _lib())
    fetched = []

    def fake_fetch(tracks, workers=3, max_new=None):
        fetched.extend(t["videoId"] for t in tracks)
        return {}

    monkeypatch.setattr(route.audio, "fetch_many", fake_fetch)
    monkeypatch.setattr(route.embed, "embed_tracks",
                        lambda paths, **k: {v: np.ones(4) for v in paths if v == "n1"})
    monkeypatch.setattr("sys.argv", ["vibe_route.py", "--no-refresh-library"])

    route.main()

    assert fetched == ["n2"]
    out = capsys.readouterr().out
    assert "1 already embedded, 1 need audio" in out
    assert "1 confident placement(s)" in out


def test_route_keeps_queue_when_placement_auth_fails(vibe_dirs, monkeypatch, capsys):
    """Cookies dying between embedding and placement must not throw away the
    review queue: it is written first, and the confident placements join it."""
    import joblib, json
    from vibe import library
    _seed_cache(vibe_dirs, "effnet", ["n1", "n2"])
    joblib.dump({"pipeline": _ConfidentPipe(), "classes": ["ONE", "TWO"],
                 "backend": "effnet", "trained_at": "t", "n_train": 4,
                 "top1": 1.0, "top3": 1.0}, vibe_dirs / "model.joblib")
    (vibe_dirs / "thresholds.json").write_text(json.dumps(
        {"thresholds": {"ONE": {"threshold": 0.8}, "TWO": {"threshold": None}}}))
    monkeypatch.setattr(route.library, "load_library", lambda refresh=False: _lib())
    monkeypatch.setattr(route.audio, "fetch_many", lambda tracks, **k: {})
    monkeypatch.setattr(route.embed, "embed_tracks",
                        lambda paths, **k: {v: np.ones(4) for v in paths})

    def dead_session(placements, lib):
        raise library.AuthError("session rejected")
    monkeypatch.setattr(route, "place", dead_session)
    monkeypatch.setattr("sys.argv", ["vibe_route.py", "--no-refresh-library", "--execute"])

    with pytest.raises(SystemExit) as exc:
        route.main()

    assert exc.value.code == 1
    queue = json.loads((vibe_dirs / "sort_queue.json").read_text())
    assert sorted(t["videoId"] for t in queue["tracks"]) == ["n1", "n2"]
    assert "in the review queue instead" in capsys.readouterr().out
