import json
import os
from ytmusicapi import YTMusic

# Container layout by default; overridable so tooling can run outside Docker
# against the mounted ./app/data/auth directory.
AUTH_DIR = os.getenv("YTM_AUTH_DIR", "/app/data/auth")
HEADERS_AUTH_FILE = f"{AUTH_DIR}/headers_auth.json"
OAUTH_FILE = f"{AUTH_DIR}/oauth.json"
OAUTH_CLIENT_FILE = f"{AUTH_DIR}/oauth_client.json"


def load_oauth_client():
    """Return {client_id, client_secret} dict, or None."""
    try:
        with open(OAUTH_CLIENT_FILE) as f:
            data = json.load(f)
        if data.get("client_id") and data.get("client_secret"):
            return data
    except Exception:
        pass
    return None


def has_oauth() -> bool:
    return os.path.exists(OAUTH_FILE) and load_oauth_client() is not None


def has_headers() -> bool:
    return os.path.exists(HEADERS_AUTH_FILE)


# The one cookie jar yt-dlp reads and writes. There used to be two (one for
# downloads, one for snippet fetches), each regenerated from headers_auth.json
# before every run. YouTube rotates the session tokens (__Secure-*PSIDTS,
# SIDCC) on use; yt-dlp saved the rotated ones to its jar, ytmusicapi kept
# presenting the originals from headers_auth.json, and Google revoked the
# whole session on the replay — within the hour, every time, on 2026-09-16/17.
COOKIES_FILE = f"{AUTH_DIR}/cookies.txt"

_ROTATING = None  # every cookie: yt-dlp's jar is authoritative for all of them


def _cookie_key(headers: dict):
    for key in ("cookie", "Cookie"):
        if key in headers:
            return key
    return None


def parse_cookie_header(value: str):
    """'a=1; b=2' -> [('a', '1'), ('b', '2')], order kept."""
    pairs = []
    for part in (value or "").split(";"):
        part = part.strip()
        if "=" in part:
            name, val = part.split("=", 1)
            pairs.append((name.strip(), val.strip()))
    return pairs


def read_cookie_jar(path: str) -> dict:
    """Netscape jar -> {name: value}. yt-dlp prefixes HttpOnly cookies with
    '#HttpOnly_', which a naive comment filter would drop."""
    jar = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#") or not line.strip():
                    continue
                fields = line.split("\t")
                if len(fields) >= 7:
                    jar[fields[5]] = fields[6]
    except OSError:
        pass
    return jar


def _write_headers(headers: dict, path=None) -> None:
    path = path or HEADERS_AUTH_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(headers, f, indent=2)
    os.replace(tmp, path)


def jar_is_newer(headers_path=None, jar_path=None) -> bool:
    try:
        return (os.path.getmtime(jar_path or COOKIES_FILE)
                > os.path.getmtime(headers_path or HEADERS_AUTH_FILE))
    except OSError:
        return False


def load_headers(headers_path=None, jar_path=None) -> dict:
    """headers_auth.json, with whatever yt-dlp has rotated since folded in.

    When the jar is newer than the headers file, its values win for every
    cookie the header carries, and the merged header is written back so both
    files describe the same session again. The next jar regeneration then
    starts from these values rather than the stale paste.
    """
    headers_path = headers_path or HEADERS_AUTH_FILE
    jar_path = jar_path or COOKIES_FILE
    with open(headers_path, encoding="utf-8") as f:
        headers = json.load(f)
    key = _cookie_key(headers)
    if key and jar_is_newer(headers_path, jar_path):
        jar = read_cookie_jar(jar_path)
        pairs = parse_cookie_header(headers[key])
        merged = "; ".join(f"{name}={jar.get(name, value)}"
                           for name, value in pairs)
        if merged != headers[key]:
            headers[key] = merged
            _write_headers(headers, headers_path)
        # Stamp the headers file just past the jar, so both files now agree
        # on which is current even if the jar's mtime came from a skewed
        # clock. Otherwise every call would re-merge and the jar would never
        # be regenerated after a fresh paste that lands within the skew.
        jar_mtime = os.path.getmtime(jar_path)
        if os.path.getmtime(headers_path) <= jar_mtime:
            os.utime(headers_path, (jar_mtime + 1, jar_mtime + 1))
    return headers


def headers_to_ytmusic() -> YTMusic:
    """Return an authenticated YTMusic client, preferring cookie headers.

    Historically OAuth was preferred (longer-lived tokens), but Google now
    rejects tokens from custom OAuth clients on the internal
    music.youtube.com/youtubei/v1/* endpoints with a 400 INVALID_ARGUMENT.
    Cookies are the only reliable path today, so use them when present and
    only fall back to OAuth if cookies aren't set up.
    """
    if os.path.exists(HEADERS_AUTH_FILE):
        # JSON string, not the path: the session may have been rotated by
        # yt-dlp since the file was written, and load_headers folds that in.
        return YTMusic(json.dumps(load_headers()))
    client = load_oauth_client()
    if client and os.path.exists(OAUTH_FILE):
        from ytmusicapi.auth.oauth import OAuthCredentials
        return YTMusic(
            OAUTH_FILE,
            oauth_credentials=OAuthCredentials(
                client_id=client["client_id"],
                client_secret=client["client_secret"],
            ),
        )
    return YTMusic(HEADERS_AUTH_FILE)
