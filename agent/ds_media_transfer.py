"""Надёжная загрузка одного медиафайла: resume, проверка и atomic rename."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)


def md5_file(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_verified(path: Path, expected_md5: str, expected_size: int) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected_size
        and md5_file(str(path)) == expected_md5
    )


def _atomic_publish(part: Path, destination: Path):
    os.replace(str(part), str(destination))
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        directory_fd = os.open(str(destination.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass  # rename уже выполнен; fsync каталога поддерживают не все ФС


def _download_attempt(session, url, part, downloaded, filename):
    headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
    if downloaded:
        log.info(f"Возобновляем загрузку {filename} с байта {downloaded}")

    with session.get(url, headers=headers, stream=True, timeout=60) as response:
        if response.status_code not in (200, 206):
            raise OSError(f"сервер вернул HTTP {response.status_code}")
        if response.status_code == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {downloaded}-"):
                raise OSError(
                    f"неверный Content-Range: {content_range or 'отсутствует'}"
                )

        mode = "ab" if downloaded and response.status_code == 206 else "wb"
        if downloaded and response.status_code == 200:
            log.warning(f"{filename}: сервер проигнорировал Range, начинаем заново")

        with open(part, mode) as target:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())


def download_atomic(session, url: str, destination: Path,
                    expected_md5: str, expected_size: int) -> bool:
    """Скачать в скрытый .part и заменить рабочий файл только после проверки."""
    filename = destination.name
    part = destination.parent / f".{filename}.part"

    if _is_verified(destination, expected_md5, expected_size):
        if part.exists():
            part.unlink()
        return True

    for attempt in range(3):
        try:
            downloaded = part.stat().st_size if part.exists() else 0
            if downloaded > expected_size:
                log.warning(f"{filename}: временный файл больше ожидаемого")
                part.unlink()
                downloaded = 0

            if _is_verified(part, expected_md5, expected_size):
                _atomic_publish(part, destination)
                return True
            if downloaded == expected_size:
                log.warning(f"{filename}: временный файл полного размера повреждён")
                part.unlink()
                downloaded = 0

            _download_attempt(session, url, part, downloaded, filename)

            actual_size = part.stat().st_size
            if actual_size != expected_size:
                if actual_size > expected_size:
                    part.unlink()
                raise OSError(
                    f"неполный размер: {actual_size}, ожидалось {expected_size}"
                )

            actual_md5 = md5_file(str(part))
            if actual_md5 != expected_md5:
                part.unlink()
                raise OSError(
                    f"MD5 не совпадает: ожидалось {expected_md5}, получено {actual_md5}"
                )

            _atomic_publish(part, destination)
            log.info(f"Загружен {filename} ({expected_size} байт), MD5 ОК")
            return True
        except Exception as error:
            log.warning(
                f"Попытка {attempt + 1}/3 для {filename} провалилась: {error}"
            )
            if attempt < 2:
                time.sleep(5 * (attempt + 1))

    return False
