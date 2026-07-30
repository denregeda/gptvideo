"""Авторизация: получение JWT-токена и данные текущего пользователя."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth_security import (
    AuthSecurityStoreUnavailable,
    LoginRateLimited,
    client_address,
    login_limiter,
)
from deps import get_db, get_current_admin, create_access_token, pwd_context

router = APIRouter()
_DUMMY_PASSWORD_HASH = pwd_context.hash(
    "invalid-account-timing-equalizer-not-a-real-password")


def _auth_store_unavailable():
    return HTTPException(
        status_code=503,
        detail="Сервис защиты входа временно недоступен",
        headers={"Retry-After": "5"},
    )


def _too_many_attempts(retry_after: int):
    return HTTPException(
        status_code=429,
        detail="Слишком много попыток входа. Повторите позже",
        headers={"Retry-After": str(max(1, retry_after))},
    )


@router.post("/token")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(),
          db: Session = Depends(get_db)):
    """Получить JWT; перебор пароля ограничивается Redis fail-closed."""
    username = (form_data.username or "").strip()
    source_ip = client_address(request)
    try:
        login_limiter.ensure_allowed(username, source_ip)
    except LoginRateLimited as error:
        raise _too_many_attempts(error.retry_after)
    except AuthSecurityStoreUnavailable:
        raise _auth_store_unavailable()

    row = db.execute(
        text("SELECT username, password_hash, "
             "COALESCE(is_blocked, FALSE) AS is_blocked, "
             "COALESCE(is_active, TRUE) AS is_active, "
             "COALESCE(session_version, 1) AS session_version "
             "FROM users WHERE username = :u"),
        {"u": username}
    ).fetchone()
    candidate_hash = row.password_hash if row else _DUMMY_PASSWORD_HASH
    password_ok = bool(
        pwd_context.verify(form_data.password, candidate_hash) and row)
    if not password_ok:
        try:
            decision = login_limiter.register_failure(username, source_ip)
        except AuthSecurityStoreUnavailable:
            raise _auth_store_unavailable()
        if decision.newly_limited:
            db.execute(text("""
                INSERT INTO audit_log (event_type, title, detail, actor)
                VALUES ('security', 'Ограничены попытки входа', :detail, 'auth')
            """), {"detail": f"логин={username or '<пусто>'}; адрес={source_ip}"})
            db.commit()
        if decision.limited:
            raise _too_many_attempts(decision.retry_after)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    try:
        login_limiter.register_success(username, source_ip)
    except AuthSecurityStoreUnavailable:
        raise _auth_store_unavailable()
    if row.is_blocked:
        raise HTTPException(status_code=403, detail="Пользователь заблокирован")
    if not row.is_active:
        raise HTTPException(status_code=403, detail="Пользователь деактивирован")
    db.execute(text("UPDATE users SET last_login = NOW() WHERE username = :u"),
               {"u": username})
    db.commit()
    token = create_access_token(
        {"sub": username, "sv": int(row.session_version or 1)})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def get_me(current_admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """Текущий авторизованный пользователь (для фронтенда — роль, права записи,
    признак обязательной смены пароля по умолчанию)."""
    row = db.execute(
        text("SELECT COALESCE(must_change_password, FALSE) AS mcp "
             "FROM users WHERE username = :u"),
        {"u": current_admin["username"]},
    ).fetchone()
    return {
        "username": current_admin["username"],
        "role": current_admin["role"],
        "must_change_password": bool(row.mcp) if row else False,
    }
