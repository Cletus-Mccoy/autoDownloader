"""The misfiled-track review queue: proposals served minus decided ones, a
move that adds-then-removes on YouTube, and a keep that hides the track."""
import csv
import json
import os

import pytest

from vibe import moves


class FakeYT:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def add_playlist_items(self, pid, ids, duplicates=False):
        if self.fail:
            raise RuntimeError("session rejected")
        self.calls.append(("add", pid, tuple(ids)))

    def remove_playlist_items(self, pid, items):
        self.calls.append(("remove", pid, tuple((i["videoId"], i["setVideoId"]) for i in items)))


def _lib():
    return {"playlists": [
        {"id": "p240", "title": "28. 240 SOUND",
         "tracks": [{"videoId": "v1", "setVideoId": "s1", "title": "T1", "artist": "A"},
                    {"videoId": "v2", "setVideoId": "s2", "title": "T2", "artist": "B"}]},
        {"id": "pb", "title": "28. BOUNCE", "tracks": []},
    ], "liked": []}


def test_move_track_adds_then_removes():
    yt = FakeYT()
    moves.move_track(yt, _lib(), "v1", "28. 240 SOUND", "28. BOUNCE")
    assert yt.calls == [("add", "pb", ("v1",)), ("remove", "p240", (("v1", "s1"),))]


def test_move_track_refuses_without_placement_id():
    lib = _lib(); lib["playlists"][0]["tracks"][0]["setVideoId"] = None
    with pytest.raises(ValueError):
        moves.move_track(FakeYT(), lib, "v1", "28. 240 SOUND", "28. BOUNCE")


def test_relocate_in_library_moves_membership():
    lib = _lib()
    assert moves.relocate_in_library(lib, "v1", "28. 240 SOUND", "28. BOUNCE")
    assert [t["videoId"] for t in lib["playlists"][0]["tracks"]] == ["v2"]
    assert lib["playlists"][1]["tracks"][0]["videoId"] == "v1"
    assert lib["playlists"][1]["tracks"][0]["setVideoId"] is None


@pytest.fixture
def remodel_env(flask_app, tmp_path, monkeypatch):
    import app as flask_module
    vibe = flask_module.VIBE_DIR
    os.makedirs(f"{vibe}/reports", exist_ok=True)
    os.makedirs(f"{vibe}/audio", exist_ok=True)
    review = f"{vibe}/reports/remodel_review.csv"
    monkeypatch.setattr(flask_module, "REMODEL_REVIEW_FILE", review)
    monkeypatch.setattr(flask_module, "REMODEL_DECISIONS_FILE", f"{vibe}/remodel_decisions.json")
    monkeypatch.setattr(flask_module, "MOVES_LEDGER_FILE", f"{vibe}/reports/moves.jsonl")
    with open(review, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["videoId", "artist", "title", "current", "p_current",
                                          "suggested", "p_suggested", "runner_up", "p_runner_up", "action"])
        w.writeheader()
        w.writerow({"videoId": "v1", "artist": "A", "title": "T1", "current": "28. 240 SOUND",
                    "p_current": "0.05", "suggested": "28. BOUNCE", "p_suggested": "0.73",
                    "runner_up": "29. HARD TECH", "p_runner_up": "0.09", "action": ""})
        w.writerow({"videoId": "v2", "artist": "B", "title": "T2", "current": "28. 240 SOUND",
                    "p_current": "0.02", "suggested": "28. BOUNCE", "p_suggested": "0.65",
                    "runner_up": "", "p_runner_up": "0", "action": ""})
    open(f"{vibe}/audio/v1.m4a", "wb").write(b"\x00")
    json.dump(_lib(), open(flask_module.VIBE_LIBRARY_FILE, "w"))
    json.dump({"backend": "effnet", "thresholds": {}, "exclude": ["recap"]},
              open(f"{vibe}/thresholds.json", "w"))
    return flask_module


