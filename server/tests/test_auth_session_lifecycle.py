"""Защита входа и управляемый отзыв пользовательских JWT-сессий."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import jwt

import deps
from deps import create_access_token, get_current_admin, get_db, pwd_context
from routers import auth, users, websockets
from auth_security import (
    AuthRateLimiter,
    AuthSecurityStoreUnavailable,
    LoginRateLimited,
    session_version_matches,
    validate_secret_key,
)


class _FakeRedis:
    def __init__(self, error=None):
        self.values = {}
        self.ttls = {}
        self.error = error

    def _check(self):
        if self.error:
            raise self.error

    def get(self, key):
        self._check()
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.values:
            return False
        self.values[key] = int(value)
        self.ttls[key] = int(ex or -1)
        return True

    def incr(self, key):
        self._check()
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def ttl(self, key):
        self._check()
        return self.ttls.get(key, -2)

    def expire(self, key, seconds):
        self._check()
        self.ttls[key] = int(seconds)
        return True

    def delete(self, *keys):
        self._check()
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)
        return len(keys)

    def ping(self):
        self._check()
        return True


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row

    def fetchone(self):
        return self.row


class _IdentityDB:
    def __init__(self, session_version):
        self.row = {
            "username": "operator",
            "role": "admin",
            "is_active": True,
            "is_blocked": False,
            "advertiser_id": None,
            "session_version": session_version,
        }

    def execute(self, _query, _params=None):
        return _Result(self.row)


class _Row(dict):
    def __getattr__(self, name):
        return self.get(name)


class _LoginDB:
    def __init__(self, password="correct-password", session_version=7):
        self.password_hash = pwd_context.hash(password)
        self.session_version = session_version
        self.queries = []
        self.commits = 0

    def execute(self, query, params=None):
        sql = str(query)
        self.queries.append((sql, params or {}))
        if "FROM users WHERE username" in sql:
            return _Result(_Row({
                "username": (params or {}).get("u"),
                "password_hash": self.password_hash,
                "is_blocked": False,
                "is_active": True,
                "session_version": self.session_version,
            }))
        return _Result(None)

    def commit(self):
        self.commits += 1


class _PasswordDB:
    def __init__(self):
        self.password_hash = pwd_context.hash("old-password")
        self.queries = []
        self.commits = 0

    def execute(self, query, params=None):
        sql = str(query)
        self.queries.append(sql)
        if "SELECT id, password_hash" in sql:
            return _Result(_Row({"id": 17, "password_hash": self.password_hash}))
        if "RETURNING session_version" in sql:
            return _Result(_Row({"session_version": 8}))
        return _Result(None)

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize("secret", [
    "",
    "short",
    "change-me-super-secret-key",
    "GENERATE_WITH_INSTALL_SERVER",
    "a" * 64,
])
def test_weak_or_placeholder_secret_key_is_rejected(secret):
    with pytest.raises(ValueError):
        validate_secret_key(secret)


def test_random_secret_key_is_accepted_without_exposing_it():
    secret = "d4a7b98c21fe5036" * 4
    assert validate_secret_key(secret) == secret


def test_session_version_must_be_present_and_equal():
    user = {"session_version": 3}

    assert session_version_matches({"sv": 3}, user)
    assert not session_version_matches({}, user)
    assert not session_version_matches({"sv": 2}, user)
    assert not session_version_matches({"sv": True}, user)


def test_password_generation_change_invalidates_old_token():
    old_token = create_access_token({"sub": "operator", "sv": 4})

    with pytest.raises(HTTPException) as error:
        get_current_admin(token=old_token, db=_IdentityDB(session_version=5))

    assert error.value.status_code == 401


def test_matching_generation_keeps_token_valid():
    token = create_access_token({"sub": "operator", "sv": 5})

    user = get_current_admin(token=token, db=_IdentityDB(session_version=5))

    assert user["username"] == "operator"
    assert user["session_version"] == 5


def test_dashboard_recheck_closes_already_open_revoked_session():
    payload = {"sub": "operator", "sv": 4}

    _user, code, reason = websockets._dashboard_identity_check(
        _IdentityDB(session_version=5), "operator", payload)

    assert code == 4401
    assert "отозвана" in reason


def test_rate_limiter_blocks_account_after_threshold_and_keeps_keys_private():
    redis = _FakeRedis()
    limiter = AuthRateLimiter(
        redis_client=redis,
        account_limit=3,
        ip_limit=20,
        window_seconds=900,
    )

    assert not limiter.register_failure("Alice", "10.0.0.7").limited
    assert not limiter.register_failure("Alice", "10.0.0.7").limited
    decision = limiter.register_failure("Alice", "10.0.0.7")

    assert decision.limited
    assert decision.newly_limited
    assert decision.retry_after == 900
    with pytest.raises(LoginRateLimited) as error:
        limiter.ensure_allowed("Alice", "10.0.0.7")
    assert error.value.retry_after == 900
    assert all("Alice" not in key and "10.0.0.7" not in key
               for key in redis.values)


def test_success_clears_account_failures_but_not_shared_ip_history():
    redis = _FakeRedis()
    limiter = AuthRateLimiter(redis_client=redis)
    limiter.register_failure("operator", "10.0.0.7")
    account_key, ip_key = limiter.keys_for("operator", "10.0.0.7")

    limiter.register_success("operator", "10.0.0.7")

    assert account_key not in redis.values
    assert ip_key in redis.values


def test_rate_limiter_fails_closed_when_redis_is_unavailable():
    limiter = AuthRateLimiter(redis_client=_FakeRedis(RuntimeError("redis down")))

    with pytest.raises(AuthSecurityStoreUnavailable):
        limiter.ensure_allowed("operator", "10.0.0.7")
    with pytest.raises(AuthSecurityStoreUnavailable):
        limiter.register_failure("operator", "10.0.0.7")


def _login_client(db):
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_login_token_contains_current_session_version(monkeypatch):
    db = _LoginDB(session_version=7)
    limiter = AuthRateLimiter(redis_client=_FakeRedis())
    monkeypatch.setattr(auth, "login_limiter", limiter)

    response = _login_client(db).post(
        "/token", data={"username": "operator", "password": "correct-password"})

    assert response.status_code == 200
    payload = jwt.decode(
        response.json()["access_token"],
        deps.SECRET_KEY,
        algorithms=[deps.ALGORITHM],
    )
    assert payload["sub"] == "operator"
    assert payload["sv"] == 7


def test_login_returns_429_after_failed_attempt_threshold(monkeypatch):
    db = _LoginDB()
    limiter = AuthRateLimiter(
        redis_client=_FakeRedis(), account_limit=2, ip_limit=20)
    monkeypatch.setattr(auth, "login_limiter", limiter)
    client = _login_client(db)

    first = client.post(
        "/token", data={"username": "operator", "password": "wrong"})
    second = client.post(
        "/token", data={"username": "operator", "password": "wrong"})
    blocked = client.post(
        "/token", data={"username": "operator", "password": "correct-password"})

    assert first.status_code == 401
    assert second.status_code == 429
    assert blocked.status_code == 429
    assert int(second.headers["retry-after"]) > 0
    assert sum("Ограничены попытки входа" in sql for sql, _ in db.queries) == 1


def test_login_fails_closed_before_password_check_when_redis_is_down(monkeypatch):
    db = _LoginDB()
    limiter = AuthRateLimiter(
        redis_client=_FakeRedis(RuntimeError("redis unavailable")))
    monkeypatch.setattr(auth, "login_limiter", limiter)

    response = _login_client(db).post(
        "/token", data={"username": "operator", "password": "correct-password"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert db.queries == []


def test_own_password_change_revokes_old_tokens_and_returns_replacement():
    db = _PasswordDB()

    result = users.change_my_password(
        {"old_password": "old-password", "new_password": "new-password"},
        current_admin={"username": "operator"},
        db=db,
    )

    payload = jwt.decode(
        result["access_token"], deps.SECRET_KEY, algorithms=[deps.ALGORITHM])
    assert payload["sv"] == 8
    assert any("session_version = session_version + 1" in sql
               for sql in db.queries)
    assert any("UPDATE user_sessions" in sql for sql in db.queries)
    assert db.commits == 1
