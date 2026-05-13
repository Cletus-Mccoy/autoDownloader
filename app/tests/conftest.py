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

    monkeypatch.setattr(flask_module, "DATA_DIR",          str(data_dir))
    monkeypatch.setattr(flask_module, "RUNS_FILE",         str(data_dir / "runs.json"))
    monkeypatch.setattr(flask_module, "DOWNLOAD_DIR",      str(data_dir / "downloads"))
    monkeypatch.setattr(flask_module, "LOG_DIR",           str(data_dir / "logs"))
    monkeypatch.setattr(flask_module, "AUTH_DIR",          str(data_dir / "auth"))
    monkeypatch.setattr(flask_module, "HEADERS_AUTH_FILE", str(data_dir / "auth" / "headers_auth.json"))
    monkeypatch.setattr(flask_module, "SELECTION_FILE",    str(data_dir / "playlist_selection.json"))
    monkeypatch.setattr(flask_module, "CRON_FILE",         str(tmp_path / "crontab"))

    flask_module.app.config["TESTING"] = True
    return flask_module.app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()
