"""Flask endpoint smoke tests — run inside the container with: pytest"""
import json
import os


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"YT Music" in resp.data


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_auth_status_unauthenticated(client):
    resp = client.get("/auth/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["authenticated"] is False
    assert data["method"] is None
    assert data["reason"] == "no_credentials"


def test_auth_headers_missing_body_returns_400(client):
    resp = client.post("/auth/headers", data={})
    assert resp.status_code == 400


def test_auth_headers_invalid_content_returns_400(client):
    resp = client.post("/auth/headers", data={"headers_raw": "not valid headers"})
    assert resp.status_code == 400


def test_auth_revoke_when_not_authenticated(client):
    resp = client.post("/auth/revoke")
    assert resp.status_code in (200, 302)


def test_auth_revoke_removes_oauth_files(client):
    """Revoke must clear both header-auth and OAuth files."""
    import app as m
    os.makedirs(m.AUTH_DIR, exist_ok=True)
    for p in (m.HEADERS_AUTH_FILE, m.OAUTH_FILE, m.OAUTH_CLIENT_FILE):
        with open(p, "w") as f:
            f.write("{}")
    resp = client.post("/auth/revoke")
    assert resp.status_code in (200, 302)
    for p in (m.HEADERS_AUTH_FILE, m.OAUTH_FILE, m.OAUTH_CLIENT_FILE):
        assert not os.path.exists(p)


# ── OAuth ────────────────────────────────────────────────────────────────────

def test_auth_oauth_setup_missing_creds_returns_400(client):
    resp = client.post("/auth/oauth/setup", json={}, content_type="application/json")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_auth_oauth_setup_persists_client_and_returns_device_code(client, monkeypatch):
    """Successful setup must save client_id/secret and return a user_code."""
    fake_code = {
        "device_code": "DEV-123",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://www.google.com/device",
        "interval": 5,
        "expires_in": 1800,
    }

    class FakeCreds:
        def __init__(self, client_id, client_secret):
            self.client_id = client_id
            self.client_secret = client_secret
        def get_code(self):
            return fake_code

    import ytmusicapi.auth.oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "OAuthCredentials", FakeCreds)

    resp = client.post(
        "/auth/oauth/setup",
        json={"client_id": "cid", "client_secret": "csec"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["user_code"] == "ABCD-EFGH"
    assert body["verification_url"].startswith("https://")
    assert body["device_code"] == "DEV-123"

    import app as m
    with open(m.OAUTH_CLIENT_FILE) as f:
        saved = json.load(f)
    assert saved == {"client_id": "cid", "client_secret": "csec"}


def test_auth_oauth_poll_pending_reports_pending(client, monkeypatch):
    """While Google returns authorization_pending, we must report status=pending
    (not error) so the frontend keeps polling."""
    import app as m
    os.makedirs(os.path.dirname(m.OAUTH_CLIENT_FILE), exist_ok=True)
    with open(m.OAUTH_CLIENT_FILE, "w") as f:
        json.dump({"client_id": "cid", "client_secret": "csec"}, f)

    class FakeCreds:
        def __init__(self, client_id, client_secret):
            pass
        def token_from_code(self, device_code):
            return {"error": "authorization_pending"}

    import ytmusicapi.auth.oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "OAuthCredentials", FakeCreds)

    resp = client.post(
        "/auth/oauth/poll",
        json={"device_code": "DEV-123"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "pending"
    assert not os.path.exists(m.OAUTH_FILE)


def test_auth_oauth_poll_success_saves_token(client, monkeypatch):
    """On successful poll the token must land in OAUTH_FILE."""
    import app as m
    os.makedirs(os.path.dirname(m.OAUTH_CLIENT_FILE), exist_ok=True)
    with open(m.OAUTH_CLIENT_FILE, "w") as f:
        json.dump({"client_id": "cid", "client_secret": "csec"}, f)

    class FakeCreds:
        def __init__(self, client_id, client_secret):
            pass
        def token_from_code(self, device_code):
            return {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

    import ytmusicapi.auth.oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "OAuthCredentials", FakeCreds)

    resp = client.post(
        "/auth/oauth/poll",
        json={"device_code": "DEV-123"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"
    with open(m.OAUTH_FILE) as f:
        saved = json.load(f)
    assert saved["access_token"] == "AT"
    assert saved["refresh_token"] == "RT"
    # OAuthToken.from_json reads expires_at to decide when to refresh;
    # missing it silently defaults to 0 and forces a refresh every call.
    assert "expires_at" in saved
    assert saved["expires_at"] > saved["expires_in"]
    assert saved["token_type"] == "Bearer"
    assert "scope" in saved


def test_auth_oauth_poll_without_setup_returns_400(client):
    resp = client.post(
        "/auth/oauth/poll",
        json={"device_code": "DEV-123"},
        content_type="application/json",
    )
    assert resp.status_code == 400


# ── Runs ─────────────────────────────────────────────────────────────────────

def test_api_runs_returns_list(client):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_download_status_returns_known_state(client):
    resp = client.get("/download-status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] in ("running", "idle")


# ── Downloads ────────────────────────────────────────────────────────────────

def test_api_downloads_empty(client):
    resp = client.get("/api/downloads")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "files" in data
    assert isinstance(data["files"], list)


def test_api_downloads_metadata_pagination(client):
    resp = client.get("/api/downloads/metadata?page=1&limit=10")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "files" in data
    assert "total" in data
    assert "page" in data
    assert "pages" in data
    assert data["page"] == 1


# ── Playlists ────────────────────────────────────────────────────────────────

def test_api_playlists_requires_auth(client):
    resp = client.get("/api/playlists")
    assert resp.status_code == 401
    assert "not authenticated" in resp.get_json()["error"]


def test_api_playlist_selection_returns_ids(client):
    resp = client.get("/api/playlists/selection")
    assert resp.status_code == 200
    assert "ids" in resp.get_json()


def test_api_playlist_selection_save(client):
    resp = client.post(
        "/api/playlists/selection",
        json={"ids": ["PLabc123"]},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


# ── Schedule / cron ──────────────────────────────────────────────────────────

def test_api_cron_get(client):
    resp = client.get("/api/cron")
    assert resp.status_code == 200
    assert "expression" in resp.get_json()


def test_api_cron_set_valid(client):
    resp = client.post(
        "/api/cron",
        json={"expression": "0 3 * * *"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_api_cron_set_invalid_expression(client):
    resp = client.post(
        "/api/cron",
        json={"expression": "not a cron"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_api_next_run(client):
    resp = client.get("/api/next-run")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "next_run" in data
    assert "delta" in data


# ── Logs ─────────────────────────────────────────────────────────────────────

def test_api_logs_returns_list(client):
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_api_status_returns_expected_fields(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tracks" in data
    assert "size" in data
    assert "last_run" in data
    assert "next_run" in data
    assert "running" in data
    # Optionally check types
    assert isinstance(data["tracks"], int)
    assert isinstance(data["running"], bool)
