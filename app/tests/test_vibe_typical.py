"""Medoid ranking per playlist, and the endpoint that serves it."""
import json
import numpy as np

from vibe.typical import typical_tracks


def _tracks(*ids):
    return [{"videoId": v, "title": v, "artist": "a"} for v in ids]


def test_typical_ranks_by_similarity_to_centroid():
    lib = {"playlists": [
        {"title": "ONE", "tracks": _tracks("a", "b", "c", "x")},
        {"title": "TINY", "tracks": _tracks("solo")},
    ]}
    # The outlier sits on its own axis so it can't tilt the centroid toward
    # "a" or "c"; "b" is then the exact medoid.
    vectors = {"a": [1, 0.1, 0], "b": [1, 0, 0], "c": [1, -0.1, 0],
               "x": [0, 0, 1],        # the odd one out
               "solo": [1, 1, 0]}
    out = typical_tracks(lib, vectors, n=2)

    assert set(out) == {"ONE"}               # TINY has one track, skipped
    one = out["ONE"]
    assert one["count"] == 4
    assert one["typical"][0]["videoId"] == "b"
    assert one["atypical"][0]["videoId"] == "x"
    assert 0 < one["cohesion"] < 1
    assert len(one["typical"]) == 2 and len(one["atypical"]) == 2


def test_typical_ignores_multi_playlist_and_excluded():
    lib = {"playlists": [
        {"title": "ONE", "tracks": _tracks("a", "b", "shared")},
        {"title": "TWO", "tracks": _tracks("c", "d", "shared")},
        {"title": "2025 Recap", "tracks": _tracks("a", "b", "c", "d")},
    ]}
    vectors = {v: [1, 0] for v in "abcd"} | {"shared": [0, 1]}
    out = typical_tracks(lib, vectors, exclude=("recap",))
    assert set(out) == {"ONE", "TWO"}
    assert all(t["videoId"] != "shared"
               for info in out.values() for t in info["typical"] + info["atypical"])


def test_api_sort_typical_serves_medoids_with_preview_flag(client, flask_app, tmp_path):
    import app as flask_module
    vibe = flask_module.VIBE_DIR
    (vibe_dir := __import__("pathlib").Path(vibe)).mkdir(exist_ok=True)
    (vibe_dir / "embeddings").mkdir(exist_ok=True)
    np.savez_compressed(vibe_dir / "embeddings" / "effnet.npz",
                        a=np.array([1, 0.1, 0]), b=np.array([1, 0, 0]),
                        x=np.array([0, 0, 1]))
    (vibe_dir / "audio" / "b.m4a").write_bytes(b"\x00")
    json.dump({"playlists": [{"title": "ONE", "id": "p1",
                              "tracks": _tracks("a", "b", "x")}], "liked": []},
              open(flask_module.VIBE_LIBRARY_FILE, "w"))
    json.dump({"backend": "effnet", "thresholds": {}},
              open(f"{vibe}/thresholds.json", "w"))

    r = client.get("/api/sort/typical")
    assert r.status_code == 200
    one = r.get_json()["playlists"]["ONE"]
    assert one["typical"][0]["videoId"] == "b"
    assert one["typical"][0]["has_preview"] is True
    assert one["atypical"][0]["videoId"] == "x"
    assert one["atypical"][0]["has_preview"] is False


def test_api_sort_typical_empty_without_embeddings(client):
    r = client.get("/api/sort/typical")
    assert r.status_code == 200
    assert r.get_json()["playlists"] == {}
