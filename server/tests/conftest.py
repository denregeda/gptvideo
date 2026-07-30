"""Общая обвязка для регрессионных тестов биллинга и кампаний.

Тесты проверяют ЧИСТУЮ математику (суммы, план/факт, границы дат, округление),
не трогая настоящий Postgres: сессия БД подменяется лёгким моком `FakeDB`,
который возвращает заранее заданные строки. SQL-запросы как таковые здесь не
проверяются — это зона smoke_test.sh против живого стека.

Запуск (внутри контейнера ds_api, где стоят зависимости):
    docker exec -w /app ds_api python -m pytest tests -q
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

# --- Импорт product-кода без побочных эффектов на реальную среду -------------
# Роутеры при импорте создают рабочие каталоги в /data, а deps.py ещё и
# инициализирует create_engine(DATABASE_URL). Все каталоги уводим под единый
# временный корень: тесты работают и в контейнере, и от непривилегированного
# пользователя, не затрагивая рабочие медиа, бэкапы, диагностику и OTA.
_test_data_root = tempfile.mkdtemp(prefix="ds_test_data_")
os.environ["MEDIA_PATH"] = os.path.join(_test_data_root, "media")
os.environ["BACKUP_DIR"] = os.path.join(_test_data_root, "backups")
os.environ["AGENT_UPDATE_PATH"] = os.path.join(
    _test_data_root,
    "agent_updates",
)
# create_engine ленивый (без БД не подключается). SQLite задаёт валидный DSN на
# случай ленивого обращения.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = (
    "d4a7b98c21fe5036d4a7b98c21fe5036"
    "d4a7b98c21fe5036d4a7b98c21fe5036"
)

# Каталог server/ (родитель tests/) — чтобы работали `import deps`, `from routers…`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    """Мок сессии SQLAlchemy: execute() игнорирует SQL и отдаёт заданные строки.

    Последние переданные параметры доступны в last_params — можно проверить,
    что функция сформировала правильные аргументы запроса.
    """

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.last_params = None

    def execute(self, query, params=None):
        self.last_params = params
        return _FakeResult(self._rows)


def db_row(**kwargs):
    """Строка результата с доступом по атрибутам (как Row в SQLAlchemy)."""
    return SimpleNamespace(**kwargs)


@pytest.fixture
def fake_db():
    """Фабрика: fake_db([db_row(...), ...]) → FakeDB с этими строками."""
    def _make(rows=()):
        return FakeDB(rows)
    return _make
