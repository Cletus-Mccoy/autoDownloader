import json
import os
from ytmusicapi import YTMusic

AUTH_DIR = "/app/data/auth"
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
    """Return an authenticated YTMusic client, preferring OAuth if configured.

    OAuth refresh tokens live effectively indefinitely (~6 months of idle
    tolerance); the cookie-header path expires more aggressively. Fall back
    to headers so existing setups keep working.
    """
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
