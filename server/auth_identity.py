"""Единое fail-closed чтение состояния пользователя из PostgreSQL."""
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

KNOWN_PANEL_ROLES = frozenset({
    "superadmin",
    "admin",
    "auditor",
    "moderator",
    "advertiser",
})


class IdentityStoreUnavailable(RuntimeError):
    """PostgreSQL не смог подтвердить пользователя и его текущие права."""


def fetch_user_identity(db: Session, username: str) -> Optional[dict]:
    """Прочитать роль и блокировки одним запросом, без аварийных подстановок."""
    try:
        row = db.execute(text("""
            SELECT username,
                   LOWER(COALESCE(role, '')) AS role,
                   COALESCE(is_active, TRUE) AS is_active,
                   COALESCE(is_blocked, FALSE) AS is_blocked,
                   advertiser_id,
                   COALESCE(session_version, 1) AS session_version
            FROM users
            WHERE username = :username
            LIMIT 1
        """), {"username": username}).mappings().first()
    except Exception as error:
        log.exception("[auth] Не удалось подтвердить пользователя %s", username)
        raise IdentityStoreUnavailable from error
    return dict(row) if row else None


def identity_denial(user: Optional[dict]) -> Optional[tuple[int, str]]:
    """Вернуть HTTP-подобный код и безопасное объяснение отказа."""
    if not user:
        return 401, "Could not validate credentials"
    if not user.get("is_active", True):
        return 403, "User is inactive"
    if user.get("is_blocked", False):
        return 403, "User is blocked"
    if str(user.get("role", "")).lower() not in KNOWN_PANEL_ROLES:
        return 403, "Unknown user role"
    return None
