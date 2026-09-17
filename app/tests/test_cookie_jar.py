"""One session, one jar. yt-dlp rotates tokens into cookies.txt; ytmusicapi
must present those, not the paste they superseded."""
import json
import os
import time

import ytmusic_auth
import download as dl


def _setup(tmp_path, monkeypatch, header="SID=sid0; __Secure-1PSIDTS=ts0; PREF=x"):
    headers = tmp_path / "headers_auth.json"
    jar = tmp_path / "cookies.txt"
    headers.write_text(json.dumps({"cookie": header, "user-agent": "ua"}))
    monkeypatch.setattr(ytmusic_auth, "HEADERS_AUTH_FILE", str(headers))
    monkeypatch.setattr(ytmusic_auth, "COOKIES_FILE", str(jar))
    monkeypatch.setattr(dl, "COOKIES_FILE", str(jar))
    monkeypatch.setattr(dl, "HEADERS_AUTH_FILE", str(headers))
    return headers, jar


def _write_jar(jar, values, httponly=("SID",)):
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in values.items():
        prefix = "#HttpOnly_" if name in httponly else ""
        lines.append(f"{prefix}.youtube.com\tTRUE\t/\tTRUE\t9999999999\t{name}\t{value}")
    jar.write_text("\n".join(lines) + "\n")


def _bump(path, seconds):
    t = time.time() + seconds
    os.utime(path, (t, t))


def test_read_jar_keeps_httponly_lines(tmp_path):
    jar = tmp_path / "c.txt"
    _write_jar(jar, {"SID": "s", "PREF": "p"})
    assert ytmusic_auth.read_cookie_jar(str(jar)) == {"SID": "s", "PREF": "p"}


def test_load_headers_folds_in_rotated_jar_and_writes_back(tmp_path, monkeypatch):
    headers, jar = _setup(tmp_path, monkeypatch)
    _write_jar(jar, {"SID": "sid0", "__Secure-1PSIDTS": "ts1", "EXTRA": "ignored"})
    _bump(jar, 60)

    h = ytmusic_auth.load_headers()

    assert h["cookie"] == "SID=sid0; __Secure-1PSIDTS=ts1; PREF=x"
    assert json.loads(headers.read_text())["cookie"] == h["cookie"]
    assert os.path.getmtime(headers) >= os.path.getmtime(jar)


def test_load_headers_trusts_newer_headers(tmp_path, monkeypatch):
    headers, jar = _setup(tmp_path, monkeypatch)
    _write_jar(jar, {"__Secure-1PSIDTS": "old"})
    _bump(headers, 60)   # a fresh paste

    assert ytmusic_auth.load_headers()["cookie"] == "SID=sid0; __Secure-1PSIDTS=ts0; PREF=x"


def test_write_cookies_file_keeps_newer_jar(tmp_path, monkeypatch):
    headers, jar = _setup(tmp_path, monkeypatch)
    _write_jar(jar, {"__Secure-1PSIDTS": "rotated"})
    _bump(jar, 60)

    dl.write_cookies_file("SID=sid0; __Secure-1PSIDTS=ts0")

    assert ytmusic_auth.read_cookie_jar(str(jar))["__Secure-1PSIDTS"] == "rotated"


def test_write_cookies_file_regenerates_after_fresh_paste(tmp_path, monkeypatch):
    headers, jar = _setup(tmp_path, monkeypatch)
    _write_jar(jar, {"__Secure-1PSIDTS": "rotated"})
    _bump(headers, 60)

    dl.write_cookies_file("SID=sid9; __Secure-1PSIDTS=ts9")

    assert ytmusic_auth.read_cookie_jar(str(jar)) == {"SID": "sid9", "__Secure-1PSIDTS": "ts9"}


def test_get_cookie_header_returns_rotated_values(tmp_path, monkeypatch):
    headers, jar = _setup(tmp_path, monkeypatch)
    _write_jar(jar, {"__Secure-1PSIDTS": "ts2"})
    _bump(jar, 60)
    assert "__Secure-1PSIDTS=ts2" in dl.get_cookie_header()
