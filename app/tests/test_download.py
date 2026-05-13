"""Unit tests for download.py utility functions."""
import json
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
