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


def headers_to_ytmusic() -> YTMusic:
    """Return an authenticated YTMusic client, preferring cookie headers.

    Historically OAuth was preferred (longer-lived tokens), but Google now
    rejects tokens from custom OAuth clients on the internal
    music.youtube.com/youtubei/v1/* endpoints with a 400 INVALID_ARGUMENT.
    Cookies are the only reliable path today, so use them when present and
    only fall back to OAuth if cookies aren't set up.
    """
    if os.path.exists(HEADERS_AUTH_FILE):
        return YTMusic(HEADERS_AUTH_FILE)
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
