import os
import re
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

from fastapi import Depends, HTTPException, Header, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from auth_identity import (
    IdentityStoreUnavailable,
    fetch_user_identity,
    identity_denial,
)
from auth_security import session_version_matches, validate_secret_key

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@postgres:5432/postgres"),
)
MEDIA_PATH = os.getenv("MEDIA_PATH", "/data/media")
os.makedirs(MEDIA_PATH, exist_ok=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = validate_secret_key(os.getenv("SECRET_KEY"))
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    session_version = to_encode.get("sv", 1)
    if isinstance(session_version, bool) or not isinstance(session_version, int) \
            or session_version < 1:
        raise ValueError("Некорректная версия пользовательской сессии")
    to_encode["sv"] = session_version
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _fetch_user_by_username(db: Session, username: str):
    """Совместимая обёртка: ошибка БД всегда превращается в безопасный 503."""
    try:
        return fetch_user_identity(db, username)
    except IdentityStoreUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
            headers={"Retry-After": "5"},
        )


def get_current_admin(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = _fetch_user_by_username(db, username)
    denial = identity_denial(user)
    if denial:
        code, detail = denial
        if code == 401:
            raise credentials_exception
        raise HTTPException(status_code=code, detail=detail)
    if not session_version_matches(payload, user):
        raise credentials_exception
    return user


# ─── Роль «рекламодатель»: доступ только к своему кабинету ──────────────────
# Скрытие пунктов меню в панели защитой НЕ является: рекламодатель может
# позвать API напрямую. Поэтому доступ режется на сервере в двух местах —
# здесь (совпадение id) и в middleware main.py (белый список путей).

ADVERTISER_ROLE = "advertiser"


def advertiser_scope(aid: int, current_admin: dict = Depends(get_current_admin)) -> int:
    """
    Разрешить работу с карточкой рекламодателя `aid`.

    Для роли advertiser id в пути ИГНОРИРУЕТСЯ как источник истины: работать
    можно только со своим кабинетом, любой другой id — 403, чтобы перебором
    номеров нельзя было прочитать чужой эфир и чужие деньги.
    """
    if str(current_admin.get("role", "")).lower() != ADVERTISER_ROLE:
        return aid
    own = current_admin.get("advertiser_id")
    if not own:
        raise HTTPException(403, "Учётная запись не привязана к рекламодателю")
    if int(aid) != int(own):
        raise HTTPException(403, "Доступ только к своему кабинету")
    return int(own)


# Пути, разрешённые роли advertiser. Всё, чего здесь нет, закрыто (принцип
# «запрещено по умолчанию»): дописывать сюда — осознанное действие.
_ADV_ALLOWED = [
    re.compile(r"^/token/?$"),
    re.compile(r"^/health/?$"),
    re.compile(r"^/me/?$"),
    re.compile(r"^/me/password/?$"),
    re.compile(r"^/session/ping/?$"),
    re.compile(r"^/advertisers/me/?$"),
    re.compile(r"^/advertisers/\d+/"
               r"(overview|airtime|airtime\.xlsx|delivery|creatives|documents|alerts|now-playing|requests)"
               r"(/.*)?$"),
]


def is_path_allowed_for_advertiser(path: str) -> bool:
    return any(rx.match(path) for rx in _ADV_ALLOWED)


def verify_device_token(
    screen_id: int,
    x_token: Optional[str] = Header(default=None, alias="X-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Проверяет, что заголовок X-Token принадлежит именно экрану screen_id
    из пути запроса. Агент шлёт X-Token на все свои запросы (см.
    agent/ds_agent.py, ds_heartbeat.py, ds_downloader.py, ds_sync.py —
    везде `session.headers["X-Token"] = token`), поэтому X-Token — основной
    заголовок; Authorization: Bearer оставлен как запасной вариант.

    ИСПРАВЛЕНО: раньше эта функция проверяла токен по несуществующей таблице
    `devices` (запрос падал в except и ВСЕГДА пропускал любой токен), и её
    вообще никто не подключал к эндпоинтам через Depends(). Реальные токены
    экранов лежат в screens.token (выдаются в /minipc/register).
    """
    token = x_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Device token missing")

    row = db.execute(
        text("SELECT id, name, token FROM screens WHERE id = :sid"),
        {"sid": screen_id},
    ).mappings().first()

    if not row or row["token"] != token:
        raise HTTPException(status_code=401, detail="Invalid device token")

    return row


def verify_any_device_token(
    x_token: Optional[str] = Header(default=None, alias="X-Token"),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Проверяет, что X-Token принадлежит ЛЮБОМУ зарегистрированному экрану.
    Нужна там, где в пути нет screen_id (например, скачивание OTA-файлов
    агента): достаточно, что запрос пришёл от известного устройства сети.
    Агент шлёт X-Token на всех запросах (см. ds_ota_updater.py).
    """
    token = x_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Device token missing")

    row = db.execute(
        text("SELECT id FROM screens WHERE token = :t LIMIT 1"),
        {"t": token},
    ).first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid device token")

    return True


def require_write(current_admin=Depends(get_current_admin)):
    role = str(current_admin.get("role", "")).lower()
    if role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Write access required")
    return current_admin


def require_moderator(current_admin=Depends(get_current_admin)):
    """Право модерировать рекламу (38-ФЗ): модератор и любой пишущий админ."""
    role = str(current_admin.get("role", "")).lower()
    if role not in {"moderator", "admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Требуется право модерации")
    return current_admin


def require_superadmin(current_admin: dict = Depends(get_current_admin)):
    if str(current_admin.get("role", "")).lower() != "superadmin":
        raise HTTPException(status_code=403, detail="Доступно только супер-админу")
    return current_admin


def require_backup_admin(current_admin: dict = Depends(get_current_admin)):
    """Доступ к полному дампу БД — только admin/superadmin.

    Бэкап содержит данные всех рекламодателей, пользователей и кампаний.
    Поэтому обычного права чтения (auditor/moderator) или общего
    ``require_write`` здесь недостаточно: разрешён только явный белый список.
    """
    role = str(current_admin.get("role", "")).lower()
    if role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Доступ к бэкапам только для администратора")
    return current_admin
