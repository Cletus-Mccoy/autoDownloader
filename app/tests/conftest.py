import sys
import os
import pytest

# Make app.py and scripts/ importable when pytest runs from /app
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_APP_DIR, "scripts")
for _p in (_APP_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    base = tmp_path_factory.mktemp("data")
    (base / "auth").mkdir()
    (base / "downloads").mkdir()
    (base / "logs").mkdir()
    (base / "runs.json").write_text("[]")
    (base / "playlist_selection.json").write_text('{"ids": []}')
    return base


@pytest.fixture
def flask_app(data_dir, monkeypatch, tmp_path):
    import app as flask_module
    from scripts import ytmusic_auth

    auth_dir       = str(data_dir / "auth")
    headers_file   = str(data_dir / "auth" / "headers_auth.json")
    oauth_file     = str(data_dir / "auth" / "oauth.json")
    oauth_client   = str(data_dir / "auth" / "oauth_client.json")

    monkeypatch.setattr(flask_module, "DATA_DIR",          str(data_dir))
    monkeypatch.setattr(flask_module, "RUNS_FILE",         str(data_dir / "runs.json"))
    monkeypatch.setattr(flask_module, "DOWNLOAD_DIR",      str(data_dir / "downloads"))
    monkeypatch.setattr(flask_module, "LOG_DIR",           str(data_dir / "logs"))
    monkeypatch.setattr(flask_module, "AUTH_DIR",          auth_dir)
    monkeypatch.setattr(flask_module, "HEADERS_AUTH_FILE", headers_file)
    monkeypatch.setattr(flask_module, "OAUTH_FILE",        oauth_file)
    monkeypatch.setattr(flask_module, "OAUTH_CLIENT_FILE", oauth_client)
    monkeypatch.setattr(flask_module, "SELECTION_FILE",    str(data_dir / "playlist_selection.json"))
    monkeypatch.setattr(flask_module, "CRON_FILE",         str(tmp_path / "crontab"))

    vibe_dir = tmp_path / "vibe"
    (vibe_dir / "audio").mkdir(parents=True)
    monkeypatch.setattr(flask_module, "VIBE_DIR",          str(vibe_dir))
    monkeypatch.setattr(flask_module, "SORT_QUEUE_FILE",   str(vibe_dir / "sort_queue.json"))
    monkeypatch.setattr(flask_module, "VIBE_LIBRARY_FILE", str(vibe_dir / "library.json"))
    monkeypatch.setattr(flask_module, "DECISIONS_FILE",    str(vibe_dir / "reports" / "decisions.jsonl"))

    # Also patch ytmusic_auth's constants — the /auth/status route imports
    # has_oauth/has_headers from there, which read these paths directly.
    monkeypatch.setattr(ytmusic_auth, "AUTH_DIR",          auth_dir)
    monkeypatch.setattr(ytmusic_auth, "HEADERS_AUTH_FILE", headers_file)
    monkeypatch.setattr(ytmusic_auth, "OAUTH_FILE",        oauth_file)
    monkeypatch.setattr(ytmusic_auth, "OAUTH_CLIENT_FILE", oauth_client)

    flask_module.app.config["TESTING"] = True
    return flask_module.app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()
