"""Unavailable videos must not eat the nightly fetch cap night after night."""
import datetime
import json

from vibe import audio, config


def _t(v):
    return {"videoId": v, "title": v, "artist": "a", "duration_seconds": 200}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(config, "EMBED_DIR", str(tmp_path / "emb"))
    monkeypatch.setattr(config, "REPORT_DIR", str(tmp_path / "rep"))
    config.ensure_dirs()
    monkeypatch.setattr(audio, "yt_dlp_binary", lambda: "/bin/true")
    monkeypatch.setattr(audio.subprocess, "run",
                        lambda *a, **k: audio.subprocess.CompletedProcess(a, 0, stdout="2026.08.19"))
    monkeypatch.setattr(audio, "_prepare_cookies", lambda: None)


def test_failed_fetch_is_marked_and_skipped_next_run(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    outcomes = {"ok": str(tmp_path / "audio" / "ok.m4a"), "dead": None}
    monkeypatch.setattr(audio, "fetch_one", lambda t, c=None: outcomes[t["videoId"]])

    got = audio.fetch_many([_t("ok"), _t("dead")], workers=1)
    assert set(got) == {"ok"}
    marks = json.load(open(tmp_path / "unavailable.json"))
    assert "dead" in marks and marks["dead"]["retry_after"] > datetime.datetime.utcnow().isoformat()

    calls = []
    monkeypatch.setattr(audio, "fetch_one", lambda t, c=None: calls.append(t["videoId"]) or None)
    audio.fetch_many([_t("dead"), _t("new")], workers=1)
    assert calls == ["new"]           # "dead" skipped, "new" attempted


def test_total_failure_is_not_blacklisted(tmp_path, monkeypatch):
    """Every fetch failing means the run is broken, not that 50 videos died."""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(audio, "fetch_one", lambda t, c=None: None)
    audio.fetch_many([_t("a"), _t("b")], workers=1)
    assert not (tmp_path / "unavailable.json").exists()


def test_retry_after_expiry_is_attempted_again(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    past = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()
    json.dump({"old": {"failed_at": past, "retry_after": past}}, open(tmp_path / "unavailable.json", "w"))
    calls = []
    monkeypatch.setattr(audio, "fetch_one", lambda t, c=None: calls.append(t["videoId"]) or None)
    audio.fetch_many([_t("old"), _t("x")], workers=1)
    assert calls == ["old", "x"]
