"""Безопасность входа: качество ключа, отзыв JWT и Redis rate limit."""
import hashlib
import os
from dataclasses import dataclass
from typing import Optional


DEFAULT_ACCOUNT_LIMIT = int(os.getenv("AUTH_ACCOUNT_FAILURE_LIMIT", "5"))
DEFAULT_IP_LIMIT = int(os.getenv("AUTH_IP_FAILURE_LIMIT", "20"))
DEFAULT_WINDOW_SECONDS = int(os.getenv("AUTH_FAILURE_WINDOW_SECONDS", "900"))

_SECRET_PLACEHOLDERS = {
    "change-me-super-secret-key",
    "generate_with_install_server",
    "secret",
    "changeme",
}


def validate_secret_key(value: Optional[str]) -> str:
    """Вернуть пригодный ключ JWT или остановить небезопасный запуск."""
    secret = (value or "").strip()
    if len(secret) < 32:
        raise ValueError("SECRET_KEY должен содержать не менее 32 символов")
    if secret.lower() in _SECRET_PLACEHOLDERS:
        raise ValueError("SECRET_KEY содержит шаблонное значение")
    if len(set(secret)) < 12:
        raise ValueError("SECRET_KEY имеет недостаточную случайность")
    if "\r" in secret or "\n" in secret:
        raise ValueError("SECRET_KEY содержит недопустимые переводы строк")
    return secret


def session_version_matches(payload: dict, user: Optional[dict]) -> bool:
    """JWT действует, только пока его поколение совпадает с записью пользователя."""
    if not user:
        return False
    token_version = payload.get("sv")
    stored_version = user.get("session_version")
    if isinstance(token_version, bool) or isinstance(stored_version, bool):
        return False
    return (
        isinstance(token_version, int)
        and isinstance(stored_version, int)
        and token_version > 0
        and token_version == stored_version
    )


class AuthSecurityStoreUnavailable(RuntimeError):
    """Redis не смог подтвердить, что вход не заблокирован."""


class LoginRateLimited(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Слишком много попыток входа")
        self.retry_after = max(1, int(retry_after))


@dataclass(frozen=True)
class LoginFailureDecision:
    limited: bool
    newly_limited: bool
    retry_after: int


class AuthRateLimiter:
    """Два Redis-лимита: на учётную запись и на адрес источника."""

    def __init__(
        self,
        redis_client=None,
        account_limit: int = DEFAULT_ACCOUNT_LIMIT,
        ip_limit: int = DEFAULT_IP_LIMIT,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ):
        self._redis_client = redis_client
        self.account_limit = max(1, int(account_limit))
        self.ip_limit = max(self.account_limit, int(ip_limit))
        self.window_seconds = max(60, int(window_seconds))

    @property
    def redis(self):
        if self._redis_client is None:
            import redis
            self._redis_client = redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://redis:6379"),
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True,
            )
        return self._redis_client

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()

    def keys_for(self, username: str, client_ip: str) -> tuple[str, str]:
        account = self._digest((username or "").strip().casefold())
        address = self._digest((client_ip or "unknown").strip())
        return f"ds:auth:account:{account}", f"ds:auth:ip:{address}"

    @staticmethod
    def _as_int(value, default=0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _read(self, key: str) -> tuple[int, int]:
        try:
            return (
                self._as_int(self.redis.get(key)),
                self._as_int(self.redis.ttl(key), -2),
            )
        except Exception as error:
            raise AuthSecurityStoreUnavailable from error

    def _increment(self, key: str) -> tuple[int, int]:
        try:
            created = self.redis.set(
                key, 1, ex=self.window_seconds, nx=True)
            count = 1 if created else self._as_int(self.redis.incr(key))
            ttl = self._as_int(self.redis.ttl(key), -2)
            if ttl < 1:
                self.redis.expire(key, self.window_seconds)
                ttl = self.window_seconds
            return count, ttl
        except Exception as error:
            raise AuthSecurityStoreUnavailable from error

    def ensure_allowed(self, username: str, client_ip: str) -> None:
        account_key, ip_key = self.keys_for(username, client_ip)
        account_count, account_ttl = self._read(account_key)
        ip_count, ip_ttl = self._read(ip_key)
        if account_count >= self.account_limit or ip_count >= self.ip_limit:
            raise LoginRateLimited(max(account_ttl, ip_ttl, 1))

    def register_failure(
        self, username: str, client_ip: str
    ) -> LoginFailureDecision:
        account_key, ip_key = self.keys_for(username, client_ip)
        account_count, account_ttl = self._increment(account_key)
        ip_count, ip_ttl = self._increment(ip_key)
        limited = (
            account_count >= self.account_limit
            or ip_count >= self.ip_limit
        )
        newly_limited = (
            account_count == self.account_limit
            or ip_count == self.ip_limit
        )
        return LoginFailureDecision(
            limited=limited,
            newly_limited=newly_limited,
            retry_after=max(account_ttl, ip_ttl, 1) if limited else 0,
        )

    def register_success(self, username: str, client_ip: str) -> None:
        account_key, _ip_key = self.keys_for(username, client_ip)
        try:
            self.redis.delete(account_key)
        except Exception as error:
            raise AuthSecurityStoreUnavailable from error

    def healthcheck(self) -> bool:
        try:
            return bool(self.redis.ping())
        except Exception as error:
            raise AuthSecurityStoreUnavailable from error


def client_address(request) -> str:
    """Адрес после доверенного nginx; при прямом вызове — адрес ASGI-клиента."""
    forwarded = (request.headers.get("x-real-ip") or "").strip()
    if forwarded and len(forwarded) <= 64 and "\r" not in forwarded and "\n" not in forwarded:
        return forwarded
    client = getattr(request, "client", None)
    return str(getattr(client, "host", None) or "unknown")[:64]


login_limiter = AuthRateLimiter()
