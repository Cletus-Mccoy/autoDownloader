"""Tests for the Chrome DevTools header normalizer in app.py."""
import app as flask_module

normalize = flask_module._normalize_headers_raw


def test_standard_format_is_unchanged():
    raw = "Accept: */*\nCookie: foo=bar\nx-goog-authuser: 0"
    result = normalize(raw)
    assert "Accept: */*" in result
    assert "Cookie: foo=bar" in result
    assert "x-goog-authuser: 0" in result


def test_chrome_devtools_alternating_format():
    raw = "accept\n*/*\ncookie\nfoo=bar\nx-goog-authuser\n0"
    result = normalize(raw)
    assert "accept: */*" in result
    assert "cookie: foo=bar" in result
    assert "x-goog-authuser: 0" in result


def test_http2_pseudo_headers_are_dropped():
    raw = ":authority\nmusic.youtube.com\n:method\nPOST\n:scheme\nhttps\ncookie\nfoo=bar"
    result = normalize(raw)
    assert ":authority" not in result
    assert ":method" not in result
    assert ":scheme" not in result
    assert "cookie: foo=bar" in result


def test_decoded_block_after_x_client_data_is_skipped():
    """Chrome appends a multi-line Decoded: annotation that must not shift pairing."""
    raw = (
        "x-client-data\nABCDEFG==\n"
        "Decoded:\nmessage ClientVariations {\n"
        "  repeated int32 variation_id = [123, 456];\n"
        "}\n"
        "x-goog-authuser\n0\n"
        "cookie\nfoo=bar"
    )
    result = normalize(raw)
    assert "x-client-data: ABCDEFG==" in result
    assert "x-goog-authuser: 0" in result
    assert "cookie: foo=bar" in result
    assert "Decoded:" not in result
    assert "ClientVariations" not in result


def test_value_that_looks_like_header_name_is_treated_as_value():
    """Values like 'true' or 'gzip' match the header-name regex but must not be treated as keys."""
    raw = "x-youtube-bootstrap-logged-in\ntrue\naccept-encoding\ngzip\nx-goog-authuser\n0"
    result = normalize(raw)
    assert "x-youtube-bootstrap-logged-in: true" in result
    assert "accept-encoding: gzip" in result
    assert "x-goog-authuser: 0" in result


def test_empty_input_returns_empty():
    assert normalize("") == ""


def test_whitespace_only_input_returns_empty():
    assert normalize("   \n  \n  ") == ""
