"""Tests for the Chrome DevTools header normalizer in app.py."""
import app as flask_module

normalize = flask_module._normalize_headers_raw


def test_standard_format_is_unchanged():
    raw = "Accept: */*\nCookie: foo=bar\nx-goog-authuser: 0"
    result = normalize(raw)
    assert "Accept: */*" in result
    assert "Cookie: foo=bar" in result
    assert "x-goog-authuser: 0" in result


def test_standard_format_from_a_form_loses_its_carriage_returns():
    # A textarea is submitted with CRLF line endings; ytmusicapi splits on
    # "\n" only, so a kept "\r" ended up in every header value and requests
    # refused to send them (reported in the UI as expired credentials).
    raw = "POST /youtubei/v1/browse HTTP/2\r\nConnection: keep-alive\r\nCookie: a=1; b=2\r\nX-Goog-AuthUser: 0\r\n"
    result = normalize(raw)
    assert "\r" not in result
    assert "Connection: keep-alive" in result.split("\n")
    assert "Cookie: a=1; b=2" in result.split("\n")


def test_stored_headers_with_carriage_returns_are_repaired(tmp_path):
    import json, os
    from ytmusic_auth import load_headers
    path = tmp_path / "headers_auth.json"
    path.write_text(json.dumps({"connection": "keep-alive\r", "cookie": "a=1; b=2\r",
                                "x-goog-authuser": "0\r"}))
    os.utime(path, (1_000_000, 1_000_000))
    headers = load_headers(str(path), str(tmp_path / "no-jar.txt"))
    assert headers == {"connection": "keep-alive", "cookie": "a=1; b=2", "x-goog-authuser": "0"}
    assert json.loads(path.read_text()) == headers
    assert os.path.getmtime(path) == 1_000_000  # ordering against the jar kept


def test_chrome_devtools_alternating_format():
    raw = "accept\n*/*\ncookie\nfoo=bar\nx-goog-authuser\n0"
    result = normalize(raw)
    assert "accept: */*" in result
    assert "cookie: foo=bar" in result
    assert "x-goog-authuser: 0" in result


def test_firefox_alternating_format_with_capitalised_names():
    # Firefox's "Copy Request Headers" (non-raw): capitalised names, values on
    # the next line indented by a tab. Values may themselves contain "; ".
    raw = ("Accept\n\t*/*\nAuthorization\n\tSAPISIDHASH 1_abc\n"
           "Cookie\n\ta=1; b=2\nUser-Agent\n\tMozilla/5.0 (Windows NT 10.0; rv:160.0)\n"
           "X-Goog-AuthUser\n\t0\n")
    result = normalize(raw)
    assert "Cookie: a=1; b=2" in result
    assert "X-Goog-AuthUser: 0" in result
    assert "Authorization: SAPISIDHASH 1_abc" in result
    assert "User-Agent: Mozilla/5.0 (Windows NT 10.0; rv:160.0)" in result


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
