"""Защита резервных копий от скачивания посторонними.

Дамп PostgreSQL содержит данные всей системы, поэтому одного скрытия кнопки
в панели недостаточно: доступ обязан проверяться самим API. Тесты поднимают
только роутер бэкапов с подменённой БД и реальным временным файлом.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_current_admin, get_db
from routers import backups


class _Result:
    def __init__(self, filename):
        self._filename = filename

    def fetchone(self):
        return SimpleNamespace(filename=self._filename)


class _BackupDB:
    def __init__(self, filename):
        self.filename = filename
        self.queries = []
        self.committed = False

    def execute(self, query, params=None):
        self.queries.append((str(query), params))
        return _Result(self.filename)

    def commit(self):
        self.committed = True


def _client(tmp_path, monkeypatch, role=None, filename="backup_20260730_120000.sql.gz"):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / filename).write_bytes(b"safe backup")
    monkeypatch.setattr(backups, "BACKUP_DIR", str(backup_dir))

    db = _BackupDB(filename)
    app = FastAPI()
    app.include_router(backups.router)
    app.dependency_overrides[get_db] = lambda: db
    if role is not None:
        app.dependency_overrides[get_current_admin] = lambda: {
            "username": f"test-{role}",
            "role": role,
        }
    return TestClient(app), db


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/backups"),
    ("post", "/backups/create"),
    ("get", "/backups/1/download"),
    ("delete", "/backups/1"),
])
def test_backup_endpoints_require_authentication(tmp_path, monkeypatch, method, path):
    client, _ = _client(tmp_path, monkeypatch)

    response = getattr(client, method)(path)

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/backups"),
    ("post", "/backups/create"),
    ("get", "/backups/1/download"),
    ("delete", "/backups/1"),
])
@pytest.mark.parametrize("role", ["advertiser", "auditor", "moderator"])
def test_backup_endpoints_reject_non_admin_roles(
        tmp_path, monkeypatch, role, method, path):
    client, _ = _client(tmp_path, monkeypatch, role=role)

    response = getattr(client, method)(path)

    assert response.status_code == 403


def test_admin_can_download_backup_and_action_is_audited(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch, role="admin")

    response = client.get("/backups/1/download")

    assert response.status_code == 200
    assert response.content == b"safe backup"
    assert any("INSERT INTO audit_log" in query for query, _ in db.queries)
    assert db.committed is True


def test_backup_filename_cannot_escape_backup_directory(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (tmp_path / "outside.sql.gz").write_bytes(b"must stay private")
    monkeypatch.setattr(backups, "BACKUP_DIR", str(backup_dir))

    db = _BackupDB("../outside.sql.gz")
    app = FastAPI()
    app.include_router(backups.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_admin] = lambda: {
        "username": "test-admin",
        "role": "admin",
    }

    response = TestClient(app).get("/backups/1/download")

    assert response.status_code == 404
