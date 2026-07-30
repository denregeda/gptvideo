"""Потоковая отдача файлов с поддержкой одного HTTP Range."""
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse


def _parse_range(value: str, size: int):
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value or size <= 0:
        raise ValueError("invalid range")

    start_text, separator, end_text = value[6:].partition("-")
    if not separator:
        raise ValueError("invalid range")

    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid suffix")
        return max(0, size - suffix), size - 1

    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("range outside file")
    return start, min(end, size - 1)


def _file_chunks(path: Path, start: int, length: int):
    with open(path, "rb") as source:
        source.seek(start)
        remaining = length
        while remaining:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def ranged_file(path: str, request: Request, filename: str):
    """Вернуть полный файл или HTTP 206 для одного диапазона байтов."""
    file_path = Path(path)
    size = file_path.stat().st_size
    headers = {"Accept-Ranges": "bytes"}

    try:
        byte_range = _parse_range(request.headers.get("range", ""), size)
    except (TypeError, ValueError):
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", **headers},
        )

    if byte_range is None:
        return FileResponse(
            file_path,
            media_type="application/octet-stream",
            filename=filename,
            headers=headers,
        )

    start, end = byte_range
    length = end - start + 1
    headers.update({
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(length),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    })
    return StreamingResponse(
        _file_chunks(file_path, start, length),
        status_code=206,
        media_type="application/octet-stream",
        headers=headers,
    )
