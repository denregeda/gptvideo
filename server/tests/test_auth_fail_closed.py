"""Авторизация обязана отказывать безопасно при проблемах с БД.

JWT хранит только имя пользователя. Роль и состояние учётной записи читаются
из БД на каждом запросе, поэтому удаление, блокировка и смена роли должны
вступать в силу немедленно. Ошибка БД никогда не должна создавать роль admin.
"""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from deps import (
    create_access_token,
    get_current_admin,
    get_db,
    require_moderator,
    require_write,
)
from routers import websockets


class _Row(dict):
    def __getattr__(self, name):
        return self.get(name)


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row

    def fetchone(self):
        return self.row

    def scalar(self):
        return self.row.get("role") if self.row else None


class _IdentityDB:
    def __init__(self, row=None, error=None):
        self.row = _Row(row) if row is not None else None
        self.error = error

    def execute(self, _query, _params=None):
        if self.error:
            raise self.error
        return _Result(self.row)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _user(role="admin", active=True, blocked=False, advertiser_id=None,
          session_version=1):
    return {
        "username": "existing-user",
        "role": role,
        "is_active": active,
        "is_blocked": blocked,
        "advertiser_id": advertiser_id,
        "session_version": session_version,
    }


def _token():
    return create_access_token({"sub": "existing-user"})


def test_database_error_never_synthesizes_admin_role():
    db = _IdentityDB(error=RuntimeError("database unavailable"))

    with pytest.raises(HTTPException) as error:
        get_current_admin(token=_token(), db=db)

    assert error.value.status_code == 503
    assert "admin" not in str(error.value.detail).lower()


def test_deleted_user_invalidates_existing_token():
    with pytest.raises(HTTPException) as error:
        get_current_admin(token=_token(), db=_IdentityDB())

    assert error.value.status_code == 401


@pytest.mark.parametrize(("active", "blocked"), [(False, False), (True, True)])
def test_inactive_or_blocked_user_is_rejected(active, blocked):
    with pytest.raises(HTTPException) as error:
        get_current_admin(
            token=_token(),
            db=_IdentityDB(_user(active=active, blocked=blocked)),
        )

    assert error.value.status_code == 403


def test_role_change_applies_to_already_issued_token():
    token = _token()

    before = get_current_admin(token=token, db=_IdentityDB(_user(role="admin")))
    after = get_current_admin(
        token=token,
        db=_IdentityDB(_user(role="advertiser", advertiser_id=77)),
    )

    assert before["role"] == "admin"
    assert after["role"] == "advertiser"
    assert after["advertiser_id"] == 77


def test_unknown_role_is_rejected_instead_of_inheriting_permissions():
    with pytest.raises(HTTPException) as error:
        get_current_admin(
            token=_token(),
            db=_IdentityDB(_user(role="unexpected-role")),
        )

    assert error.value.status_code == 403


@pytest.mark.parametrize(("role", "allowed"), [
    ("superadmin", True),
    ("admin", True),
    ("auditor", False),
    ("moderator", False),
    ("advertiser", False),
    ("unexpected-role", False),
])
def test_write_permission_uses_explicit_role_allowlist(role, allowed):
    user = _user(role=role)

    if allowed:
        assert require_write(user) is user
    else:
        with pytest.raises(HTTPException) as error:
            require_write(user)
        assert error.value.status_code == 403


@pytest.mark.parametrize(("role", "allowed"), [
    ("superadmin", True),
    ("admin", True),
    ("moderator", True),
    ("auditor", False),
    ("advertiser", False),
    ("unexpected-role", False),
])
def test_moderation_permission_uses_explicit_role_allowlist(role, allowed):
    user = _user(role=role)

    if allowed:
        assert require_moderator(user) is user
    else:
        with pytest.raises(HTTPException) as error:
            require_moderator(user)
        assert error.value.status_code == 403


def test_http_middleware_returns_503_when_identity_store_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        main,
        "SessionLocal",
        lambda: _IdentityDB(error=RuntimeError("database unavailable")),
    )

    response = TestClient(main.app).get(
        "/health",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 503


def test_http_middleware_rejects_deleted_user_token(monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", lambda: _IdentityDB())

    response = TestClient(main.app).get(
        "/health",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 401


def test_http_middleware_rejects_blocked_user_token(monkeypatch):
    monkeypatch.setattr(
        main,
        "SessionLocal",
        lambda: _IdentityDB(_user(blocked=True)),
    )

    response = TestClient(main.app).get(
        "/health",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(("db", "close_code"), [
    (_IdentityDB(), 4401),
    (_IdentityDB(_user(active=False)), 4403),
    (_IdentityDB(_user(blocked=True)), 4403),
    (_IdentityDB(_user(role="advertiser", advertiser_id=77)), 4403),
    (_IdentityDB(_user(role="unexpected-role")), 4403),
    (_IdentityDB(error=RuntimeError("database unavailable")), 1013),
])
def test_dashboard_websocket_fails_closed(db, close_code):
    app = FastAPI()
    app.include_router(websockets.router)
    app.dependency_overrides[get_db] = lambda: db

    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(app).websocket_connect(
                "/ws/dashboard",
                subprotocols=["ds-auth", _token()]):
            pass

    assert error.value.code == close_code
