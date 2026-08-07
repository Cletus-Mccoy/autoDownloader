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


def test_write_cookies_file_produces_netscape_format(tmp_path):
    path = str(tmp_path / "cookies.txt")
    result = dl.write_cookies_file("SID=abc; __Secure-1PSID=def; YSC=xyz", path)
    assert result == path
    content = open(path).read()
    assert content.startswith("# Netscape HTTP Cookie File")
    lines = [l for l in content.splitlines() if l and not l.startswith("#")]
    assert len(lines) == 3
    for line in lines:
        fields = line.split("\t")
        assert len(fields) == 7
        assert fields[0] == ".youtube.com"
        assert fields[1] == "TRUE"
        assert fields[2] == "/"
    # SID and __Secure-1PSID must be marked Secure; YSC must not.
    by_name = {l.split("\t")[5]: l.split("\t")[3] for l in lines}
    assert by_name["SID"] == "TRUE"
    assert by_name["__Secure-1PSID"] == "TRUE"
    assert by_name["YSC"] == "FALSE"


def test_write_cookies_file_skips_host_prefix(tmp_path):
    """__Host- cookies can't be scoped to .youtube.com without breaking the
    prefix contract, so they must be omitted from the file."""
    path = str(tmp_path / "cookies.txt")
    dl.write_cookies_file("__Host-foo=bar; SID=ok", path)
    content = open(path).read()
    assert "__Host-foo" not in content
    assert "SID\tok" in content


def test_write_cookies_file_empty_input(tmp_path):
    assert dl.write_cookies_file("", str(tmp_path / "cookies.txt")) is None


def test_download_uses_cookies_flag_not_add_header(tmp_path, monkeypatch):
    """yt-dlp deprecated `--add-header Cookie:` — we must pass `--cookies FILE`."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    cookies_target = tmp_path / "cookies.txt"
    monkeypatch.setattr(dl, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(dl, "COOKIES_FILE", str(cookies_target))
    monkeypatch.setattr(dl, "get_cookie_header", lambda: "SID=abc; SAPISID=def")
    monkeypatch.setattr(subprocess, "run", fake_run)

    dl.download_playlist({"title": "Test", "url": "https://example.com", "count": 1})

    cmd = captured["cmd"]
    assert "--cookies" in cmd
    cookies_arg = cmd[cmd.index("--cookies") + 1]
    assert cookies_arg == str(cookies_target)
    assert cookies_target.exists()
    # Regression guard: the deprecated header form must not sneak back in.
    for i, arg in enumerate(cmd):
        if arg == "--add-header":
            assert not cmd[i + 1].lower().startswith("cookie:"), (
                "must not pass Cookie via --add-header (deprecated by yt-dlp)"
            )
