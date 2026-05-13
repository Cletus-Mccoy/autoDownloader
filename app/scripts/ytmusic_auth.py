from ytmusicapi import YTMusic

HEADERS_AUTH_FILE = "/app/data/auth/headers_auth.json"


def headers_to_ytmusic() -> YTMusic:
    return YTMusic(HEADERS_AUTH_FILE)
