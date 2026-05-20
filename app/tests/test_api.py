"""Flask endpoint smoke tests — run inside the container with: pytest"""


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"YT Music" in resp.data


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_auth_status_unauthenticated(client):
    resp = client.get("/auth/status")
    assert resp.status_code == 200
    assert resp.get_json()["authenticated"] is False


def test_auth_headers_missing_body_returns_400(client):
    resp = client.post("/auth/headers", data={})
    assert resp.status_code == 400


def test_auth_headers_invalid_content_returns_400(client):
    resp = client.post("/auth/headers", data={"headers_raw": "not valid headers"})
    assert resp.status_code == 400


def test_auth_revoke_when_not_authenticated(client):
    resp = client.post("/auth/revoke")
    assert resp.status_code in (200, 302)


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