def test_remodel_queue_lists_proposals(client, remodel_env):
    d = client.get("/api/sort/remodel").get_json()
    assert [t["videoId"] for t in d["tracks"]] == ["v1", "v2"]
    assert d["tracks"][0]["has_preview"] is True
    assert d["tracks"][1]["has_preview"] is False
    assert d["tracks"][1]["runner_up"] is None
    assert "28. BOUNCE" in d["playlists"]


def test_remodel_move_updates_youtube_ledger_and_library(client, remodel_env, monkeypatch):
    yt = FakeYT()
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: yt)

    r = client.post("/api/sort/remodel/move", json={
        "videoId": "v1", "current": "28. 240 SOUND", "target": "28. BOUNCE",
        "title": "T1", "artist": "A"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert yt.calls[0][0] == "add" and yt.calls[1][0] == "remove"

    lib = json.load(open(remodel_env.VIBE_LIBRARY_FILE))
    assert [t["videoId"] for t in lib["playlists"][0]["tracks"]] == ["v2"]
    ledger = [json.loads(l) for l in open(remodel_env.MOVES_LEDGER_FILE)]
    assert ledger[0]["videoId"] == "v1" and ledger[0]["target"] == "28. BOUNCE"
    decisions = [json.loads(l) for l in open(remodel_env.DECISIONS_FILE)]
    assert decisions[-1]["kind"] == "remodel"

    assert [t["videoId"] for t in client.get("/api/sort/remodel").get_json()["tracks"]] == ["v2"]


def test_remodel_move_failure_leaves_everything(client, remodel_env, monkeypatch):
    import scripts.ytmusic_auth as auth
    monkeypatch.setattr(auth, "headers_to_ytmusic", lambda: FakeYT(fail=True))
    r = client.post("/api/sort/remodel/move", json={
        "videoId": "v1", "current": "28. 240 SOUND", "target": "28. BOUNCE"})
    assert r.status_code == 500
    assert "rejected" in r.get_json()["error"]
    assert not os.path.exists(remodel_env.MOVES_LEDGER_FILE)
    assert len(client.get("/api/sort/remodel").get_json()["tracks"]) == 2


def test_remodel_move_rejects_bad_target(client, remodel_env):
    r = client.post("/api/sort/remodel/move", json={
        "videoId": "v1", "current": "28. 240 SOUND", "target": "28. 240 SOUND"})
    assert r.status_code == 400
    r = client.post("/api/sort/remodel/move", json={
        "videoId": "v1", "current": "28. 240 SOUND", "target": "NOPE"})
    assert r.status_code == 400


def test_remodel_keep_hides_while_in_that_playlist(client, remodel_env):
    r = client.post("/api/sort/remodel/keep", json={"videoId": "v2", "current": "28. 240 SOUND"})
    assert r.get_json()["ok"]
    assert [t["videoId"] for t in client.get("/api/sort/remodel").get_json()["tracks"]] == ["v1"]
    decisions = [json.loads(l) for l in open(remodel_env.DECISIONS_FILE)]
    assert decisions[-1]["kind"] == "remodel_keep"
    stats = client.get("/api/sort/stats").get_json()
    assert stats["activity"]["kept"] == 1


def test_remodel_queue_empty_without_plan(client, flask_app):
    d = client.get("/api/sort/remodel").get_json()
    assert d["tracks"] == [] and d["generated_at"] is None


def test_scheduler_runs_remodel_plan(monkeypatch, tmp_path):
    import sys, subprocess
    import scheduler
    monkeypatch.setattr(scheduler, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler, "RUNS_FILE", str(tmp_path / "runs.json"))
    seen = []
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **k: seen.append(argv) or subprocess.CompletedProcess(argv, 0))
    scheduler.run_downloader()
    scripts = [argv[1] for argv in seen]
    assert scripts.index("/app/scripts/vibe_remodel.py") > scripts.index("/app/scripts/vibe_route.py")
    assert scripts[-1] == "/app/scripts/download.py"
