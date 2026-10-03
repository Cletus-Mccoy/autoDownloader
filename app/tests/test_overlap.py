"""Playlist overlap geometry, and the endpoints that serve it."""
import json
import pathlib

import numpy as np

from vibe import overlap


def _tracks(prefix, n):
    return [{"videoId": f"{prefix}{i}", "title": f"{prefix}{i}", "artist": "x"}
            for i in range(n)]


def _cloud(rng, axis, n, spread=0.05, dim=8):
    base = np.zeros(dim)
    base[axis] = 1.0
    return base + rng.normal(0, spread, size=(n, dim))


def _library(rng):
    """A and B share a sound (axis 0); C is elsewhere; D holds two sounds."""
    vectors, playlists = {}, []
    for title, prefix, axis in (("A", "a", 0), ("B", "b", 0), ("C", "c", 3)):
        tracks = _tracks(prefix, 25)
        for t, v in zip(tracks, _cloud(rng, axis, 25)):
            vectors[t["videoId"]] = v
        playlists.append({"title": title, "id": title, "tracks": tracks})
    d = _tracks("d", 40)
    for t, v in zip(d, np.vstack([_cloud(rng, 5, 20), _cloud(rng, 6, 20)])):
        vectors[t["videoId"]] = v
    playlists.append({"title": "D", "id": "D", "tracks": d})
    return {"playlists": playlists, "liked": []}, vectors


def test_overlapping_playlists_are_adjacent_and_flagged_entangled():
    lib, vectors = _library(np.random.default_rng(0))
    out = overlap.analyse(lib, vectors)

    order = out["playlists"]
    assert abs(order.index("A") - order.index("B")) == 1
    top = out["pairs"][0]
    assert {top["a"], top["b"]} == {"A", "B"}
    assert top["verdict"] in ("interchangeable", "entangled")
    # C shares nothing with the others.
    c = out["matrix"]["C"]
    assert c.get("C", 0) == 1.0
    assert out["stats"]["C"]["bleed"] == 0
    assert out["stats"]["A"]["bleed"] > 0.2


def test_a_track_in_the_wrong_playlist_is_flagged_misfiled():
    rng = np.random.default_rng(1)
    lib, vectors = _library(rng)
    # An axis-3 track filed under A: it sounds like C.
    lib["playlists"][0]["tracks"].append({"videoId": "stray", "title": "stray",
                                           "artist": "x"})
    vectors["stray"] = _cloud(rng, 3, 1)[0]
    out = overlap.analyse(lib, vectors)

    stray = next(t for t in out["tracks"] if t["videoId"] == "stray")
    assert stray["kind"] == "misfiled"
    assert stray["current"] == "A" and stray["other"] == "C"
    assert stray["margin"] < 0


def test_own_centroid_is_computed_without_the_track():
    """Two-track playlists would look perfect if the track voted for itself."""
    vectors = {"a1": [1, 0], "a2": [1, 0], "a3": [1, 0], "a4": [1, 0],
               "a5": [1, 0], "odd": [0, 1],
               "b1": [0, 1], "b2": [0, 1], "b3": [0, 1], "b4": [0, 1],
               "b5": [0, 1]}
    lib = {"playlists": [
        {"title": "A", "tracks": _tracks("a", 0) + [
            {"videoId": v, "title": v} for v in ("a1", "a2", "a3", "a4", "a5", "odd")]},
        {"title": "B", "tracks": [{"videoId": v, "title": v}
                                  for v in ("b1", "b2", "b3", "b4", "b5")]},
    ]}
    out = overlap.analyse(lib, vectors)
    odd = next(t for t in out["tracks"] if t["videoId"] == "odd")
    assert odd["kind"] == "misfiled" and odd["other"] == "B"


def test_split_summary_ranks_the_two_sound_playlist_first():
    lib, vectors = _library(np.random.default_rng(2))
    splits = overlap.split_summary(lib, vectors)
    assert max(splits, key=lambda k: splits[k]["score"]) == "D"
    assert splits["D"]["balance"] > 0.4
    assert splits["D"]["score"] > 5 * splits["C"]["score"]


def test_split_detail_has_plot_points_and_cluster_examples():
    lib, vectors = _library(np.random.default_rng(3))
    d = overlap.split_detail(lib, vectors, "D")
    assert len(d["points"]) == 40
    assert {c["size"] for c in d["clusters"]} == {20}
    assert all(len(c["examples"]) == 4 for c in d["clusters"])
    assert overlap.split_detail(lib, vectors, "nope") is None


def test_too_few_playlists_gives_none():
    lib = {"playlists": [{"title": "A", "tracks": _tracks("a", 6)}]}
    vectors = {t["videoId"]: [1, 0] for t in lib["playlists"][0]["tracks"]}
    assert overlap.analyse(lib, vectors) is None


def test_overlap_endpoints(client, flask_app):
    import app as flask_module
    vibe = pathlib.Path(flask_module.VIBE_DIR)
    (vibe / "embeddings").mkdir(exist_ok=True)

    empty = client.get("/api/sort/overlap").get_json()
    assert empty["playlists"] == [] and empty["tracks"] == []

    lib, vectors = _library(np.random.default_rng(4))
    np.savez_compressed(vibe / "embeddings" / "effnet.npz", **vectors)
    (vibe / "audio" / "a0.m4a").write_bytes(b"\x00")
    json.dump(lib, open(flask_module.VIBE_LIBRARY_FILE, "w"))
    json.dump({"backend": "effnet", "thresholds": {}},
              open(f"{flask_module.VIBE_DIR}/thresholds.json", "w"))

    r = client.get("/api/sort/overlap")
    body = r.get_data(as_text=True)
    assert "NaN" not in body
    d = json.loads(body)
    assert set(d["playlists"]) == {"A", "B", "C", "D"}
    assert d["splits"]["D"]["score"] > d["splits"]["C"]["score"]
    assert all("has_preview" in t for t in d["tracks"])

    s = client.get("/api/sort/split", query_string={"playlist": "D"}).get_json()
    assert len(s["points"]) == 40
    assert client.get("/api/sort/split", query_string={"playlist": "zz"}).status_code == 404


def test_stats_reports_the_nightly_cap(client, flask_app, monkeypatch):
    monkeypatch.setenv("VIBE_MAX_NEW_AUDIO", "25")
    assert client.get("/api/sort/stats").get_json()["pipeline"]["nightly_cap"] == 25
    monkeypatch.setenv("VIBE_MAX_NEW_AUDIO", "junk")
    assert client.get("/api/sort/stats").get_json()["pipeline"]["nightly_cap"] == 50
