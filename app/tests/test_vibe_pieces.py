"""The piece model: the 9 loudest pieces of the whole track, a piece-level
classifier, the artist vote, and the rule for when it replaces the
one-vector model."""
import os

import numpy as np
import pytest

from vibe import config, model, pieces, route, train

SR = 1000  # a small rate keeps the arrays tiny; select() takes sr explicitly


def test_select_keeps_the_loudest_pieces_in_time_order():
    # 100 s: a quiet first half (the intro), a loud second half that rises.
    audio = np.full(100 * SR, 0.01, dtype=np.float32)
    audio[50 * SR:] = np.linspace(0.5, 0.9, 50 * SR)
    got = pieces.select(audio, sr=SR, pieces=3, seconds=5)
    assert len(got) == 3 and all(len(p) == 5 * SR for p in got)
    assert all(p.mean() > 0.4 for p in got), "a quiet window was kept"
    assert [p.mean() for p in got] == sorted(p.mean() for p in got), "not in time order"


def test_select_short_track_is_one_piece():
    got = pieces.select(np.ones(3 * SR, dtype=np.float32), sr=SR, pieces=9, seconds=10)
    assert len(got) == 1


@pytest.fixture
def piece_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EMBED_DIR", str(tmp_path / "emb"))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_ensure_uses_downloads_first_caps_and_cleans_up(piece_cache):
    fetched = piece_cache / "f.m4a"
    tracks = [{"videoId": v} for v in ("dl1", "new1", "dl2", "new2")]
    sources = {"dl1": "a.mp3", "dl2": "b.mp3"}
    embedded = []

    def fake_embed(path):
        embedded.append(os.path.basename(path))
        return np.ones((9, 4), dtype=np.float32)

    def fake_fetch(vid, cookies):
        fetched.write_bytes(b"x")
        return str(fetched)

    got = pieces.ensure(tracks, max_new=3, sources=sources,
                        embed_fn=fake_embed, fetch_fn=fake_fetch)
    assert set(got) == {"dl1", "dl2", "new1"}          # downloads first, then the cap
    assert embedded[:2] == ["a.mp3", "b.mp3"]
    assert not fetched.exists(), "a fetched full track must be deleted after embedding"
    # the next run picks up the deferred one and re-embeds nothing
    embedded.clear()
    got = pieces.ensure(tracks, sources=sources, embed_fn=fake_embed, fetch_fn=fake_fetch)
    assert set(got) == {"dl1", "dl2", "new1", "new2"} and len(embedded) == 1


def _toy(n_per=24, seed=0):
    """Three playlists, separable in audio; 'shared' artist sits in both a and b."""
    rng = np.random.default_rng(seed)
    centres = {"a": np.r_[1, 0, 0, 0.], "b": np.r_[0, 1, 0, 0.], "c": np.r_[0, 0, 1, 0.]}
    tracks, y = [], []
    for label, c in centres.items():
        for i in range(n_per):
            tracks.append({"videoId": f"{label}{i}", "artist": f"{label}-artist{i % 4}"})
            y.append(label)
    vectors = {t["videoId"]: centres[l] + rng.normal(0, 0.35, (9, 4))
               for t, l in zip(tracks, y)}
    return tracks, np.array(y), vectors


def test_piece_model_learns_and_the_artist_vote_breaks_a_tie():
    tracks, y, vectors = _toy()
    X = model.track_inputs(vectors, tracks)
    m = model.PieceModel().fit(X, y)
    assert (m.predict(X) == y).mean() > 0.9

    # A track that sounds exactly between a and b: the artist decides.
    between = {"videoId": "t", "artist": "b-artist1 & someone new"}
    Xt = model.track_inputs({"t": np.tile([0.5, 0.5, 0, 0], (9, 1))}, [between])
    p = dict(zip(m.classes_, m.predict_proba(Xt)[0]))
    assert p["b"] > p["a"]
    no_vote = model.PieceModel(artist_weight=0).fit(X, y)
    q = dict(zip(no_vote.classes_, no_vote.predict_proba(Xt)[0]))
    assert p["b"] - p["a"] > q["b"] - q["a"]


