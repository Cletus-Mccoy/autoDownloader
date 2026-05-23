"""Unit tests for download.py utility functions."""
import json
import subprocess
import download as dl


def test_get_cookie_header_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "HEADERS_AUTH_FILE", str(tmp_path / "nonexistent.json"))
    assert dl.get_cookie_header() is None


def test_get_cookie_header_capitalised_key(tmp_path, monkeypatch):
    auth_file = tmp_path / "headers_auth.json"
    auth_file.write_text(json.dumps({"Cookie": "foo=bar; baz=qux"}))
    monkeypatch.setattr(dl, "HEADERS_AUTH_FILE", str(auth_file))
    assert dl.get_cookie_header() == "foo=bar; baz=qux"


def test_get_cookie_header_lowercase_key(tmp_path, monkeypatch):
    auth_file = tmp_path / "headers_auth.json"
    auth_file.write_text(json.dumps({"cookie": "foo=bar"}))
    monkeypatch.setattr(dl, "HEADERS_AUTH_FILE", str(auth_file))
    assert dl.get_cookie_header() == "foo=bar"


def test_get_cookie_header_invalid_json(tmp_path, monkeypatch):
    auth_file = tmp_path / "headers_auth.json"
    auth_file.write_text("not json {{")
    monkeypatch.setattr(dl, "HEADERS_AUTH_FILE", str(auth_file))
    assert dl.get_cookie_header() is None


def test_get_cookie_header_no_cookie_key(tmp_path, monkeypatch):
    auth_file = tmp_path / "headers_auth.json"
    auth_file.write_text(json.dumps({"User-Agent": "Mozilla/5.0"}))
    monkeypatch.setattr(dl, "HEADERS_AUTH_FILE", str(auth_file))
    assert dl.get_cookie_header() is None


def test_postprocessor_args_scoped_to_extract_audio(tmp_path, monkeypatch):
    """--postprocessor-args must target ExtractAudio, not ffmpeg, so that
    metadata/thumbnail embedding steps don't re-encode and strip tags."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(dl, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(dl, "get_cookie_header", lambda: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    dl.download_playlist({"title": "Test", "url": "https://example.com", "count": 1})

    cmd = captured["cmd"]
    pp_index = cmd.index("--postprocessor-args")
    pp_value = cmd[pp_index + 1]

    assert pp_value.startswith("ExtractAudio:"), (
        f"postprocessor-args must be scoped to 'ExtractAudio:' to avoid "
        f"re-encoding during metadata/thumbnail embedding, got: {pp_value!r}"
    )
