"""
ds_downloader.py — загрузка медиафайлов с сервера с поддержкой resume и MD5-проверки
"""
from __future__ import annotations  # Python 3.7 совместимость: list[dict], tuple[int,int]
import logging
import requests
from pathlib import Path
from ds_media_transfer import download_atomic, md5_file

log = logging.getLogger(__name__)


class FileManifestUnavailable(RuntimeError):
    """Серверный список файлов не получен или не прошёл валидацию."""


class Downloader:
    def __init__(self, server: str, token: str, media_dir: str):
        self.server = server.rstrip("/")
        self.token = token
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["X-Token"] = token

    def _url(self, path):
        return f"{self.server}{path}"

    def get_file_list(self, screen_id: int) -> list[dict]:
        """Получить список файлов, которые должны быть на этом устройстве."""
        try:
            r = self.session.get(self._url(f"/api/files/list/{screen_id}"), timeout=15)
            r.raise_for_status()
            manifest = r.json()
            if not isinstance(manifest, list):
                raise ValueError("ответ должен быть JSON-массивом")
            if any(not isinstance(item, dict) or not item.get("filename")
                   for item in manifest):
                raise ValueError("элемент манифеста не содержит filename")
            return manifest
        except Exception as e:
            log.error(f"Не удалось получить список файлов: {e}")
            raise FileManifestUnavailable(str(e)) from e

    def download_file(self, media_id: int, filename: str, expected_md5: str,
                      expected_size: int) -> bool:
        """Скачать во временный файл и атомарно заменить рабочий после проверки."""
        dest = self.media_dir / filename
        url = self._url(f"/api/files/download/{media_id}")
        return download_atomic(
            self.session, url, dest, expected_md5, expected_size
        )

    def sync_files(self, screen_id: int) -> tuple[int, int]:
        """
        Синхронизировать файлы: скачать нужные, вернуть (downloaded, failed).
        """
        try:
            needed = self.get_file_list(screen_id)
        except FileManifestUnavailable:
            log.warning("Синхронизация пропущена: серверный манифест недоступен")
            return 0, 1
        downloaded = 0
        failed = 0

        for item in needed:
            fname = item["filename"]
            dest = self.media_dir / fname
            if dest.exists():
                if md5_file(str(dest)) == item.get("md5_hash", ""):
                    continue  # Уже есть
            log.info(f"Нужно скачать: {fname}")
            ok = self.download_file(
                item["media_id"], fname,
                item.get("md5_hash", ""), item.get("filesize", 0)
            )
            if ok:
                downloaded += 1
            else:
                failed += 1

        return downloaded, failed

    def cleanup_unused(self, screen_id: int):
        """Удалить файлы, которые больше не нужны для расписания."""
        try:
            needed = {item["filename"] for item in self.get_file_list(screen_id)}
        except FileManifestUnavailable:
            log.warning("Очистка пропущена: серверный манифест недоступен")
            return 0

        removed = 0
        for path in self.media_dir.iterdir():
            # Скрытые файлы (начинающиеся с '.') не трогаем:
            # .schedule_cache.json нужен для офлайн-работы агента.
            if path.is_file() and not path.name.startswith('.') and path.name not in needed:
                log.info(f"Удаляем ненужный файл: {path.name}")
                path.unlink()
                removed += 1
        return removed