def test_artists_split_on_collaborations():
    assert model.artists_of({"artist": "A & B feat. C, D"}) == {"a", "b", "c", "d"}
    assert model.artists_of({"artist": None}) == frozenset()


def test_piece_model_runs_through_the_nested_gate():
    tracks, y, vectors = _toy()
    X = model.track_inputs(vectors, tracks)
    sizes = {"a": 24, "b": 24, "c": 24}
    result = train.evaluate(X, y, sizes, 0.9, nested=True, build=model.piece_build)
    assert result["top1"] > 0.9 and result["auto_share"] > 0
    assert result["honest_precision"] is not None
    assert isinstance(result["fitted"], model.PieceModel)


@pytest.fixture
def toy_training(monkeypatch):
    """train.train on toy data: the one-vector model hears only a blurred
    mean, the piece model the pieces and the artists."""
    tracks, y, vectors = _toy(seed=1)
    rows = [{**t, "label": l} for t, l in zip(tracks, y)]
    rng = np.random.default_rng(2)
    X1 = np.vstack([vectors[t["videoId"]].mean(0) + rng.normal(0, 0.6, 4) for t in tracks])
    sizes = {"a": 24, "b": 24, "c": 24}
    monkeypatch.setattr(train.library, "load_library", lambda refresh=False: {})
    monkeypatch.setattr(train, "ensure_embeddings", lambda *a, **k: 0)
    monkeypatch.setattr(train.sweep, "load_cached", lambda *a, **k: (X1, y, {}, sizes))
    monkeypatch.setattr(train.sweep, "load_cached_rows", lambda *a, **k: rows)
    covered = {"n": len(rows)}
    monkeypatch.setattr(pieces, "ensure", lambda rs, max_new=None: {
        r["videoId"]: vectors[r["videoId"]] for r in rs[:covered["n"]]})
    return covered


def test_train_switches_to_the_piece_model_when_it_places_more(toy_training):
    *_, meta = train.train("effnet", [], 0.9, nested=True, pieces_enabled=True)
    assert set(meta["candidates"]) == {"effnet", pieces.BACKEND}
    assert meta["vectors_backend"] == "effnet"
    assert meta["backend"] == pieces.BACKEND
    assert meta["candidates"][pieces.BACKEND]["auto_share"] > meta["candidates"]["effnet"]["auto_share"]


def test_train_keeps_the_one_vector_model_while_pieces_backfill(toy_training):
    toy_training["n"] = 40                      # 40 of 72 tracks: under the bar
    *_, meta = train.train("effnet", [], 0.9, nested=True, pieces_enabled=True)
    assert meta["backend"] == "effnet" and set(meta["candidates"]) == {"effnet"}


def test_the_piece_model_must_place_more_without_losing_precision():
    current = {"auto_share": 0.19, "honest_precision": 0.89}
    assert train.better({"auto_share": 0.23, "honest_precision": 0.89}, current, 0.9, 0.05)
    assert not train.better({"auto_share": 0.18, "honest_precision": 0.95}, current, 0.9, 0.05)
    assert not train.better({"auto_share": 0.30, "honest_precision": 0.80}, current, 0.9, 0.05)
    assert not train.better({"auto_share": 0.30, "honest_precision": None}, current, 0.9, 0.05)


def test_route_decides_with_the_piece_model():
    tracks, y, vectors = _toy()
    fitted = model.PieceModel().fit(model.track_inputs(vectors, tracks), y)
    bundle = {"pipeline": fitted, "classes": list(fitted.classes_),
              "backend": route.PIECES_BACKEND}
    pending = [{"videoId": "n1", "artist": "x", "title": "t"}]
    vec = {"n1": np.tile([0, 0, 1.0, 0], (9, 1))}
    placements, queued = route.decide(vec, pending, bundle,
                                      {"c": {"threshold": 0.5}}, shortlist=3)
    assert [p["playlist"] for p in placements] == ["c"] and not queued
